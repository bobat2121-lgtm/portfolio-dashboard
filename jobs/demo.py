"""Fill data/demo.db with made-up accounts so the dashboard can be styled before any real keys exist.

    python -m jobs.demo
    DEMO=1 streamlit run streamlit_app.py        (PowerShell: $env:DEMO=1; streamlit run streamlit_app.py)

Only ever touches data/demo.db; your real database is never involved.
"""
from __future__ import annotations

import math
import os
import random
from datetime import timedelta

os.environ["DEMO"] = "1"

from portfolio import db, models as m, sync  # noqa: E402
from portfolio.config import DATA_DIR  # noqa: E402
from portfolio.timeutil import today_ny, utcnow  # noqa: E402


class DemoSource:
    def __init__(self, name, make, derive=False):
        self.name, self.make, self.derive_cost_basis = name, make, derive

    def missing_config(self):
        return None

    def fetch(self, since, refresh=False):
        return self.make()


def snap(ext, inst, name, raw_type, positions, cash, txns=()):
    acct = m.SourceAccount(external_id=ext, institution=inst, name=name, number=f"DEMO{ext[-4:]}", raw_type=raw_type)
    return m.AccountSnapshot(account=acct, positions=positions, cash=[m.Cash("USD", cash)], transactions=list(txns),
                             data_as_of=utcnow())


def P(sym, qty, cls, price, cost, **kw):
    return m.Position(symbol=sym, quantity=qty, asset_class=cls, price=price, cost_basis=cost, name=sym, **kw)


def state(later: bool):
    now = utcnow()
    fid = snap("demo-fid1", "Fidelity", "Individual", "INDIVIDUAL", [
        P("MSTR", 40, m.EQUITY, 160.0, 9200.0), P("STRC", 120 + (30 if later else 0), m.EQUITY, 98.5, 11820.0 + (2955 if later else 0)),
        P("VTI", 55, m.ETF, 330.0, 14000.0), P("SPAXX", 2400, m.CASH, 1.0, 2400.0, in_cash_balance=True),
    ], 2400.0 - (2955 if later else 0) + (5000 if later else 0),
        [m.Txn("demo-dep-1", m.DEPOSIT, now - timedelta(days=40), amount=10000.0)]
        + ([m.Txn("demo-dep-2", m.DEPOSIT, now, amount=5000.0)] if later else []))
    rh = snap("demo-rh01", "Robinhood", "Robinhood Individual", "individual", [
        P("IBIT", 150, m.ETF, 48.0, 6300.0), P("COIN", 12, m.EQUITY, 310.0, 2900.0),
    ], 830.0, [m.Txn("demo-dep-3", m.DEPOSIT, now - timedelta(days=70), amount=9000.0)])
    ira = snap("demo-ira1", "Robinhood", "Robinhood Roth IRA", "ira_roth", [
        P("SATA", 200, m.EQUITY, 99.0, 19400.0), P("QQQ", 20, m.ETF, 560.0, 9800.0),
    ], 410.0, [m.Txn("demo-dep-4", m.DEPOSIT, now - timedelta(days=200), amount=7000.0)])
    kr_txns = [
        m.Txn("demo-k-dep", m.DEPOSIT, now - timedelta(days=90), amount=20000.0),
        m.Txn("demo-k-btc", m.BUY, now - timedelta(days=89), symbol="BTC", quantity=0.2, amount=-15040.0, value_usd=15000.0, fee=40.0),
        m.Txn("demo-k-eth", m.BUY, now - timedelta(days=60), symbol="ETH", quantity=1.0, amount=-2605.0, value_usd=2600.0, fee=5.0),
        m.Txn("demo-k-rwd", m.REWARD, now - timedelta(days=10), symbol="ETH", quantity=0.004),
    ]
    kraken = snap("kraken", "Kraken", "Kraken", "spot", [
        m.Position("BTC", 0.2, m.CRYPTO, name="BTC"), m.Position("ETH", 1.004, m.CRYPTO, name="ETH"),
        m.Position("USDC", 500, m.STABLECOIN, name="USDC"),
    ], 1855.0, kr_txns)
    return [fid, rh, ira], [kraken]


def backfill(days: int = 90) -> None:
    """Invent a daily history that ends at today's real snapshot."""
    rnd = random.Random(7)
    with db.session() as s:
        today = s.query(db.HoldingSnapshot).filter(db.HoldingSnapshot.as_of == today_ny()).all()
        rows = []
        for back in range(1, days + 1):
            day = today_ny() - timedelta(days=back)
            drift = 1 - 0.0012 * back + 0.03 * math.sin(back / 9) + rnd.uniform(-0.01, 0.01)
            for r in today:
                cash_like = r.symbol.startswith(sync.CASH_PREFIX) or r.asset_class in (m.CASH, m.STABLECOIN)
                f = 1.0 if cash_like else drift * (1.6 if r.asset_class == m.CRYPTO else 1.0) - (0.6 if r.asset_class == m.CRYPTO else 0.0)
                rows.append({"as_of": day, "account_key": r.account_key, "symbol": r.symbol, "asset_class": r.asset_class,
                             "quantity": r.quantity, "price": (r.price or 0) * f,
                             "market_value": None if r.market_value is None else r.market_value * f,
                             "cost_basis": r.cost_basis, "in_cash_balance": r.in_cash_balance, "taken_at": utcnow()})
        db.upsert(s, db.HoldingSnapshot, rows, keys=["as_of", "account_key", "symbol"])
        s.commit()


def main() -> int:
    path = DATA_DIR / "demo.db"
    db.reset_engine()
    if path.exists():
        path.unlink()  # demo file only
    for later in (False, True):
        brokers, kraken = state(later)
        res = sync.run(trigger="demo", force=True, sources=[
            DemoSource("snaptrade", lambda b=brokers: b), DemoSource("kraken", lambda k=kraken: k, derive=True),
        ])
    backfill()
    print(f"demo data written to {path} ({res['status']}).")
    print("View it:  $env:DEMO=1; streamlit run streamlit_app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
