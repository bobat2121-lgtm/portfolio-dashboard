"""Storage. SQLite locally, Postgres (Neon) in the cloud via DATABASE_URL.

Nothing here is ever deleted. Positions you exit stay as rows with quantity 0 and a closed_at stamp,
and every table that records history (snapshots, ticks, transactions, changes, sync runs) only grows.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from portfolio.config import DATA_DIR, env
from portfolio.timeutil import utcnow


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)  # fidelity_taxable, kraken, ...
    label: Mapped[str] = mapped_column(String(120), default="")
    institution: Mapped[str] = mapped_column(String(80), default="")
    tax: Mapped[str] = mapped_column(String(20), default="unknown")  # taxable | ira | unknown
    source: Mapped[str] = mapped_column(String(20), default="")  # snaptrade | kraken
    external_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    number_mask: Mapped[str] = mapped_column(String(20), default="")
    raw_type: Mapped[str] = mapped_column(String(80), default="")
    mapped: Mapped[bool] = mapped_column(Boolean, default=True)  # False = found at the broker but not in config
    broker_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Position(Base):
    """Current holdings, one row per account+symbol. Exited positions keep their row at quantity 0."""
    __tablename__ = "positions"
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    asset_class: Mapped[str] = mapped_column(String(20), default="other")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    broker_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # price used for valuation
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_source: Mapped[str] = mapped_column(String(20), default="")  # yahoo | kraken | broker | par
    price_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_basis: Mapped[float | None] = mapped_column(Float, nullable=True)  # total $
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    in_cash_balance: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class CashBalance(Base):
    __tablename__ = "cash_balances"
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    currency: Mapped[str] = mapped_column(String(10), primary_key=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    buying_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HoldingSnapshot(Base):
    """End-of-day holdings: one row per day+account+symbol. The day's last sync is the one that sticks."""
    __tablename__ = "holding_snapshots"
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_class: Mapped[str] = mapped_column(String(20), default="other")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_basis: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_cash_balance: Mapped[bool] = mapped_column(Boolean, default=False)  # skip when summing, cash row covers it
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValueTick(Base):
    """Account value at every sync: the intraday line."""
    __tablename__ = "value_ticks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(80), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    invested: Mapped[float] = mapped_column(Float, default=0.0)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    day_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    broker_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    unpriced: Mapped[int] = mapped_column(Integer, default=0)  # positions with no price at all
    sync_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("account_key", "external_id", name="uq_txn_account_external"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(80), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(24), index=True)
    raw_type: Mapped[str] = mapped_column(String(60), default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    description: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Change(Base):
    """What moved between two syncs: shows a buy, sell or deposit right away.

    Brokers post the matching transaction a day later.
    """
    __tablename__ = "changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(80), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sync_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(20))  # opened | added | trimmed | closed | cash_in | cash_out
    qty_before: Mapped[float] = mapped_column(Float, default=0.0)
    qty_after: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_delta: Mapped[float | None] = mapped_column(Float, nullable=True)


class Quote(Base):
    __tablename__ = "quotes"
    venue: Mapped[str] = mapped_column(String(10), primary_key=True)  # yahoo | kraken
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[float] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PriceHistory(Base):
    """Daily closes from Yahoo for everything you've held, plus the benchmarks (BTC-USD, SPY).
    `close` is split-adjusted; `adj_close` is also dividend-adjusted (a total-return series)."""
    __tablename__ = "price_history"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)  # Yahoo symbol: MSTR, BTC-USD, BRK-B
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Split(Base):
    """Stock splits (ratio = new shares per old share: 0.05 is a 1-for-20 reverse split). Brokers don't
    always report them as transactions, so history and tax lots adjust old quantities with these."""
    __tablename__ = "splits"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    ratio: Mapped[float] = mapped_column(Float)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), default="cli")  # schedule | manual | cli
    status: Mapped[str] = mapped_column(String(12), default="running")  # ok | partial | error | skipped
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- engine

_engines: dict[str, object] = {}   # one engine per database URL
_base_url: str | None = None
_override: ContextVar[str | None] = ContextVar("db_url_override", default=None)


@contextmanager
def using(url: str | None):
    """Point this thread's reads at another database (the Simulation switch's made-up one) for a block.
    Each Streamlit session runs in its own thread, so one visitor's switch never touches another's."""
    token = _override.set(url)
    try:
        yield
    finally:
        _override.reset(token)


def db_url() -> str:
    if (o := _override.get()):
        return o
    if env("DEMO"):  # `python -m jobs.demo` fills this with made-up accounts for UI work
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(DATA_DIR / 'demo.db').as_posix()}"
    url = env("DATABASE_URL")
    if not url:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(DATA_DIR / 'portfolio.db').as_posix()}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def engine():
    """One engine per URL. The main one is rebuilt whenever its URL changes, e.g. when Streamlit secrets
    are edited while the app is running."""
    global _base_url
    url = db_url()
    if _override.get() is None:
        if _base_url is not None and url != _base_url and (old := _engines.pop(_base_url, None)) is not None:
            old.dispose()
        _base_url = url
    if url not in _engines:
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        e = create_engine(url, **kwargs)
        try:
            Base.metadata.create_all(e)
        except ProgrammingError:
            pass  # read-only login (the dashboard): the sync job, which can write, creates tables
        _engines[url] = e
    return _engines[url]


def is_postgres() -> bool:
    return db_url().startswith("postgresql")


def reset_engine() -> None:
    """Drop the cached engines so the next call re-reads DATABASE_URL (tests)."""
    global _base_url
    for e in _engines.values():
        e.dispose()
    _engines.clear()
    _base_url = None


def session() -> Session:
    return Session(engine(), expire_on_commit=False)


def upsert(s: Session, model, rows: list[dict], keys: list[str], keep: tuple[str, ...] = ()) -> None:
    """INSERT ... ON CONFLICT (keys) DO UPDATE every other column except `keep` (e.g. first_seen_at)."""
    if not rows:
        return
    if s.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        stmt = insert(model).values(chunk)
        cols = [c for c in chunk[0] if c not in keys and c not in keep]
        if cols:
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in cols})
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        s.execute(stmt)
