"""Different ways to look at the same holdings: by asset, by theme, and the words for the Briefing crawl.

Pure pandas on the frames from portfolio.queries (holdings already repriced, cash balances).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

import pandas as pd

from portfolio import models as m
from portfolio.config import section

OCC = re.compile(r"^([A-Z.]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
CASH_ASSET = "Cash"
DEFAULT_THEMES = [  # used when config/portfolio.yaml has no themes
    {"name": "Bitcoin", "color": "#F7931A", "bitcoin_linked": True, "symbols": ["BTC", "IBIT", "FBTC", "GBTC"]},
    {"name": "Bitcoin treasuries", "color": "#FFE81F", "bitcoin_linked": True, "symbols": ["MSTR", "ASST", "SMLR"]},
    {"name": "Digital credit", "color": "#4BD5EE", "bitcoin_linked": True, "symbols": ["STRC", "STRK", "STRF", "STRD", "SATA"]},
    {"name": "Crypto", "color": "#B388FF", "symbols": ["COIN"], "asset_classes": ["crypto"]},
    {"name": "Stocks & funds", "color": "#39FF14", "asset_classes": ["equity", "etf", "fund", "bond", "other"]},
    {"name": "Cash & stablecoins", "color": "#8A8F98", "asset_classes": ["cash", "stablecoin"]},
]
OTHER_THEME = {"name": "Other", "color": "#D8B373"}


def option_label(symbol: str) -> tuple[str | None, str]:
    """'ASST  280121C00035000' -> ('ASST', "ASST $35 call Jan '28"); anything else -> (None, symbol)."""
    mt = OCC.match(symbol.replace(" ", "").upper())
    if not mt:
        return None, symbol
    und, yy, mm, _dd, cp, strike = mt.groups()
    k = int(strike) / 1000
    return und, f"{und} ${k:g} {'call' if cp == 'C' else 'put'} {MONTHS[int(mm) - 1]} '{yy}"


def combine_assets(h: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """One row per holding across every account (BTC on Kraken + Robinhood = one BTC). All cash, including
    money-market sweep funds, becomes a single Cash row. Positions already counted inside cash are skipped."""
    rows: dict[str, dict] = {}

    def add(key, **kw):
        r = rows.setdefault(key, {"asset": key, "symbol": key, "underlying": key, "asset_class": kw.get("asset_class", ""),
                                  "name": "", "quantity": 0.0, "price": None, "market_value": 0.0, "cost_basis": 0.0,
                                  "basis_known": 0.0, "basis_complete": True, "day_change": 0.0, "accounts": set()})
        for k in ("asset", "symbol", "underlying", "asset_class"):
            if kw.get(k):
                r[k] = kw[k]
        if kw.get("name") and not r["name"]:
            r["name"] = kw["name"]
        if kw.get("price") is not None and r["price"] is None:
            r["price"] = kw["price"]
        r["quantity"] += kw.get("quantity") or 0.0
        mv = kw.get("market_value")
        r["market_value"] += 0.0 if mv is None or pd.isna(mv) else mv
        cb = kw.get("cost_basis")
        if cb is None or pd.isna(cb):
            r["basis_complete"] = False
        else:
            r["cost_basis"] += cb
            r["basis_known"] += 0.0 if mv is None or pd.isna(mv) else mv
        dc = kw.get("day_change")
        r["day_change"] += 0.0 if dc is None or pd.isna(dc) else dc
        if kw.get("account"):
            r["accounts"].add(kw["account"])

    if not h.empty:
        for p in h[~h["in_cash_balance"].astype(bool)].itertuples():
            if p.asset_class == m.CASH:
                add(CASH_ASSET, asset_class=m.CASH, name="Cash and money-market funds", market_value=p.market_value,
                    quantity=p.market_value, cost_basis=p.market_value, account=p.account, price=1.0)
                continue
            und, label = option_label(p.symbol) if p.asset_class == m.OPTION else (None, p.symbol)
            add(p.symbol, asset=label, symbol=p.symbol, underlying=und or p.symbol, asset_class=p.asset_class,
                name=getattr(p, "name", "") or "", quantity=p.quantity, price=p.price, market_value=p.market_value,
                cost_basis=p.cost_basis, day_change=getattr(p, "day_change", None), account=p.account)
    if not c.empty:
        for r in c[c["currency"] == "USD"].itertuples():
            if r.amount:
                add(CASH_ASSET, asset_class=m.CASH, name="Cash and money-market funds", market_value=r.amount,
                    quantity=r.amount, cost_basis=r.amount, account=r.account, price=1.0)

    df = pd.DataFrame(list(rows.values()))
    if df.empty:
        return df
    df["accounts"] = df["accounts"].map(lambda s: sorted(s))
    df["n_accounts"] = df["accounts"].map(len)
    df.loc[~df["basis_complete"], "cost_basis"] = float("nan")
    df["unrealized"] = df["market_value"] - df["cost_basis"]
    df["unrealized_pct"] = df["unrealized"] / df["cost_basis"].where(df["cost_basis"] > 0)
    total = df["market_value"].sum()
    df["weight"] = df["market_value"] / total if total else 0.0
    return df.drop(columns=["basis_known"]).sort_values("market_value", ascending=False).reset_index(drop=True)


def theme_rules() -> list[dict]:
    rules = section("themes")
    return rules if isinstance(rules, list) and rules else DEFAULT_THEMES


def theme_of(symbol: str, asset_class: str, underlying: str | None = None, rules: list[dict] | None = None) -> dict:
    rules = rules or theme_rules()
    look = {(underlying or symbol).upper(), symbol.upper()}
    for r in rules:                       # symbols first (an option follows its underlying's symbol)
        if look & {s.upper() for s in r.get("symbols") or []}:
            return r
    if asset_class == m.OPTION:           # an option on something not listed: its underlying is probably a stock
        asset_class = m.EQUITY
    for r in rules:
        if asset_class in (r.get("asset_classes") or []):
            return r
    return OTHER_THEME


def with_themes(assets: pd.DataFrame, rules: list[dict] | None = None) -> pd.DataFrame:
    if assets.empty:
        return assets
    rules = rules or theme_rules()
    df = assets.copy()
    th = [theme_of(r.symbol, r.asset_class, r.underlying, rules) for r in df.itertuples()]
    df["theme"] = [t["name"] for t in th]
    df["theme_color"] = [t.get("color", OTHER_THEME["color"]) for t in th]
    df["bitcoin_linked"] = [bool(t.get("bitcoin_linked")) for t in th]
    return df


def by_theme(assets: pd.DataFrame) -> pd.DataFrame:
    """assets must come from with_themes()."""
    if assets.empty:
        return pd.DataFrame()
    g = assets.groupby(["theme", "theme_color", "bitcoin_linked"], as_index=False).agg(
        market_value=("market_value", "sum"), day_change=("day_change", "sum"),
        cost_basis=("cost_basis", lambda s: s.sum(min_count=len(s))),
        members=("asset", list), member_values=("market_value", list))
    g["unrealized"] = g["market_value"] - g["cost_basis"]
    g["unrealized_pct"] = g["unrealized"] / g["cost_basis"].where(g["cost_basis"] > 0)
    total = g["market_value"].sum()
    g["weight"] = g["market_value"] / total if total else 0.0
    return g.sort_values("market_value", ascending=False).reset_index(drop=True)


def bitcoin_share(assets: pd.DataFrame) -> float:
    if assets.empty or "bitcoin_linked" not in assets:
        return 0.0
    total = assets["market_value"].sum()
    return float(assets.loc[assets["bitcoin_linked"], "market_value"].sum() / total) if total else 0.0


# ---------------------------------------------------------------- the Briefing crawl

def roman(n: int) -> str:
    out, n = "", max(1, int(n))
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                 (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += s
            n -= v
    return out


def episode_title(day_pct: float) -> str:
    if day_pct >= 0.02:
        return "The Rally Awakens"
    if day_pct > 0.003:
        return "Return of the Bulls"
    if day_pct <= -0.02:
        return "Revenge of the Sellers"
    if day_pct < -0.003:
        return "The Bears Strike Back"
    return "The Phantom Drift"


def _money(v: float) -> str:
    return f"${abs(v):,.0f}"


def crawl(totals: dict, assets: pd.DataFrame, accounts: list[str], first_day: date | None, today: date) -> dict:
    """The opening crawl for today, written from the numbers. Returns {episode, title, paragraphs}."""
    total, day = totals["total"], totals["day_change"]
    base = total - day
    pct = day / base if base else 0.0
    episode = roman(((today - first_day).days if first_day else 0) + 1)
    mood = "rising hope" if pct > 0.003 else "galactic unease" if pct < -0.003 else "uneasy calm"
    direction = "up" if day >= 0 else "down"
    held = assets[(assets["asset"] != CASH_ASSET) & (assets["market_value"] >= 1)] if not assets.empty else assets
    bases = ", ".join(accounts[:-1]) + (f" and {accounts[-1]}" if len(accounts) > 1 else (accounts[0] if accounts else ""))
    p1 = (f"It is a period of {mood}. The Rebel treasury stands at {_money(total)}, {direction} {_money(day)} "
          f"({abs(pct):.2%}) since the last close. From {len(accounts)} hidden bases, {bases}, the Alliance "
          f"commands {len(held)} {'position' if len(held) == 1 else 'positions'}.")
    paras = [p1]
    if not held.empty:
        lead = held.iloc[0]
        p2 = f"{lead['asset']} leads the fleet, {lead['weight']:.0%} of the treasury at {_money(lead['market_value'])}."
        movers = held[held["day_change"].abs() >= 1].sort_values("day_change")
        if not movers.empty:
            best, worst = movers.iloc[-1], movers.iloc[0]
            if best["day_change"] > 0:
                p2 += f" {best['asset']} gained {_money(best['day_change'])} today"
                p2 += f", while {worst['asset']} lost {_money(worst['day_change'])}." if worst["day_change"] < 0 else "."
            elif worst["day_change"] < 0:
                p2 += f" {worst['asset']} lost {_money(worst['day_change'])} today."
        paras.append(p2)
    unreal = totals.get("unrealized", 0.0)
    p3 = (f"Acquired for {_money(totals.get('cost_basis', 0.0))}, the fleet now carries {_money(unreal)} in "
          f"unrealized {'gains' if unreal >= 0 else 'losses'}. {_money(totals['cash'])} in credits waits in the "
          "hangar, ready to deploy.")
    paras.append(p3)
    share = bitcoin_share(assets)
    if share > 0:
        paras.append(f"{share:.0%} of the treasury is bound to the power of Bitcoin, and as its price moves, so "
                     "moves the fate of the galaxy....")
    else:
        paras.append("And the next transmission from the brokers arrives with the next sync....")
    return {"episode": f"Episode {episode}", "title": episode_title(pct), "paragraphs": paras}


def ellipse(n: int, rx: float, ry: float) -> list[tuple[float, float]]:
    return [(rx * math.cos(2 * math.pi * i / n), ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def as_date(v) -> date | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(v).date()
    except (ValueError, TypeError):
        return None
