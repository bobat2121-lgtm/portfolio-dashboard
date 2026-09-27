"""Rebuild the portfolio day by day from today's holdings and the transaction history, and replay the
same money into Bitcoin and the S&P 500 for comparison.

How: start from what each account holds today and walk its transactions backwards (a buy is undone by
removing the shares and giving the cash back), which gives holdings and cash for every day since the
account's first transaction. Each day is valued with Yahoo closes (split-adjusted; old quantities are
adjusted with the stored splits, which brokers don't always report), falling back to the account's own
trade prices for options and delisted tickers. Money in = deposits minus withdrawals, coins/shares moved
in or out at market value, and each account's opening balance on its first day of history.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from portfolio import models as m
from portfolio.lenses import option_label
from portfolio.pricehist import BENCHMARKS, cash_like, crypto_accounts, yahoo_for

QTY_TYPES = {m.BUY, m.SELL, m.REINVEST, m.STOCK_DIVIDEND, m.TRANSFER_IN, m.TRANSFER_OUT, m.TRANSFER, m.SPLIT,
             m.OPTION_EVENT, m.REWARD}
FLOW_TYPES = {m.DEPOSIT, m.WITHDRAWAL}
MOVE_TYPES = {m.TRANSFER_IN, m.TRANSFER_OUT, m.TRANSFER}


@dataclass
class History:
    daily: pd.DataFrame            # index: date; value, flow, net_in, btc, spy
    start: date | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.daily.empty


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def split_factors(splits: pd.DataFrame) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list] = defaultdict(list)
    for r in splits.itertuples():
        out[r.symbol].append((pd.Timestamp(r.date).date(), float(r.ratio)))
    return out


def factor_after(events: list[tuple[date, float]], d: date) -> float:
    """Shares held on day d become this many shares today (product of later split ratios)."""
    f = 1.0
    for sd, ratio in events:
        if sd > d and ratio > 0:
            f *= ratio
    return f


def build(txns: pd.DataFrame, positions: pd.DataFrame, cash: pd.DataFrame, accounts: pd.DataFrame,
          prices: pd.DataFrame, splits: pd.DataFrame, today: date) -> History:
    notes: list[str] = []
    if accounts.empty:
        return History(pd.DataFrame())
    crypto = crypto_accounts(accounts)
    spl = split_factors(splits)
    txns = txns.copy()
    if not txns.empty:
        txns["trade_date"] = pd.to_datetime(txns["trade_date"]).dt.date
        txns = txns[txns["trade_date"].notna() & (txns["trade_date"] <= today)]
    starts = [txns["trade_date"].min()] if not txns.empty else []
    start = min(starts) if starts else today
    days = pd.date_range(start - timedelta(days=1), today, freq="D").date
    idx = pd.Index(days, name="date")

    # Yahoo closes, one column per symbol, carried over weekends/holidays
    closes: dict[str, pd.Series] = {}
    if not prices.empty:
        p = prices.copy()
        p["date"] = pd.to_datetime(p["date"]).dt.date
        for sym, g in p.groupby("symbol"):
            closes[sym] = g.set_index("date")["close"].sort_index()
            if sym == "SPY" and g["adj_close"].notna().any():
                closes["SPY:tr"] = g.set_index("date")["adj_close"].sort_index()

    def series(values: pd.Series) -> pd.Series:
        s = values[~values.index.duplicated(keep="last")].reindex(sorted(set(values.index) | set(days)))
        return s.ffill().bfill().reindex(idx)

    total_value = pd.Series(0.0, index=idx)
    total_flow = pd.Series(0.0, index=idx)
    negative: set[str] = set()
    pre_held: set[str] = set()

    for acct in accounts.itertuples():
        key = acct.key
        is_crypto = key in crypto
        t = txns[txns["account_key"] == key] if not txns.empty else txns
        pos = positions[positions["account_key"] == key] if not positions.empty else positions
        now_qty: dict[str, float] = {}
        mult: dict[str, float] = {}
        now_px: dict[str, float] = {}
        cash_now = float(cash.loc[(cash["account_key"] == key) & (cash["currency"] == "USD"), "amount"].sum()) if not cash.empty else 0.0
        for p in pos.itertuples():
            if bool(p.in_cash_balance):
                continue
            if p.asset_class == m.CASH or cash_like(p.symbol):
                cash_now += float(p.market_value or 0.0)
                continue
            now_qty[p.symbol] = now_qty.get(p.symbol, 0.0) + float(p.quantity)
            mult[p.symbol] = float(p.multiplier or 1.0)
            if p.price is not None and not pd.isna(p.price):
                now_px[p.symbol] = float(p.price)
        acct_start = t["trade_date"].min() if not t.empty else today

        qty_delta: dict[str, pd.Series] = {}
        trade_px: dict[str, list] = defaultdict(list)
        cash_delta = pd.Series(0.0, index=idx)
        flow = pd.Series(0.0, index=idx)
        moves = []                                    # (date, symbol, adjusted qty) moved in/out
        outs: dict[str, list] = defaultdict(list)     # symbol -> [(date, qty < 0, amount, type)] leaving
        for r in t.itertuples():
            sym = r.symbol or ""
            ysym = yahoo_for(sym, is_crypto) if sym else None
            f = factor_after(spl.get(ysym, []), r.trade_date) if ysym else 1.0
            if sym and cash_like(sym) and r.type in (m.BUY, m.SELL, m.REINVEST):
                continue                              # sweep-fund moves are cash to cash
            if r.amount is not None and not pd.isna(r.amount) and r.type != m.INTERNAL:
                cash_delta[r.trade_date] += float(r.amount)
            if r.type in FLOW_TYPES and r.amount is not None and not pd.isna(r.amount):
                flow[r.trade_date] += float(r.amount)
            if sym and not cash_like(sym) and r.type in QTY_TYPES and r.quantity is not None and not pd.isna(r.quantity):
                q = float(r.quantity) * f
                qty_delta.setdefault(sym, pd.Series(0.0, index=idx))
                qty_delta[sym][r.trade_date] += q
                if r.type in MOVE_TYPES:
                    moves.append((r.trade_date, sym, q))
                if q < 0:
                    outs[sym].append((r.trade_date, q, _num(r.amount), r.type))
            if sym and r.price is not None and not pd.isna(r.price) and float(r.price) > 0:
                trade_px[sym].append((r.trade_date, float(r.price) / f))
            if option_label(sym)[0]:
                mult.setdefault(sym, 100.0)

        value = pd.Series(0.0, index=idx)
        px_cache: dict[str, pd.Series] = {}
        pre_moved: dict[tuple, float] = defaultdict(float)
        for sym in set(now_qty) | set(qty_delta):
            d = qty_delta.get(sym, pd.Series(0.0, index=idx))
            hold = now_qty.get(sym, 0.0) - (d.sum() - d.cumsum())
            if (hold < -1e-6).any():
                negative.add(sym)
            # Shares/coins held before this account's history aren't tracked: they're not money in, and
            # not value. They leave first (first in, first out); when sold, the cash they bring in is money in.
            pre = max(0.0, now_qty.get(sym, 0.0) - float(d.sum()))
            untracked = pd.Series(pre, index=idx)
            if pre > 1e-9:
                pre_held.add(sym)
                left = pre
                for dd, q, amount, typ in sorted(outs.get(sym, []), key=lambda x: x[0]):
                    take = min(left, -q)
                    if take <= 1e-12:
                        break
                    if typ in (m.SELL, m.OPTION_EVENT) and amount is not None:
                        flow[dd] += abs(amount) * take / -q
                    elif typ in MOVE_TYPES:
                        pre_moved[(dd, sym)] += take
                    left -= take
                    untracked[untracked.index >= dd] = left
            hold = hold - untracked
            ysym = yahoo_for(sym, is_crypto)
            if ysym in closes:
                px = series(closes[ysym])
            elif trade_px.get(sym):
                tp = pd.Series({dd: pp for dd, pp in trade_px[sym]})
                px = series(tp.groupby(level=0).last())
            elif sym in now_px:
                px = pd.Series(now_px[sym], index=idx)
            else:
                px = pd.Series(0.0, index=idx)
                notes.append(f"No price history for {sym}; it counts as $0 before today.")
            if sym in now_px:
                px = px.copy()
                px.iloc[-1] = now_px[sym]
            px_cache[sym] = px
            value = value.add(hold.clip(lower=0) * px * mult.get(sym, 1.0), fill_value=0.0)
        cash_series = cash_now - (cash_delta.sum() - cash_delta.cumsum())
        value = value + cash_series

        for d, sym, q in moves:                        # coins/shares moved in or out count as money in/out
            q = q + pre_moved.pop((d, sym), 0.0) if q < 0 else q     # untracked ones leaving don't count
            flow[d] += q * float(px_cache[sym][d]) * mult.get(sym, 1.0)
        live = pd.Series(np.array(days) >= acct_start, index=idx)
        opening = float(value[acct_start - timedelta(days=1)]) if (acct_start - timedelta(days=1)) in value.index else 0.0
        flow[acct_start] += opening
        total_value = total_value.add(value.where(live, 0.0), fill_value=0.0)
        total_flow = total_flow.add(flow.where(live, 0.0), fill_value=0.0)

    if pre_held:
        notes.append("Shares and coins held before SnapTrade's history (" + ", ".join(sorted(pre_held)) + ") aren't "
                     "counted; when they were sold, the cash they brought in counts as money in that day.")
    if negative:
        notes.append("Some history before the first transaction SnapTrade has is incomplete ("
                     + ", ".join(sorted(negative)) + "); those days are approximate.")
    daily = pd.DataFrame({"value": total_value, "flow": total_flow}, index=idx).iloc[1:]
    daily["net_in"] = daily["flow"].cumsum()
    for col, sym in (("btc", "BTC-USD"), ("spy", "SPY:tr" if "SPY:tr" in closes else "SPY")):
        if sym in closes:
            px = series(closes[sym]).iloc[1:]
            daily[f"{col}_px"] = px
            daily[col] = (daily["flow"] / px).cumsum() * px
        else:
            daily[col] = np.nan
            notes.append(f"No {BENCHMARKS.get(sym.split(':')[0], sym)} prices yet; they fill in after the next sync.")
    return History(daily=daily, start=start, notes=notes)


def rebase(h: History, start: date | None) -> History:
    """Start the timeline at `start`: what the portfolio was worth the day before counts as money in on
    that day, and the BTC / S&P comparisons are replayed from there."""
    d = h.daily
    if h.empty or start is None or start <= d.index[0] or start > d.index[-1]:
        return h
    before = d[d.index < start]
    opening = float(before["value"].iloc[-1]) if not before.empty else 0.0
    nd = d[d.index >= start].copy()
    nd.loc[nd.index[0], "flow"] += opening
    nd["net_in"] = nd["flow"].cumsum()
    for col in ("btc", "spy"):
        if f"{col}_px" in nd and nd[f"{col}_px"].notna().all():
            nd[col] = (nd["flow"] / nd[f"{col}_px"]).cumsum() * nd[f"{col}_px"]
    return History(daily=nd, start=start, notes=h.notes)


def summary(h: History) -> dict:
    """Headline numbers for the comparison cards."""
    if h.empty:
        return {}
    last = h.daily.iloc[-1]
    return {"value": float(last["value"]), "net_in": float(last["net_in"]), "gain": float(last["value"] - last["net_in"]),
            "btc": float(last["btc"]) if pd.notna(last["btc"]) else None,
            "spy": float(last["spy"]) if pd.notna(last["spy"]) else None, "since": h.start}
