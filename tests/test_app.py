"""The dashboard script runs end to end on real synced data (no network: quotes are stubbed)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio import prices
from tests.test_sync import FakeSource, fidelity, kraken, mstr, run, spaxx

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


def deck(at) -> str:
    """The headline deck's HTML ("" while the dashboard is locked or empty)."""
    return " ".join(str(e.proto) for e in at.get("html") if 'class=\\"sw-deck\\"' in str(e.proto))


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()  # the app's loaders are cached per process; each test has its own DB


def test_empty_database_shows_setup_hint(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["sim"] = False                                 # the real (empty) database
    at.run()
    assert not at.exception
    assert any("No data yet" in i.value for i in at.info)


def test_dashboard_renders_with_data(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])), FakeSource("kraken", kraken, derive=True))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(12), spaxx()], cash=0)), FakeSource("kraken", kraken, derive=True))
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["sim"] = False                                 # the real (test) accounts, not the simulation
    at.run()
    assert not at.exception, at.exception
    html = deck(at)
    for label in ("Total value", "YTD return", "Bitcoin", "Today", "Unrealized", "Invested", "Cash", "Cost basis"):
        assert f">{label}<" in html, label
    assert html.count("of total<") == 2                            # Invested and Cash both show their share
    # a card opens its drawer under it, and closes again
    def drawer_html():
        return " ".join(str(e.proto) for e in at.get("html") if 'class=\\"sw-dr-head\\"' in str(e.proto))
    assert not drawer_html()
    at.button(key="sw-open-0").click().run()
    assert not at.exception, at.exception
    assert "The position" in drawer_html()
    assert drawer_html().count('class=\\"sw-st\\"') == 6                         # one line of six
    order = [drawer_html().index(f">{k}<") for k in ("Shares", "Avg cost", "Paid", "Value", "Unrealized", "Total return")]
    assert order == sorted(order)                                                  # paid, then value
    assert any("Taxes" in str(e.proto) and "sw-dr-k" in str(e.proto) for e in at.get("html"))
    at.button(key="sw-drawer-close").click().run()
    assert not drawer_html()
    from panel.dashboard import EXPLORE, TABS
    assert [t.label for t in at.tabs] == TABS[:2] + EXPLORE + TABS[2:]  # Explore's sub-tabs sit inside it
    assert [e.label for e in at.expander][:2] == [at.expander[0].label, "What if bitcoin hits…"]


def test_password_gate_blocks_until_correct(tmp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "a-long-enough-test-password")
    run(FakeSource("kraken", kraken, derive=True))
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not deck(at)
    at.text_input[0].input("nope")
    at.button[0].click().run()
    assert at.error and not deck(at)
    at.text_input[0].input("a-long-enough-test-password")
    at.button[0].click().run()
    assert not at.exception and deck(at)


def test_real_database_without_strong_password_stays_locked(tmp_db, monkeypatch):
    import portfolio.db

    monkeypatch.setattr(portfolio.db, "is_postgres", lambda: True)
    for pw in (None, "short"):
        if pw:
            monkeypatch.setenv("APP_PASSWORD", pw)
        else:
            monkeypatch.delenv("APP_PASSWORD", raising=False)
        at = AppTest.from_file(APP, default_timeout=30).run()
        assert "Locked" in at.error[0].value and not deck(at) and not at.text_input


def test_cloud_app_without_broker_keys_has_no_in_app_sync(tmp_db, monkeypatch):
    for k in ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("kraken", kraken, derive=True))
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert "Sync now" not in [b.label for b in at.button]


def test_simulation_is_on_by_default_and_switches_to_the_real_accounts(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])), FakeSource("kraken", kraken, derive=True))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert "$32,571" in deck(at)                                    # the made-up portfolio
    cards = " ".join(str(e.proto) for e in at.get("html") if "sw-hold" in str(e.proto))
    for sym in ("SPCX", "MSTR", "BTC", "QQQ", "TSLA", "AUR", "Cash", "AAPL"):
        assert f">{sym}<" in cards, sym
    at.button(key="sw-sim-on").click().run()                       # off: the real (test) accounts
    assert not at.exception, at.exception
    assert "$32,571" not in deck(at) and deck(at)
    assert at.button(key="sw-sim-off")
    at.button(key="sw-sim-off").click().run()                      # and back on
    assert "$32,571" in deck(at)
