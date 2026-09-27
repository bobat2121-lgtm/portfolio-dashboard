"""Badges earned from your actual history. Each has a two-character insignia, a rule, and progress
toward it while it's still locked."""
from __future__ import annotations

from datetime import date

import pandas as pd

from portfolio import models as m
from portfolio.lenses import CASH_ASSET, bitcoin_share, option_label
from portfolio.pricehist import yahoo_for


def _badge(code, name, desc, progress, detail="", earned=None):
    p = max(0.0, min(1.0, float(progress or 0.0)))
    return {"code": code, "name": name, "desc": desc, "progress": p, "earned": p >= 1.0 if earned is None else earned,
            "detail": detail}


def evaluate(assets: pd.DataFrame, totals: dict, hist: dict, txns: pd.DataFrame, lots: pd.DataFrame,
             accounts: pd.DataFrame, prices: pd.DataFrame, today: date) -> list[dict]:
    held = assets[(assets["asset"] != CASH_ASSET) & (assets["market_value"] >= 1)] if not assets.empty else assets
    t = txns.copy()
    if not t.empty:
        t["trade_date"] = pd.to_datetime(t["trade_date"]).dt.date
    out = []

    best = held["unrealized_pct"].max() if not held.empty and held["unrealized_pct"].notna().any() else 0.0
    best_name = held.loc[held["unrealized_pct"].idxmax(), "asset"] if best else ""
    out.append(_badge("2X", "Doubled", "A holding up 100% or more", best / 1.0, f"{best_name} {best:+.0%}" if best else ""))
    out.append(_badge("10", "Ten-bagger", "A holding up 900% or more", best / 9.0, f"best so far {best:+.0%}" if best else ""))

    deepest, deep_name = 0.0, ""
    for r in held.itertuples():                      # fell 30%+ below what you paid, and you kept it
        if r.asset_class in (m.OPTION, m.CASH, m.STABLECOIN) or pd.isna(r.cost_basis) or not r.quantity:
            continue
        ysym = yahoo_for(r.symbol, r.asset_class == m.CRYPTO)
        buys = t[(t["symbol"] == r.symbol) & (t["type"] == m.BUY)] if not t.empty else t
        if not ysym or buys.empty or prices.empty:
            continue
        px = prices[(prices["symbol"] == ysym) & (pd.to_datetime(prices["date"]).dt.date >= buys["trade_date"].min())]
        if px.empty:
            continue
        dd = 1 - px["close"].min() / (r.cost_basis / r.quantity)
        if dd > deepest:
            deepest, deep_name = dd, r.asset
    out.append(_badge("DH", "Diamond hands", "Held on through a 30%+ drop below what you paid", deepest / 0.3,
                      f"{deep_name} fell {deepest:.0%} below your cost" if deepest > 0 else ""))

    v = hist.get("value") or totals.get("total", 0.0)
    for code, key, name in (("SP", "spy", "Beat the S&P 500"), ("BB", "btc", "Beat bitcoin")):
        other = hist.get(key)
        out.append(_badge(code, name, f"Your money did better than the same deposits in {'the S&P 500' if key == 'spy' else 'BTC'}",
                          (v / other) if other else 0.0, f"${v - other:+,.0f} vs it" if other else "needs price history"))

    buys = t[t["type"] == m.BUY] if not t.empty else t
    per_month = buys.groupby([d.strftime("%Y-%m") for d in buys["trade_date"]]).size() if not buys.empty else pd.Series(dtype=int)
    top = int(per_month.max()) if not per_month.empty else 0
    out.append(_badge("ST", "Stacker", "10 or more buys in one month", top / 10,
                      f"most: {top} in {per_month.idxmax()}" if top else ""))
    deps = t[t["type"] == m.DEPOSIT] if not t.empty else t
    months = len({d.strftime("%Y-%m") for d in deps["trade_date"]}) if not deps.empty else 0
    out.append(_badge("SH", "Steady hand", "Added money in 6 different months", months / 6, f"{months} months so far"))
    income = float(t.loc[t["type"].isin([m.DIVIDEND, m.INTEREST]), "amount"].sum()) if not t.empty else 0.0
    out.append(_badge("PW", "Paid to wait", "$100 in dividends and interest", income / 100, f"${income:,.2f} so far"))

    total = totals.get("total", 0.0) or 1.0
    cash_share = totals.get("cash", 0.0) / total
    out.append(_badge("DP", "Dry powder", "Keep 5% or more in cash", cash_share / 0.05, f"{cash_share:.1%} in cash"))
    share = bitcoin_share(assets)
    out.append(_badge("MX", "Bitcoin maxi", "90% or more of the portfolio Bitcoin-linked", share / 0.9, f"{share:.0%} now"))
    btc_qty = float(assets.loc[(assets["symbol"] == "BTC") & (assets["asset_class"] == m.CRYPTO), "quantity"].sum()) if not assets.empty else 0.0
    out.append(_badge("1B", "Whole coin", "Hold 1 BTC directly", btc_qty, f"₿{btc_qty:.4f} held"))
    out.append(_badge("6F", "Six figures", "Portfolio worth $100,000", total / 100_000, f"${total:,.0f} now"))

    oldest = int(lots["days"].max()) if not lots.empty and lots["days"].notna().any() else 0
    out.append(_badge("LG", "Long game", "Hold a lot for over a year (long-term rates)", oldest / 366, f"oldest lot: {oldest} days"))
    sells = t[t["type"] == m.SELL] if not t.empty else t
    since = (today - sells["trade_date"].max()).days if not sells.empty else 9999
    out.append(_badge("HL", "HODL", "90 days without selling anything", since / 90,
                      f"{since} days since your last sale" if not sells.empty else "no sales yet"))
    opt = t[t["symbol"].map(lambda s: bool(option_label(s or "")[0])) & t["type"].isin([m.BUY, m.SELL])] if not t.empty else t
    contracts = float(opt["quantity"].abs().sum()) if not opt.empty else 0.0
    out.append(_badge("OP", "Options pilot", "Trade 5 option contracts", contracts / 5, f"{contracts:g} contracts traded"))
    brokers = set()
    if not accounts.empty and "total" in accounts:
        brokers = {a.institution for a in accounts.itertuples() if (a.total or 0) >= 1}
    out.append(_badge("RA", "Rebel alliance", "Money at 3 different brokers", len(brokers) / 3,
                      ", ".join(sorted(brokers)) if brokers else ""))
    return sorted(out, key=lambda b: (not b["earned"], -b["progress"]))
