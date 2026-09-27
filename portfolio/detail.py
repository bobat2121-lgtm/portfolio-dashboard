"""One holding in depth, for the drawer that opens under its card: the position, its purchase tranches,
your trades on its price chart, what it means for taxes, and for options the contract itself.

Only what happened after the portfolio's start counts (config: performance.start, Jun 30 2025); anything
older is left out. Pure pandas; panel/drawer.py draws it.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from portfolio import history, lenses, models as m

WASH_DAYS = 30  # a sale at a loss within 30 days of a buy (any account, the IRA too) is a wash sale


def multiplier(r) -> float:
    return 100.0 if r.asset_class == m.OPTION else 1.0


def _after(d, since: date) -> bool:
    return d is not None and not pd.isna(d) and d > since


def symbols(r, lots: pd.DataFrame | None) -> set[str]:
    """Every raw symbol behind a combined holding (its own, plus whatever its lots were bought as)."""
    out = {str(r.symbol)}
    if lots is not None and not lots.empty:
        out |= set(lots.loc[lots["asset"] == r.asset, "symbol"])
    return out


def tranches(lots: pd.DataFrame | None, r, since: date) -> pd.DataFrame:
    """This holding's open lots bought after `since`, oldest first, with the price paid per share."""
    cols = ["acquired", "account", "tax", "qty", "cost", "value", "unrealized", "days", "term", "long_term_on", "paid",
            "gain_pct"]
    if lots is None or lots.empty:
        return pd.DataFrame(columns=cols)
    t = lots[(lots["asset"] == r.asset) & lots["acquired"].map(lambda d: _after(d, since))].copy()
    if t.empty:
        return pd.DataFrame(columns=cols)
    t["paid"] = t["cost"] / (t["qty"] * multiplier(r))
    t["gain_pct"] = t["unrealized"] / t["cost"].where(t["cost"] > 0)
    return t.sort_values("acquired").reset_index(drop=True)[cols]


def trades(txns: pd.DataFrame, syms: set[str], since: date, splits: pd.DataFrame | None, mult: float = 1.0) -> pd.DataFrame:
    """Buys and sells after `since`, in today's shares (split-adjusted, so they sit on the price chart)."""
    cols = ["date", "side", "qty", "price", "amount", "account"]
    if txns is None or txns.empty:
        return pd.DataFrame(columns=cols)
    t = txns[txns["symbol"].isin(syms) & txns["type"].isin([m.BUY, m.SELL])
             & txns["trade_date"].map(lambda d: _after(d, since))]
    events = history.split_factors(splits) if splits is not None and not splits.empty else {}
    rows = []
    for x in t.itertuples():
        f = history.factor_after(events.get(x.symbol, []), x.trade_date)
        qty = abs(float(x.quantity or 0)) * f
        amount = abs(float(x.value_usd)) if pd.notna(x.value_usd) else abs(float(x.amount or 0))
        price = float(x.price) / f if pd.notna(x.price) and x.price else (amount / (qty * mult) if qty else None)
        rows.append({"date": x.trade_date, "side": x.type, "qty": qty, "price": price, "amount": amount,
                     "account": x.account})
    return pd.DataFrame(rows, columns=cols)


def by_day(tr: pd.DataFrame) -> pd.DataFrame:
    """One marker per day and side: shares, dollars, and the average price that day."""
    if tr.empty:
        return tr.assign(n=[])
    g = tr.groupby(["date", "side"], as_index=False).agg(qty=("qty", "sum"), amount=("amount", "sum"),
                                                          n=("qty", "size"), pq=("price", lambda s: s.mean()))
    g["price"] = [(a / q) if q and a else p for a, q, p in zip(g["amount"], g["qty"], g["pq"])]
    return g.drop(columns=["pq"])


def summary(r, lots: pd.DataFrame, realized: pd.DataFrame | None, txns: pd.DataFrame | None, syms: set[str],
            since: date, today: date) -> dict:
    """The position at a glance. Break-even counts realized gains and income on this holding since `since`:
    the price where selling everything leaves you even on it overall."""
    mult, qty = multiplier(r), float(r.quantity or 0)
    cost = None if pd.isna(r.cost_basis) else float(r.cost_basis)
    real = 0.0
    if realized is not None and not realized.empty:
        rz = realized[(realized["asset"] == r.asset) & realized["sold"].map(lambda d: _after(d, since))]
        real = float(rz["gain"].fillna(0).sum())
    income = 0.0
    if txns is not None and not txns.empty:
        inc = txns[txns["symbol"].isin(syms) & txns["type"].isin([m.DIVIDEND, m.INTEREST])
                   & txns["trade_date"].map(lambda d: _after(d, since))]
        income = float(inc["amount"].fillna(0).sum())
    units = qty * mult
    first = min(lots["acquired"]) if not lots.empty else None
    unreal = None if cost is None else float(r.market_value) - cost
    return {
        "shares": qty, "mult": mult, "paid": cost, "value": float(r.market_value), "unrealized": unreal,
        "avg_cost": cost / units if cost is not None and units else None,
        "breakeven": (cost - real - income) / units if cost is not None and units else None,
        "realized": real, "income": income, "total": (unreal or 0.0) + real + income,
        "first": first, "held_days": (today - first).days if first else None,
    }


def taxes(lots: pd.DataFrame, tr: pd.DataFrame, rates: dict, today: date) -> dict:
    """Unrealized gain by term (taxable accounts only), a rough bill if you sold it all today, the next
    lot to turn long-term, losses you could harvest, and whether a sale at a loss would be a wash sale."""
    ira = lots[lots["tax"].eq("ira")] if not lots.empty else lots
    taxable = lots[~lots["tax"].eq("ira")] if not lots.empty else lots
    short = float(taxable.loc[taxable["term"].eq("short"), "unrealized"].fillna(0).sum()) if not taxable.empty else 0.0
    long_ = float(taxable.loc[taxable["term"].eq("long"), "unrealized"].fillna(0).sum()) if not taxable.empty else 0.0
    s, lg = short, long_
    if s < 0 < lg:                           # losses of one term offset gains of the other
        s, lg = 0.0, max(0.0, lg + s)
    elif lg < 0 < s:
        s, lg = max(0.0, s + lg), 0.0
    state = rates.get("state_rate", 0.0)
    bill = max(0.0, s) * (rates.get("short_term_rate", 0.0) + state) + max(0.0, lg) * (rates.get("long_term_rate", 0.0) + state)
    nxt = None
    if not taxable.empty:
        soon = taxable[taxable["term"].eq("short") & taxable["long_term_on"].map(lambda d: d is not None and not pd.isna(d) and d > today)]
        if not soon.empty:
            n = soon.sort_values("long_term_on").iloc[0]
            nxt = {"on": n["long_term_on"], "days": (n["long_term_on"] - today).days, "qty": float(n["qty"]),
                   "gain": float(n["unrealized"]) if pd.notna(n["unrealized"]) else None}
    losses = taxable[taxable["unrealized"] < 0] if not taxable.empty else taxable
    buys = tr[tr["side"].eq(m.BUY)] if not tr.empty else tr
    last_buy = max(buys["date"]) if not buys.empty else None
    # a loss sale only washes against a *different* purchase, so it takes other shares than the latest buy
    others = not lots.empty and bool((lots["acquired"] != last_buy).any()) if last_buy else False
    wash_until = (last_buy + timedelta(days=WASH_DAYS)
                  if last_buy and others and last_buy + timedelta(days=WASH_DAYS) >= today else None)
    return {"short": short, "long": long_, "bill": bill, "rates": rates, "next": nxt,
            "loss_lots": len(losses), "loss": float(losses["unrealized"].sum()) if not losses.empty else 0.0,
            "last_buy": last_buy, "wash_until": wash_until,
            "ira_qty": float(ira["qty"].sum()) if not ira.empty else 0.0,
            "ira_value": float(ira["value"].fillna(0).sum()) if not ira.empty else 0.0}


def option(r, underlying_price: float | None, today: date) -> dict | None:
    """The contract: strike, expiry, what you paid, where it breaks even at expiry and how far the
    underlying has to move to get there, and how much of its price is intrinsic value vs time value."""
    mt = lenses.OCC.match(str(r.symbol).replace(" ", "").upper())
    if not mt:
        return None
    und, yy, mm, dd, cp, strike = mt.groups()
    k, call = int(strike) / 1000, cp == "C"
    expiry = date(2000 + int(yy), int(mm), int(dd))
    contracts = float(r.quantity or 0)
    premium = float(r.cost_basis) / (contracts * 100) if contracts and pd.notna(r.cost_basis) else None
    price = float(r.price) if r.price is not None and pd.notna(r.price) else None
    s = underlying_price
    be = None if premium is None else (k + premium if call else k - premium)
    intrinsic = None if s is None else (max(0.0, s - k) if call else max(0.0, k - s))
    return {
        "underlying": und, "call": call, "strike": k, "expiry": expiry, "days": (expiry - today).days,
        "contracts": contracts, "shares": contracts * 100, "premium": premium, "paid": premium * contracts * 100 if premium else None,
        "price": price, "breakeven": be, "spot": s,
        "move": (be / s - 1) if be and s else None,                       # to break even at expiry
        "money": ((s / k - 1) if call else (1 - s / k)) if s and k else None,   # > 0: in the money by this much
        "intrinsic": intrinsic, "time_value": (price - intrinsic) if price is not None and intrinsic is not None else None,
    }


def cash_by_account(h: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """Where the Cash card's dollars sit: cash balances plus money-market funds, per account."""
    rows = []
    if h is not None and not h.empty:
        mm_ = h[(h["asset_class"] == m.CASH) & ~h["in_cash_balance"].astype(bool)]
        rows += [{"account": x.account, "what": x.symbol, "amount": float(x.market_value or 0)} for x in mm_.itertuples()]
    if c is not None and not c.empty:
        rows += [{"account": x.account, "what": "cash", "amount": float(x.amount)} for x in c[c["currency"] == "USD"].itertuples()
                 if x.amount]
    df = pd.DataFrame(rows, columns=["account", "what", "amount"])
    return df.groupby("account", as_index=False)["amount"].sum().sort_values("amount", ascending=False) if not df.empty else df
