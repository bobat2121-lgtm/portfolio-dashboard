"""The dashboard script runs end to end on real synced data (no network: quotes are stubbed)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio import prices
from tests.test_sync import FakeSource, fidelity, kraken, mstr, run, spaxx

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()  # the app's loaders are cached per process; each test has its own DB


def test_empty_database_shows_setup_hint(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("No data yet" in i.value for i in at.info)


def test_dashboard_renders_with_data(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])), FakeSource("kraken", kraken, derive=True))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(12), spaxx()], cash=0)), FakeSource("kraken", kraken, derive=True))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception, at.exception
    labels = [mt.label for mt in at.metric]
    assert labels == ["Total", "Today", "Cost basis", "Cash", "Unrealized"]
    assert [t.label for t in at.tabs] == ["Overview", "Holdings", "Activity", "Sync"]


def test_password_gate_blocks_until_correct(tmp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "a-long-enough-test-password")
    run(FakeSource("kraken", kraken, derive=True))
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.metric
    at.text_input[0].input("nope")
    at.button[0].click().run()
    assert at.error and not at.metric
    at.text_input[0].input("a-long-enough-test-password")
    at.button[0].click().run()
    assert not at.exception and at.metric


def test_real_database_without_strong_password_stays_locked(tmp_db, monkeypatch):
    import portfolio.db

    monkeypatch.setattr(portfolio.db, "is_postgres", lambda: True)
    for pw in (None, "short"):
        if pw:
            monkeypatch.setenv("APP_PASSWORD", pw)
        else:
            monkeypatch.delenv("APP_PASSWORD", raising=False)
        at = AppTest.from_file(APP, default_timeout=30).run()
        assert "Locked" in at.error[0].value and not at.metric and not at.text_input


def test_cloud_app_without_broker_keys_has_no_in_app_sync(tmp_db, monkeypatch):
    for k in ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("kraken", kraken, derive=True))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert "Sync now" not in [b.label for b in at.button]
