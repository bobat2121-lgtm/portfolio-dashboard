"""Daily price and split history for everything you've ever held, plus the benchmarks.

Filled by the sync job (GitHub Actions reaches Yahoo reliably) and read by the dashboard. Only missing
days are fetched after the first backfill; nothing is ever deleted.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from portfolio import db, models as m
from portfolio.config import pricing
from portfolio.lenses import option_label
from portfolio.timeutil import today_ny, utcnow

BENCHMARKS = {"BTC-USD": "Bitcoin", "SPY": "S&P 500"}
LOOKBACK_DAYS = 420  # a year+ of closes for betas, even for things bought recently


def cash_like(symbol: str) -> bool:
    return symbol.upper() in {s.upper() for s in pricing().get("cash_symbols") or []}


def crypto_accounts(accounts: pd.DataFrame) -> set[str]:
    if accounts.empty:
        return set()
    mask = (accounts["source"] == "kraken") | accounts["raw_type"].fillna("").str.upper().isin({"DIGITALASSET", "CRYPTO"})
    return set(accounts.loc[mask, "key"])


def yahoo_for(symbol: str, crypto: bool) -> str | None:
    """Yahoo symbol for a holding, or None when there's no market series (options, cash, USD)."""
    if not symbol or cash_like(symbol) or symbol.upper() in {"USD", "CASH:USD"} or option_label(symbol)[0]:
        return None
    if crypto:
        return f"{symbol.upper()}-USD"
    return symbol.upper().replace(".", "-").replace("/", "-")


def wanted(s) -> dict[str, date]:
    """Yahoo symbol -> first date we need prices from."""
    accts = pd.read_sql(select(db.Account.key, db.Account.source, db.Account.raw_type), s.connection())
    crypto = crypto_accounts(accts)
    stables = {x.upper() for x in pricing().get("stablecoins") or []}
    out: dict[str, date] = {}

    def need(sym: str, acct: str, since: date | None, asset_class: str = ""):
        is_crypto = acct in crypto or asset_class in (m.CRYPTO, m.STABLECOIN)
        if is_crypto and sym.upper() in stables:
            return
        y = yahoo_for(sym, is_crypto)
        if y:
            start = min(since or today_ny(), today_ny() - timedelta(days=LOOKBACK_DAYS))
            out[y] = min(out.get(y, start), start)

    for sym, acct, first in s.execute(select(db.Transaction.symbol, db.Transaction.account_key,
                                             func.min(db.Transaction.trade_date))
                                      .where(db.Transaction.symbol != "")
                                      .group_by(db.Transaction.symbol, db.Transaction.account_key)).all():
        need(sym, acct, first)
    for sym, acct, cls in s.execute(select(db.Position.symbol, db.Position.account_key, db.Position.asset_class)).all():
        need(sym, acct, None, cls)
    first_any = s.scalar(select(func.min(db.Transaction.trade_date)))
    for b in BENCHMARKS:
        start = min(first_any or today_ny(), today_ny() - timedelta(days=LOOKBACK_DAYS))
        out[b] = min(out.get(b, start), start)
    return out


def download(symbols: list[str], start: date) -> pd.DataFrame:
    """Long frame: symbol, date, close, adj_close, split."""
    import yfinance as yf

    df = yf.download(sorted(symbols), start=start.isoformat(), end=(today_ny() + timedelta(days=1)).isoformat(),
                     auto_adjust=False, actions=True, progress=False, group_by="ticker", threads=True)
    rows = []
    for sym in symbols:
        try:
            sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        for d, r in sub.iterrows():
            rows.append({"symbol": sym, "date": pd.Timestamp(d).date(), "close": float(r["Close"]),
                         "adj_close": float(r["Adj Close"]) if pd.notna(r.get("Adj Close")) else None,
                         "split": float(r.get("Stock Splits") or 0.0)})
    return pd.DataFrame(rows)


def update(s) -> dict:
    """Fetch what's missing and store it. Returns a small summary for the sync run."""
    need = wanted(s)
    if not need:
        return {"symbols": 0, "rows": 0}
    have = dict(s.execute(select(db.PriceHistory.symbol, func.max(db.PriceHistory.date))
                          .group_by(db.PriceHistory.symbol)).all())
    first = dict(s.execute(select(db.PriceHistory.symbol, func.min(db.PriceHistory.date))
                           .group_by(db.PriceHistory.symbol)).all())
    today = today_ny()
    batches: dict[date, list[str]] = {}
    for sym, start in need.items():
        if sym in have and first.get(sym) and first[sym] <= start + timedelta(days=5):
            if have[sym] >= today - timedelta(days=1):
                continue                                      # up to date
            start = have[sym] - timedelta(days=5)             # a little overlap for late corrections
        batches.setdefault(start, []).append(sym)
    stored = 0
    for start, syms in sorted(batches.items()):
        df = download(syms, start)
        if df.empty:
            continue
        now = utcnow()
        db.upsert(s, db.PriceHistory, [{"symbol": r.symbol, "date": r.date, "close": r.close,
                                        "adj_close": r.adj_close, "fetched_at": now} for r in df.itertuples()],
                  keys=["symbol", "date"])
        splits = df[df["split"] > 0]
        db.upsert(s, db.Split, [{"symbol": r.symbol, "date": r.date, "ratio": r.split} for r in splits.itertuples()],
                  keys=["symbol", "date"])
        stored += len(df)
    s.commit()
    return {"symbols": len(need), "rows": stored}
