from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from portfolio import models as m, prices, queries, sync
from portfolio.prices import QuoteData
from portfolio.timeutil import today_ny

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, name, make, derive=False):
        self.name, self.make, self.derive_cost_basis = name, make, derive

    def missing_config(self):
        return None

    def fetch(self, since, refresh=False):
        return self.make()


def fidelity(positions, cash=500.0, broker_total=None, txns=()):
    acct = m.SourceAccount(external_id="fid-1", institution="Fidelity", name="Individual", number="Z1234",
                           raw_type="INDIVIDUAL", broker_total=broker_total)
    return [m.AccountSnapshot(account=acct, positions=positions, cash=[m.Cash("USD", cash)],
                              transactions=list(txns), data_as_of=NOW)]


def mstr(qty=10.0):
    return m.Position(symbol="MSTR", quantity=qty, asset_class=m.EQUITY, price=300.0, cost_basis=2500.0 * qty / 10)


def spaxx(qty=500.0):
    return m.Position(symbol="SPAXX", quantity=qty, asset_class=m.CASH, price=1.0, in_cash_balance=True)


def kraken():
    acct = m.SourceAccount(external_id="kraken", institution="Kraken", name="Kraken")
    txns = [
        m.Txn("kraken:D1", m.DEPOSIT, NOW, amount=1000.0),
        m.Txn("kraken:T1", m.BUY, NOW, symbol="BTC", quantity=0.01, amount=-602.4, value_usd=600.0, fee=2.4),
    ]
    return [m.AccountSnapshot(account=acct, positions=[m.Position("BTC", 0.01, m.CRYPTO)],
                              cash=[m.Cash("USD", 397.6)], transactions=txns, data_as_of=NOW)]


def quotes(wanted):
    table = {("yahoo", "MSTR"): QuoteData("yahoo", "MSTR", 310.0, 300.0, NOW),
             ("kraken", "BTC"): QuoteData("kraken", "BTC", 65000.0, 64000.0, NOW)}
    return {k: v for k, v in table.items() if k in wanted}, []


@pytest.fixture(autouse=True)
def no_min_gap(monkeypatch):
    monkeypatch.setattr(sync, "source_settings", lambda name: {})


def run(*sources, **kw):
    return sync.run(sources=list(sources), quote_fn=quotes, **kw)


def count(db, model, **where):
    with db.session() as s:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return s.scalar(stmt)


def test_first_sync_maps_prices_and_records(tmp_db):
    res = run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])),
              FakeSource("kraken", kraken, derive=True))
    assert res["status"] == "ok", res
    fid, kr = res["accounts"]["fidelity_taxable"], res["accounts"]["kraken"]
    # MSTR repriced by the live quote; SPAXX is already inside the $500 cash, so it isn't added again
    assert fid["total"] == 3100 + 500
    assert fid["changes"] == 0  # first sync has no baseline
    assert kr["total"] == pytest.approx(650 + 397.6)
    with tmp_db.session() as s:
        btc = s.get(tmp_db.Position, ("kraken", "BTC"))
        assert btc.cost_basis == pytest.approx(602.4)  # derived from the ledger, fee included
        assert btc.price_source == "kraken"
        tick = s.scalars(select(tmp_db.ValueTick).where(tmp_db.ValueTick.account_key == "fidelity_taxable")).one()
        assert tick.day_change == pytest.approx(100.0)
    assert count(tmp_db, tmp_db.HoldingSnapshot, account_key="fidelity_taxable", as_of=today_ny()) == 3  # MSTR, SPAXX, cash


def test_sell_everything_then_deposit_is_detected_and_nothing_is_deleted(tmp_db):
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])))
    res = run(FakeSource("snaptrade", lambda: fidelity([spaxx(3500)], cash=3500)))
    assert res["accounts"]["fidelity_taxable"]["changes"] == 2
    with tmp_db.session() as s:
        kinds = {c.symbol: (c.kind, c.value_delta) for c in s.scalars(select(tmp_db.Change)).all()}
        assert kinds["MSTR"] == ("closed", -3100.0)  # valued at its last known price
        assert kinds["USD"] == ("cash_in", 3000.0)
        row = s.get(tmp_db.Position, ("fidelity_taxable", "MSTR"))
        assert row.quantity == 0 and row.closed_at is not None  # kept, not deleted
        snap = s.get(tmp_db.HoldingSnapshot, (today_ny(), "fidelity_taxable", "MSTR"))
        assert snap.quantity == 0 and snap.market_value == 0  # today's EOD no longer counts it
    assert queries.holdings()["symbol"].tolist() == ["SPAXX"]


def test_empty_fetch_while_broker_says_invested_keeps_holdings(tmp_db):
    run(FakeSource("snaptrade", lambda: fidelity([mstr()], broker_total=3500)))
    res = run(FakeSource("snaptrade", lambda: fidelity([], broker_total=3500)))
    assert res["accounts"]["fidelity_taxable"]["status"] == "suspect"
    assert res["status"] == "partial"
    with tmp_db.session() as s:
        assert s.get(tmp_db.Position, ("fidelity_taxable", "MSTR")).quantity == 10
        assert s.scalar(select(func.count()).select_from(tmp_db.Change)) == 0


def test_transactions_are_idempotent_and_keep_first_seen(tmp_db):
    t = m.Txn("a1", m.DEPOSIT, NOW, amount=500.0)
    run(FakeSource("snaptrade", lambda: fidelity([mstr()], txns=[t])))
    run(FakeSource("snaptrade", lambda: fidelity([mstr()], txns=[t, m.Txn("a2", m.DIVIDEND, NOW, amount=3.0)])))
    assert count(tmp_db, tmp_db.Transaction) == 2
    contrib = queries.contributions()
    assert contrib["amount"].sum() == 500


def test_unlisted_account_is_still_tracked(tmp_db):
    def two():
        extra = m.SourceAccount(external_id="hsa-9", institution="Fidelity", name="Health Savings", number="X9999",
                                raw_type="HSA")
        return fidelity([mstr()]) + [m.AccountSnapshot(account=extra, cash=[m.Cash("USD", 50)])]

    res = run(FakeSource("snaptrade", two))
    # the HSA isn't a retirement account by our rules, so both Fidelity accounts match fidelity_taxable
    assert any("fidelity_taxable" in w for w in res["warnings"])
    assert set(res["accounts"]) == {"fidelity_1234", "fidelity_9999"}
    assert all(v.get("unmapped") for v in res["accounts"].values())


def test_source_failure_is_reported_and_other_sources_still_write(tmp_db):
    def boom():
        raise RuntimeError("broker down")

    res = run(FakeSource("snaptrade", boom), FakeSource("kraken", kraken, derive=True))
    assert res["status"] == "partial"
    assert res["sources"]["snaptrade"]["status"] == "error"
    assert res["accounts"]["kraken"]["status"] == "ok"


def test_dry_run_writes_nothing(tmp_db):
    res = run(FakeSource("kraken", kraken, derive=True), dry_run=True)
    assert res["accounts"]["kraken"]["status"] == "ok"
    assert count(tmp_db, tmp_db.Position) == 0 and count(tmp_db, tmp_db.SyncRun) == 0


def test_live_reprice_in_the_app(tmp_db, monkeypatch):
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])))
    h = queries.holdings()
    later = {("yahoo", "MSTR"): QuoteData("yahoo", "MSTR", 320.0, 300.0, NOW)}
    assert queries.wanted_quotes(h) == {("yahoo", "MSTR")}
    h = queries.reprice(h, later)
    t = queries.totals(h, queries.cash())
    assert t["total"] == 3200 + 500 and t["day_change"] == 200
    assert t["unrealized"] == 3200 - 2500
    alloc = queries.allocation(h, queries.cash())
    assert set(alloc["asset_class"]) == {"equity", "cash"}
