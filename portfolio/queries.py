"""Read side for the dashboard: everything comes back as a pandas DataFrame."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select

from portfolio import db, models as m, prices


def _df(stmt) -> pd.DataFrame:
    with db.engine().connect() as c:
        df = pd.read_sql(stmt, c)
    for col in df.columns:  # SQLite hands timestamps back naive; they're all UTC
        if pd.api.types.is_datetime64_any_dtype(df[col]) and df[col].dt.tz is None:
            df[col] = df[col].dt.tz_localize("UTC")
    return df


def accounts() -> pd.DataFrame:
    """Every account with its latest value tick."""
    latest = (select(db.ValueTick.account_key, func.max(db.ValueTick.id).label("tick_id"))
              .group_by(db.ValueTick.account_key).subquery())
    stmt = (select(db.Account.key, db.Account.label, db.Account.institution, db.Account.tax, db.Account.source,
                   db.Account.mapped, db.Account.number_mask, db.Account.last_synced_at, db.Account.data_as_of,
                   db.Account.last_error, db.Account.broker_total,
                   db.ValueTick.total, db.ValueTick.invested, db.ValueTick.cash, db.ValueTick.day_change,
                   db.ValueTick.unpriced)
            .outerjoin(latest, latest.c.account_key == db.Account.key)
            .outerjoin(db.ValueTick, db.ValueTick.id == latest.c.tick_id)
            .order_by(db.Account.mapped.desc(), db.Account.key))
    return _df(stmt)


def holdings() -> pd.DataFrame:
    """Open positions across accounts, as last stored by a sync."""
    stmt = (select(db.Position, db.Account.label.label("account"), db.Account.tax, db.Account.source)
            .join(db.Account, db.Account.key == db.Position.account_key)
            .where(db.Position.closed_at.is_(None), db.Position.quantity != 0))
    df = _df(stmt)
    if df.empty:
        return df
    return df.drop(columns=["meta"], errors="ignore")


def cash() -> pd.DataFrame:
    stmt = (select(db.CashBalance, db.Account.label.label("account"), db.Account.tax)
            .join(db.Account, db.Account.key == db.CashBalance.account_key)
            .where(db.CashBalance.amount != 0))
    return _df(stmt)


def wanted_quotes(h: pd.DataFrame) -> set[tuple[str, str]]:
    out = set()
    for r in h.itertuples():
        venue = prices.venue_for(r.asset_class, r.symbol, r.source)
        if venue in (prices.YAHOO, prices.KRAKEN):
            out.add((venue, r.symbol))
    return out


def reprice(h: pd.DataFrame, quotes: dict) -> pd.DataFrame:
    """Re-value stored holdings with fresh quotes (the app calls this on load, between syncs)."""
    if h.empty:
        return h
    h = h.copy()
    for i, r in h.iterrows():
        q = quotes.get((prices.venue_for(r.asset_class, r.symbol, r.source), r.symbol))
        if q is not None:
            h.at[i, "price"], h.at[i, "prev_close"], h.at[i, "price_source"] = q.price, q.prev_close, q.venue
            h.at[i, "price_as_of"] = q.as_of
            h.at[i, "market_value"] = r.quantity * r.multiplier * q.price
    return enrich(h)


def enrich(h: pd.DataFrame) -> pd.DataFrame:
    if h.empty:
        return h
    h = h.copy()
    h["day_change"] = (h["quantity"] * h["multiplier"] * (h["price"] - h["prev_close"])).where(h["prev_close"].notna())
    h["unrealized"] = h["market_value"] - h["cost_basis"]
    h["unrealized_pct"] = h["unrealized"] / h["cost_basis"].where(h["cost_basis"] > 0)
    counted = h[~h["in_cash_balance"].astype(bool)]
    total = counted["market_value"].sum()
    h["weight"] = (h["market_value"] / total).where(~h["in_cash_balance"].astype(bool)) if total else None
    return h


def totals(h: pd.DataFrame, c: pd.DataFrame) -> dict:
    counted = h[~h["in_cash_balance"].astype(bool)] if not h.empty else h
    cash_positions = counted[counted["asset_class"] == m.CASH]["market_value"].sum() if not h.empty else 0.0
    invested = counted[counted["asset_class"] != m.CASH]["market_value"].sum() if not h.empty else 0.0
    usd_cash = c[c["currency"] == "USD"]["amount"].sum() if not c.empty else 0.0
    positions = counted[counted["asset_class"] != m.CASH] if not h.empty else h
    with_basis = positions[positions["cost_basis"].notna()] if not h.empty else h
    return {
        "total": float(invested + cash_positions + usd_cash),
        "invested": float(invested),
        "cost_basis": float(with_basis["cost_basis"].sum()) if not with_basis.empty else 0.0,  # what you paid
        "cash": float(cash_positions + usd_cash),
        "day_change": float(counted["day_change"].sum()) if not h.empty else 0.0,
        "unrealized": float(with_basis["unrealized"].sum()) if not with_basis.empty else 0.0,
        "basis_coverage": float(with_basis["market_value"].sum() / invested) if invested else 1.0,
        "unknown_basis": int((positions["cost_basis"].isna() & (positions["market_value"].abs() >= 1)).sum())
        if not h.empty else 0,
    }


def allocation(h: pd.DataFrame, c: pd.DataFrame, by: str = "asset_class") -> pd.DataFrame:
    """by: asset_class | account | tax | symbol"""
    parts = []
    if not h.empty:
        parts.append(h.loc[~h["in_cash_balance"].astype(bool), [by, "market_value"]])
    if not c.empty:
        cc = c[c["currency"] == "USD"].rename(columns={"amount": "market_value"}).assign(asset_class=m.CASH, symbol="USD")
        parts.append(cc[[by, "market_value"]])
    if not parts:
        return pd.DataFrame(columns=[by, "market_value", "weight"])
    df = pd.concat(parts).groupby(by, as_index=False)["market_value"].sum().sort_values("market_value", ascending=False)
    df["weight"] = df["market_value"] / df["market_value"].sum()
    return df


def value_history() -> pd.DataFrame:
    """Daily end-of-day value per account (rows: as_of, account_key, value)."""
    stmt = (select(db.HoldingSnapshot.as_of, db.HoldingSnapshot.account_key,
                   func.sum(db.HoldingSnapshot.market_value).label("value"))
            .where(db.HoldingSnapshot.in_cash_balance.is_(False))
            .group_by(db.HoldingSnapshot.as_of, db.HoldingSnapshot.account_key)
            .order_by(db.HoldingSnapshot.as_of))
    return _df(stmt)


def intraday(since) -> pd.DataFrame:
    stmt = (select(db.ValueTick.taken_at, db.ValueTick.account_key, db.ValueTick.total)
            .where(db.ValueTick.taken_at >= since).order_by(db.ValueTick.taken_at))
    return _df(stmt)


def transactions(limit: int = 200) -> pd.DataFrame:
    stmt = (select(db.Transaction.trade_date, db.Account.label.label("account"), db.Transaction.type,
                   db.Transaction.symbol, db.Transaction.quantity, db.Transaction.price, db.Transaction.amount,
                   db.Transaction.fee, db.Transaction.description)
            .join(db.Account, db.Account.key == db.Transaction.account_key)
            .order_by(db.Transaction.trade_date.desc(), db.Transaction.id.desc()).limit(limit))
    return _df(stmt)


def changes(limit: int = 100) -> pd.DataFrame:
    stmt = (select(db.Change.detected_at, db.Account.label.label("account"), db.Change.kind, db.Change.symbol,
                   db.Change.qty_before, db.Change.qty_after, db.Change.value_delta)
            .join(db.Account, db.Account.key == db.Change.account_key)
            .order_by(db.Change.detected_at.desc(), db.Change.id.desc()).limit(limit))
    return _df(stmt)


def contributions() -> pd.DataFrame:
    """Money you put in / took out, per account per month (deposits and withdrawals only)."""
    stmt = (select(db.Transaction.trade_date, db.Account.label.label("account"), db.Transaction.type,
                   db.Transaction.amount)
            .join(db.Account, db.Account.key == db.Transaction.account_key)
            .where(db.Transaction.type.in_([m.DEPOSIT, m.WITHDRAWAL])))
    df = _df(stmt)
    if df.empty:
        return df
    df["month"] = pd.to_datetime(df["trade_date"]).dt.to_period("M").astype(str)
    return df.groupby(["month", "account"], as_index=False)["amount"].sum()


def sync_runs(limit: int = 30) -> pd.DataFrame:
    stmt = (select(db.SyncRun.id, db.SyncRun.started_at, db.SyncRun.finished_at, db.SyncRun.trigger,
                   db.SyncRun.status, db.SyncRun.summary)
            .order_by(db.SyncRun.id.desc()).limit(limit))
    return _df(stmt)


def all_transactions() -> pd.DataFrame:
    """Every transaction with what the history, tax and achievement views need."""
    stmt = (select(db.Transaction.account_key, db.Account.label.label("account"), db.Account.tax,
                   db.Transaction.trade_date, db.Transaction.occurred_at, db.Transaction.type, db.Transaction.symbol,
                   db.Transaction.quantity, db.Transaction.price, db.Transaction.amount, db.Transaction.value_usd,
                   db.Transaction.fee, db.Transaction.id)
            .join(db.Account, db.Account.key == db.Transaction.account_key)
            .order_by(db.Transaction.trade_date, db.Transaction.id))
    return _df(stmt)


def price_history() -> pd.DataFrame:
    return _df(select(db.PriceHistory.symbol, db.PriceHistory.date, db.PriceHistory.close, db.PriceHistory.adj_close))


def splits() -> pd.DataFrame:
    return _df(select(db.Split.symbol, db.Split.date, db.Split.ratio))


def account_rows() -> pd.DataFrame:
    return _df(select(db.Account.key, db.Account.label, db.Account.source, db.Account.raw_type, db.Account.tax))
