"""In-kind transfers must move assets, never invent cash or purchase basis."""
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from portfolio import db, history, models as m, queries, taxes
from portfolio.costbasis import average_cost, basis_for
from portfolio.sources.snaptrade import parse_activity
from portfolio.transactions import normalize_security_transfer, normalize_transactions


START = date(2025, 12, 30)
MOVED = date(2026, 1, 2)
TODAY = date(2026, 1, 4)


def transaction(i, account, day, kind, symbol="", quantity=None, amount=None, price=None):
    return {"id": i, "account_key": account, "trade_date": day, "occurred_at": None,
            "type": kind, "symbol": symbol, "quantity": quantity, "amount": amount,
            "price": price, "value_usd": None, "fee": 0.0}


def accounts():
    return pd.DataFrame([
        ["exchange", "Exchange", "kraken", "CRYPTO", "taxable"],
        ["broker", "Broker", "snaptrade", "CRYPTO", "taxable"],
    ], columns=["key", "label", "source", "raw_type", "tax"])


def positions(rows=()):
    return pd.DataFrame(rows, columns=["account_key", "symbol", "quantity", "price", "multiplier",
                                       "asset_class", "in_cash_balance", "market_value"])


def prices():
    return pd.DataFrame([
        {"date": day.date(), "symbol": symbol, "close": price, "adj_close": price}
        for day in pd.date_range(START, TODAY)
        for symbol, price in [("BTC-USD", 100_000.0), ("SPY", 500.0)]
    ])


def splits():
    return pd.DataFrame(columns=["symbol", "date", "ratio"])


@pytest.mark.parametrize("raw_kind,quantity,kind,expected_quantity", [
    ("CONTRIBUTION", 0.02, m.TRANSFER_IN, 0.02),
    ("CONTRIBUTION", -0.02, m.TRANSFER_IN, 0.02),
    ("WITHDRAWAL", 0.02, m.TRANSFER_OUT, -0.02),
    ("WITHDRAWAL", -0.02, m.TRANSFER_OUT, -0.02),
    ("EXTERNAL_ASSET_TRANSFER_IN", 0.02, m.TRANSFER_IN, 0.02),
    ("EXTERNAL_ASSET_TRANSFER_OUT", 0.02, m.TRANSFER_OUT, -0.02),
    ("TRANSFER", -0.02, m.TRANSFER, -0.02),
])
def test_broker_security_transfer_is_not_usd_cash(raw_kind, quantity, kind, expected_quantity):
    raw = {"id": "synthetic-transfer", "type": raw_kind, "symbol": {"symbol": "BTC"},
           "units": quantity, "price": 100_000.0, "amount": 2000.0, "fee": 0.0,
           "trade_date": "2026-01-02", "currency": {"code": "USD"}}
    parsed = parse_activity(raw)
    assert parsed.type == kind
    assert parsed.quantity == pytest.approx(expected_quantity)
    assert parsed.amount is None
    assert parsed.value_usd == pytest.approx(2000.0)
    assert parsed.raw == raw
    assert parsed.price == 100_000.0


@pytest.mark.parametrize("raw_kind,expected", [("CONTRIBUTION", 2000.0), ("WITHDRAWAL", -2000.0)])
@pytest.mark.parametrize("symbol,quantity", [("", None), ("USD", 2000.0), ("SPAXX", 2000.0)])
def test_cash_deposits_and_withdrawals_keep_their_cash_effect(raw_kind, expected, symbol, quantity):
    parsed = parse_activity({"id": "cash", "type": raw_kind, "symbol": {"symbol": symbol},
                             "units": quantity, "amount": 2000.0, "trade_date": "2026-01-02"})
    assert parsed.type == (m.DEPOSIT if expected > 0 else m.WITHDRAWAL)
    assert parsed.amount == expected


@pytest.mark.parametrize("quantity", [None, 0.0, float("nan"), float("inf")])
def test_transfer_classification_requires_a_real_nonzero_quantity(quantity):
    row = {"type": m.DEPOSIT, "symbol": "BTC", "quantity": quantity, "amount": 2000.0}
    normalized = normalize_security_transfer(row)
    assert normalized["type"] == m.DEPOSIT
    assert normalized["amount"] == 2000.0


def test_normalization_is_idempotent_and_preserves_dataframe_identity_fields():
    original = pd.DataFrame([
        {"type": m.DEPOSIT, "symbol": "BTC", "quantity": -0.02, "amount": 2000.0,
         "value_usd": None, "occurred_at": pd.Timestamp("2026-01-02", tz="UTC"), "raw": {"id": "one"}},
        {"type": m.BUY, "symbol": "BTC", "quantity": 0.02, "amount": -2000.0,
         "value_usd": 2000.0, "occurred_at": pd.Timestamp("2026-01-03", tz="UTC"), "raw": {"id": "two"}},
    ], index=[7, 7])
    before = original.copy(deep=True)
    normalized = normalize_transactions(original)
    pd.testing.assert_frame_equal(original, before)
    pd.testing.assert_frame_equal(normalize_transactions(normalized), normalized)
    assert normalized.index.equals(original.index)
    assert normalized.columns.equals(original.columns)
    pd.testing.assert_series_equal(normalized["occurred_at"], original["occurred_at"])
    assert normalized["raw"].tolist() == original["raw"].tolist()
    assert normalized.iloc[1]["amount"] == -2000.0


def seed_legacy_rows(rows):
    with db.session() as session:
        for row in accounts().to_dict("records"):
            session.add(db.Account(**row))
        for row in rows:
            session.add(db.Transaction(**row, external_id=f"synthetic-{row['id']}"))
        session.commit()


def test_existing_stored_transfers_are_corrected_in_all_dashboard_reads(tmp_db):
    seed_legacy_rows([
        transaction(1, "broker", START, m.DEPOSIT, amount=500.0),
        transaction(2, "broker", MOVED, m.DEPOSIT, "BTC", 0.02, 2000.0),
        transaction(3, "broker", MOVED, m.WITHDRAWAL, "BTC", 0.01, -1000.0),
    ])
    for frame in (queries.all_transactions(), queries.transactions()):
        incoming = frame[frame["type"] == m.TRANSFER_IN].iloc[0]
        outgoing = frame[frame["type"] == m.TRANSFER_OUT].iloc[0]
        assert incoming["quantity"] == 0.02 and pd.isna(incoming["amount"])
        assert outgoing["quantity"] == -0.01 and pd.isna(outgoing["amount"])
    contributions = queries.contributions()
    assert contributions["amount"].sum() == pytest.approx(500.0)
    # Read-side compatibility also repairs transactions older than a sync's overlap window.
    with db.session() as session:
        original = session.get(db.Transaction, 2)
        assert original.type == m.DEPOSIT and original.amount == 2000.0


def test_stored_internal_transfer_then_sale_rebuilds_flat_history_without_a_new_contribution(tmp_db):
    seed_legacy_rows([
        transaction(1, "exchange", START, m.DEPOSIT, amount=1000.0),
        transaction(2, "exchange", START, m.BUY, "BTC", 0.01, -1000.0, 100_000.0),
        transaction(3, "broker", START, m.DEPOSIT, amount=100.0),
        transaction(4, "exchange", MOVED, m.TRANSFER_OUT, "BTC", -0.01),
        transaction(5, "broker", MOVED, m.DEPOSIT, "BTC", 0.01, 1000.0),
        transaction(6, "broker", MOVED, m.SELL, "BTC", -0.01, 1000.0, 100_000.0),
    ])
    cash = pd.DataFrame([["broker", "USD", 1100.0]], columns=["account_key", "currency", "amount"])
    rebuilt = history.build(queries.all_transactions(), positions(), cash, accounts(), prices(), splits(), TODAY)
    assert rebuilt.daily["value"].tolist() == pytest.approx([1100.0] * len(rebuilt.daily))
    assert rebuilt.daily.loc[MOVED, "flow"] == pytest.approx(0.0)
    assert rebuilt.daily["flow"].sum() == pytest.approx(1100.0)
    assert not rebuilt.notes
    ytd = history.ytd(rebuilt, TODAY)
    assert ytd["gain"] == pytest.approx(0.0)
    assert ytd["pct"] == pytest.approx(0.0)
    assert ytd["capital"] == pytest.approx(1100.0)


def test_external_coin_withdrawal_reduces_assets_and_flow_without_withdrawing_cash():
    rows = pd.DataFrame([
        transaction(1, "broker", START, m.DEPOSIT, amount=1000.0),
        transaction(2, "broker", START, m.BUY, "BTC", 0.01, -1000.0, 100_000.0),
        transaction(3, "broker", MOVED, m.WITHDRAWAL, "BTC", 0.004, -400.0),
    ])
    held = positions([["broker", "BTC", 0.006, 100_000.0, 1.0, m.CRYPTO, False, 600.0]])
    cash = pd.DataFrame(columns=["account_key", "currency", "amount"])
    rebuilt = history.build(normalize_transactions(rows), held, cash, accounts().iloc[1:], prices(), splits(), TODAY)
    assert rebuilt.daily.loc[START, "value"] == pytest.approx(1000.0)
    assert rebuilt.daily.loc[MOVED, "value"] == pytest.approx(600.0)
    assert rebuilt.daily.loc[MOVED, "flow"] == pytest.approx(-400.0)
    assert history.ytd(rebuilt, TODAY)["gain"] == pytest.approx(0.0)
    assert history.ytd(rebuilt, TODAY)["pct"] == pytest.approx(0.0)


def test_transferred_market_value_never_becomes_an_invented_tax_cost():
    rows = normalize_transactions(pd.DataFrame([
        transaction(1, "broker", START, m.DEPOSIT, "BTC", 0.02, 2000.0),
        transaction(2, "broker", MOVED, m.SELL, "BTC", -0.01, 1100.0, 110_000.0),
    ]))
    held = positions([["broker", "BTC", 0.01, 110_000.0, 1.0, m.CRYPTO, False, 1100.0]])
    report = taxes.build(rows, held, accounts().iloc[1:], splits(), TODAY)
    assert len(report.realized) == 1
    assert report.realized["proceeds"].sum() == pytest.approx(1100.0)
    assert report.realized["cost"].isna().all()
    assert not report.realized["basis_known"].any()
    assert report.lots["qty"].sum() == pytest.approx(0.01)
    assert report.lots["cost"].isna().all()
    parsed = parse_activity({"id": "unknown-basis", "type": "CONTRIBUTION", "symbol": {"symbol": "BTC"},
                             "units": 0.02, "amount": 2000.0, "trade_date": "2026-01-02"})
    parsed.occurred_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    basis = average_cost([parsed])["BTC"]
    assert basis.units == pytest.approx(0.02)
    assert not basis.complete
    assert basis_for(0.02, basis) is None
