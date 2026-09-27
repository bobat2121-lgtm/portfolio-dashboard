"""'What if BTC hits $X?' Each holding moves with bitcoin according to its beta: how much it has moved
per 1% move in BTC over the past year of daily closes (log returns). Projection uses
value x (target / now) ^ beta, which behaves sensibly for big moves. Options are re-priced from their
underlying at expiry value plus today's time value. A rough guide, not a forecast.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from portfolio import models as m
from portfolio.lenses import CASH_ASSET, OCC
from portfolio.pricehist import yahoo_for

FALLBACK_BETA = {"Bitcoin": 1.0, "Bitcoin treasuries": 1.4, "Digital credit": 0.15, "Crypto": 1.1,
                 "Stocks & funds": 0.3, "Cash & stablecoins": 0.0, "Other": 0.5}
MIN_DAYS = 40


def _closes(prices: pd.DataFrame, sym: str) -> pd.Series:
    s = prices[prices["symbol"] == sym]
    return pd.Series(s["close"].values, index=pd.to_datetime(s["date"])).sort_index() if not s.empty else pd.Series(dtype=float)


def betas(prices: pd.DataFrame, assets: pd.DataFrame, window_days: int = 365) -> dict[str, dict]:
    """asset -> {beta, days, source}"""
    out: dict[str, dict] = {}
    btc = _closes(prices, "BTC-USD") if not prices.empty else pd.Series(dtype=float)
    if not btc.empty:
        btc = btc[btc.index >= btc.index.max() - pd.Timedelta(days=window_days)]
    for r in assets.itertuples():
        theme = getattr(r, "theme", "Other")
        if r.asset_class in (m.CASH, m.STABLECOIN) or r.asset == CASH_ASSET:
            out[r.asset] = {"beta": 0.0, "days": 0, "source": "cash"}
            continue
        is_crypto = r.asset_class == m.CRYPTO
        if is_crypto and r.symbol.upper() == "BTC":
            out[r.asset] = {"beta": 1.0, "days": 0, "source": "is bitcoin"}
            continue
        ysym = yahoo_for(r.underlying or r.symbol, is_crypto)
        a = _closes(prices, ysym) if ysym and not prices.empty else pd.Series(dtype=float)
        joined = pd.concat([a.rename("a"), btc.rename("b")], axis=1, join="inner").dropna()
        if len(joined) > MIN_DAYS:
            rets = np.log(joined).diff().dropna()
            beta = float(rets.cov().loc["a", "b"] / rets["b"].var())
            out[r.asset] = {"beta": float(np.clip(beta, -0.5, 4.0)), "days": len(rets), "source": "measured"}
        else:
            out[r.asset] = {"beta": FALLBACK_BETA.get(theme, 0.5), "days": len(joined), "source": f"{theme} default"}
    return out


def project(assets: pd.DataFrame, b: dict[str, dict], btc_now: float, target: float) -> pd.DataFrame:
    ratio = target / btc_now if btc_now else 1.0
    under_px = {r.symbol: r.price for r in assets.itertuples() if r.price is not None and not pd.isna(r.price)}
    rows = []
    for r in assets.itertuples():
        beta = b.get(r.asset, {"beta": 0.0})["beta"]
        v = float(r.market_value)
        mt = OCC.match((r.symbol or "").replace(" ", "").upper())
        if mt and r.asset_class == m.OPTION:
            und, _yy, _mm, _dd, cp, strike = mt.groups()
            K, S = int(strike) / 1000, under_px.get(und)
            if S:
                contracts = float(r.quantity)
                intrinsic = lambda s: max(0.0, (s - K) if cp == "C" else (K - s)) * 100 * contracts  # noqa: E731
                time_value = max(0.0, v - intrinsic(S))
                S2 = S * ratio ** b.get(und, {"beta": beta})["beta"]
                rows.append({"asset": r.asset, "now": v, "then": intrinsic(S2) + time_value, "beta": None})
                continue
        rows.append({"asset": r.asset, "now": v, "then": v * ratio ** beta if v > 0 else v, "beta": beta})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["change"] = df["then"] - df["now"]
        df["change_pct"] = df["change"] / df["now"].where(df["now"] > 0)
    return df


def curve(assets: pd.DataFrame, b: dict, btc_now: float, lo: float, hi: float, n: int = 80) -> pd.DataFrame:
    pts = np.exp(np.linspace(math.log(lo), math.log(hi), n))
    return pd.DataFrame({"btc": pts, "total": [project(assets, b, btc_now, p)["then"].sum() for p in pts]})


def price_choices(btc_now: float) -> list[float]:
    """Round BTC prices around today's, for the slider."""
    base = [25e3, 40e3, 50e3, 60e3, 75e3, 100e3, 125e3, 150e3, 200e3, 250e3, 300e3, 400e3, 500e3, 750e3, 1e6]
    keep = [p for p in base if btc_now / 4 <= p <= btc_now * 12]
    return sorted(set(keep + [round(btc_now, -2)]))
