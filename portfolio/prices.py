"""Live prices between broker updates: Yahoo for stocks/ETFs/funds, Kraken's public ticker for crypto.

Brokers' own prices are the fallback, so a quote outage never zeroes a holding.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from portfolio import models as m
from portfolio.sources._util import num
from portfolio.timeutil import utcnow

YAHOO, KRAKEN, PAR, BROKER = "yahoo", "kraken", "par", "broker"
_PAIRS: tuple[float, dict[str, str]] | None = None
PAIRS_TTL = 6 * 3600


@dataclass
class QuoteData:
    venue: str
    symbol: str
    price: float
    prev_close: float | None
    as_of: datetime


def venue_for(asset_class: str, symbol: str, source: str) -> str | None:
    """Where to get a live price for this holding (None = use the broker's price)."""
    if asset_class in (m.OPTION, m.BOND, m.OTHER_CLASS) and source != "kraken":
        return None
    if source == "kraken" or asset_class in (m.CRYPTO, m.STABLECOIN):
        return KRAKEN
    if asset_class == m.CASH:
        return PAR  # money-market core positions sit at $1
    return YAHOO


def yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-").replace("/", "-")


def yahoo_quotes(symbols: list[str]) -> dict[str, QuoteData]:
    if not symbols:
        return {}
    import pandas as pd
    import yfinance as yf

    ymap = {s: yahoo_symbol(s) for s in symbols}
    df = yf.download(sorted(set(ymap.values())), period="5d", interval="1d", auto_adjust=False,
                     progress=False, group_by="ticker", threads=True)
    now, out = utcnow(), {}
    for s, y in ymap.items():
        try:
            closes = (df[y]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]).dropna()
        except KeyError:
            continue
        if closes.empty:
            continue
        prev = float(closes.iloc[-2]) if len(closes) > 1 else None
        out[s] = QuoteData(YAHOO, s, float(closes.iloc[-1]), prev, now)
    return out


def kraken_quotes(symbols: list[str], client=None) -> dict[str, QuoteData]:
    """`prev_close` is Kraken's opening price for the current UTC day."""
    global _PAIRS
    if not symbols:
        return {}
    from portfolio.sources.kraken import KrakenClient

    client = client or KrakenClient(key="", secret="")
    if _PAIRS is None or time.monotonic() - _PAIRS[0] > PAIRS_TTL:
        _PAIRS = (time.monotonic(), client.usd_pairs())
    pairs = _PAIRS[1]
    want = {s: pairs[s] for s in symbols if s in pairs}
    out: dict[str, QuoteData] = {}
    if want:
        res = client.public("Ticker", pair=",".join(sorted(set(want.values()))))
        now = utcnow()
        for s, p in want.items():
            t = res.get(p)
            if t and t.get("c"):
                out[s] = QuoteData(KRAKEN, s, float(t["c"][0]), num(t.get("o")), now)
    return out


def get_quotes(wanted: set[tuple[str, str]]) -> tuple[dict[tuple[str, str], QuoteData], list[str]]:
    """{(venue, symbol)} -> quotes, plus any errors. One batched call per venue."""
    quotes: dict[tuple[str, str], QuoteData] = {}
    errors: list[str] = []
    for venue, fn in ((YAHOO, yahoo_quotes), (KRAKEN, kraken_quotes)):
        syms = sorted({s for v, s in wanted if v == venue})
        if not syms:
            continue
        try:
            for s, q in fn(syms).items():
                quotes[(venue, s)] = q
        except Exception as e:  # noqa: BLE001 - fall back to broker prices
            errors.append(f"{venue} quotes: {type(e).__name__}: {e}")
    return quotes, errors


def valuation(pos: m.Position, venue: str | None, quote: QuoteData | None):
    """-> (price, prev_close, price_source, as_of, market_value)"""
    if venue == PAR:
        price, prev, src, as_of = 1.0, 1.0, PAR, None
    elif quote is not None:
        price, prev, src, as_of = quote.price, quote.prev_close, quote.venue, quote.as_of
    elif pos.price is not None:
        price, prev, src, as_of = pos.price, None, BROKER, None
    elif pos.asset_class == m.STABLECOIN:
        price, prev, src, as_of = 1.0, 1.0, PAR, None
    else:
        return None, None, "", None, None
    return price, prev, src, as_of, pos.quantity * pos.multiplier * price
