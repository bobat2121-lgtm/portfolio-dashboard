"""Tax lots and realized gains, first-in-first-out (Robinhood's and Fidelity's default), from the
transaction history. An estimate to plan with; your broker's 1099 is the record.

Splits are applied to old lots (same total cost, today's share count). Sales of shares bought before the
history SnapTrade has show up with an unknown cost. A loss sale with a buy of the same symbol within 30
days either side, in any account including the IRA, is flagged as a possible wash sale.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from portfolio import models as m
from portfolio.config import section
from portfolio.history import factor_after, split_factors
from portfolio.lenses import option_label
from portfolio.pricehist import cash_like, crypto_accounts, yahoo_for

LONG_TERM_DAYS = 365
DEFAULT_RATES = {"short_term_rate": 0.24, "long_term_rate": 0.15, "state_rate": 0.0}


def rates() -> dict:
    return {**DEFAULT_RATES, **(section("taxes") or {})}


@dataclass
class TaxReport:
    realized: pd.DataFrame
    lots: pd.DataFrame
    income: pd.DataFrame
    notes: list[str] = field(default_factory=list)


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def build(txns: pd.DataFrame, positions: pd.DataFrame, accounts: pd.DataFrame, splits: pd.DataFrame,
          today: date) -> TaxReport:
    notes: list[str] = []
    crypto = crypto_accounts(accounts)
    spl = split_factors(splits)
    tax_of = dict(zip(accounts["key"], accounts["tax"])) if not accounts.empty else {}
    label_of = dict(zip(accounts["key"], accounts["label"])) if not accounts.empty else {}
    lots: dict[tuple, deque] = defaultdict(deque)          # (account, symbol) -> [{acquired, qty, cost}]
    realized, buys = [], []
    t = txns.copy()
    if not t.empty:
        t["trade_date"] = pd.to_datetime(t["trade_date"]).dt.date
        t = t.sort_values(["trade_date", "id"])
    for r in t.itertuples():
        sym = r.symbol or ""
        if not sym or cash_like(sym) or r.quantity is None or pd.isna(r.quantity):
            continue
        ysym = yahoo_for(sym, r.account_key in crypto)
        q = float(r.quantity) * (factor_after(spl.get(ysym, []), r.trade_date) if ysym else 1.0)
        key = (r.account_key, sym)
        amount, fee, value = _num(r.amount), _num(r.fee) or 0.0, _num(r.value_usd)
        if q > 0 and r.type in (m.BUY, m.REINVEST):
            cost = abs(amount) if amount is not None else (value or 0.0) + fee
            lots[key].append({"acquired": r.trade_date, "qty": q, "cost": cost})
            buys.append((sym, r.trade_date, r.account_key))
        elif q > 0 and r.type in (m.STOCK_DIVIDEND, m.REWARD):
            lots[key].append({"acquired": r.trade_date, "qty": q, "cost": 0.0})
        elif q > 0 and r.type in (m.TRANSFER_IN, m.TRANSFER):
            lots[key].append({"acquired": r.trade_date, "qty": q, "cost": float("nan")})
        elif q < 0:
            realize = r.type in (m.SELL, m.OPTION_EVENT)
            proceeds = (abs(amount) if amount is not None else max(0.0, (value or 0.0) - fee)) if realize else 0.0
            left, total = -q, -q
            while left > 1e-9:
                lot = lots[key][0] if lots[key] else None
                take = min(left, lot["qty"]) if lot else left
                if realize:
                    cost = lot["cost"] * take / lot["qty"] if lot else float("nan")
                    part = proceeds * take / total
                    realized.append({"account_key": r.account_key, "symbol": sym, "qty": take,
                                     "acquired": lot["acquired"] if lot else None, "sold": r.trade_date,
                                     "proceeds": part, "cost": cost, "gain": part - cost})
                if not lot:
                    break
                lot["cost"] -= lot["cost"] * take / lot["qty"]
                lot["qty"] -= take
                if lot["qty"] <= 1e-9:
                    lots[key].popleft()
                left -= take

    R = pd.DataFrame(realized, columns=["account_key", "symbol", "qty", "acquired", "sold", "proceeds", "cost", "gain"])
    if not R.empty:
        R["days"] = [(s - a).days if a else None for a, s in zip(R["acquired"], R["sold"])]
        R["term"] = ["unknown" if d is None else "long" if d > LONG_TERM_DAYS else "short" for d in R["days"]]
        R["basis_known"] = R["cost"].notna()
        R["wash_sale"] = [
            bool(g < 0) and any(bs == sym and abs((bd - sold).days) <= 30 and not (ba == acct and bd == acq)
                                for bs, bd, ba in buys)
            for sym, sold, acq, acct, g in zip(R["symbol"], R["sold"], R["acquired"], R["account_key"], R["gain"])]
        R["year"] = [s.year for s in R["sold"]]

    price_of, held = {}, defaultdict(float)
    if not positions.empty:
        for p in positions.itertuples():
            if p.price is not None and not pd.isna(p.price):
                price_of[(p.account_key, p.symbol)] = (float(p.price), float(p.multiplier or 1.0))
            if not (bool(p.in_cash_balance) or p.asset_class == m.CASH or cash_like(p.symbol)):
                held[(p.account_key, p.symbol)] += float(p.quantity)
    for key, q in lots.items():               # shares that left without a reported sale: drop the oldest lots
        excess = sum(lot["qty"] for lot in q) - held.get(key, 0.0)
        if excess <= max(1e-6, held.get(key, 0.0) * 0.001):
            continue
        px = price_of.get(key, (0.0, 1.0))
        if excess * px[0] * px[1] >= 1 or key not in price_of:
            notes.append(f"{label_of.get(key[0], key[0])} {option_label(key[1])[1]}: {excess:g} shares left the account "
                         "without a sale SnapTrade reported (sold before its history, or moved), so they're not shown as open lots.")
        while excess > 1e-9 and q:
            take = min(excess, q[0]["qty"])
            q[0]["cost"] -= q[0]["cost"] * take / q[0]["qty"]
            q[0]["qty"] -= take
            excess -= take
            if q[0]["qty"] <= 1e-9:
                q.popleft()
    open_rows = []
    for (acct, sym), q in lots.items():
        for lot in q:
            if lot["qty"] <= 1e-9:
                continue
            px, mult = price_of.get((acct, sym), (None, 100.0 if option_label(sym)[0] else 1.0))
            value = lot["qty"] * px * mult if px is not None else None
            held = (today - lot["acquired"]).days
            open_rows.append({"account_key": acct, "symbol": sym, "acquired": lot["acquired"], "qty": lot["qty"],
                              "cost": lot["cost"], "value": value,
                              "unrealized": None if value is None or pd.isna(lot["cost"]) else value - lot["cost"],
                              "days": held, "term": "long" if held > LONG_TERM_DAYS else "short",
                              "long_term_on": lot["acquired"] + timedelta(days=LONG_TERM_DAYS + 1)})
    L = pd.DataFrame(open_rows, columns=["account_key", "symbol", "acquired", "qty", "cost", "value", "unrealized",
                                         "days", "term", "long_term_on"])

    # lots should add up to what the broker says you hold
    if not positions.empty:
        for p in positions.itertuples():
            if bool(p.in_cash_balance) or p.asset_class == m.CASH or cash_like(p.symbol):
                continue
            have = L.loc[(L["account_key"] == p.account_key) & (L["symbol"] == p.symbol), "qty"].sum()
            if abs(have - float(p.quantity)) > max(1e-6, abs(float(p.quantity)) * 0.001):
                gap = float(p.quantity) - have
                gap_value = gap * float(p.price or 0) * float(p.multiplier or 1)
                if gap_value < 1:
                    continue                  # dust
                L = pd.concat([L, pd.DataFrame([{
                    "account_key": p.account_key, "symbol": p.symbol, "acquired": None, "qty": gap, "cost": float("nan"),
                    "value": gap_value, "unrealized": None, "days": None,
                    "term": "unknown", "long_term_on": None}])], ignore_index=True)
                notes.append(f"{label_of.get(p.account_key, p.account_key)} {p.symbol}: {gap:g} shares predate the "
                             "history SnapTrade has, so their cost and dates are unknown.")

    for frame in (R, L):
        if not frame.empty:
            frame["account"] = frame["account_key"].map(label_of)
            frame["tax"] = frame["account_key"].map(tax_of).fillna("unknown")
            frame["asset"] = frame["symbol"].map(lambda s: option_label(s)[1])

    inc = t[t["type"].isin([m.DIVIDEND, m.INTEREST])] if not t.empty else t
    income = pd.DataFrame(columns=["account", "tax", "year", "type", "amount"])
    if not inc.empty:
        inc = inc.assign(year=[d.year for d in inc["trade_date"]], account=inc["account_key"].map(label_of),
                         tax=inc["account_key"].map(tax_of).fillna("unknown"))
        income = inc.groupby(["account", "tax", "year", "type"], as_index=False)["amount"].sum()
    return TaxReport(realized=R, lots=L, income=income, notes=notes)


def year_summary(rep: TaxReport, year: int) -> dict:
    """Taxable accounts only: an IRA's gains aren't taxed when they happen."""
    R = rep.realized
    R = R[(R["year"] == year) & (R["tax"] == "taxable")] if not R.empty else R
    st = float(R.loc[R["term"] == "short", "gain"].sum()) if not R.empty else 0.0
    lt = float(R.loc[R["term"] == "long", "gain"].sum()) if not R.empty else 0.0
    unknown = float(R.loc[~R["basis_known"], "proceeds"].sum()) if not R.empty else 0.0
    inc = rep.income
    income = float(inc.loc[(inc["year"] == year) & (inc["tax"] == "taxable"), "amount"].sum()) if not inc.empty else 0.0
    rt = rates()
    est = max(0.0, st + income) * (rt["short_term_rate"] + rt["state_rate"]) + max(0.0, lt) * (rt["long_term_rate"] + rt["state_rate"])
    if st + lt < 0:  # net losses offset up to $3,000 of ordinary income
        est = max(0.0, est - min(3000.0, -(st + lt)) * rt["short_term_rate"])
    wash = int(R["wash_sale"].sum()) if not R.empty else 0
    return {"short": st, "long": lt, "income": income, "estimate": est, "unknown_proceeds": unknown, "wash": wash,
            "rates": rt}
