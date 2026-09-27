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
    assert labels == ["Total", "Today", "Invested", "Cash", "Unrealized"]
    assert [t.label for t in at.tabs] == ["Overview", "Holdings", "Activity", "Sync"]


def test_password_gate_blocks_until_correct(tmp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.metric
    at.text_input[0].input("nope").run()
    assert at.error
    at.text_input[0].input("hunter2").run()
    assert not at.exception
