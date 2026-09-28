"""The dashboard script runs end to end on real synced data (no network: quotes are stubbed)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from portfolio import prices
from tests.test_sync import FakeSource, fidelity, kraken, mstr, run, spaxx

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


def app(timeout: int = 60, **state) -> AppTest:
    """The app past its Enter screen, with any session state set."""
    at = AppTest.from_file(APP, default_timeout=timeout)
    at.session_state["entered"] = True
    for k, v in state.items():
        at.session_state[k] = v
    return at


def deck(at) -> str:
    """The headline deck's HTML ("" while the dashboard is locked or empty)."""
    return " ".join(str(e.proto) for e in at.get("html") if 'class=\\"sw-deck\\"' in str(e.proto))


@pytest.fixture(autouse=True)
def fresh_cache():
    from panel import auth

    st.cache_data.clear()  # the app's loaders are cached per process; each test has its own DB
    auth._misses.clear()


def test_empty_database_shows_setup_hint(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    at = app(sim=False).run()                                      # the real (empty) database
    assert not at.exception
    assert any("No data yet" in i.value for i in at.info)


def test_dashboard_renders_with_data(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])), FakeSource("kraken", kraken, derive=True))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(12), spaxx()], cash=0)), FakeSource("kraken", kraken, derive=True))
    at = app(sim=False).run()                                      # the real (test) accounts, not the simulation
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


PW = "a-long-enough-test-password"


def test_the_start_screen_is_one_enter_button_then_the_simulation(tmp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PW)
    run(FakeSource("kraken", kraken, derive=True))
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert [b.label for b in at.button] == ["Enter"] and not deck(at) and not at.text_input
    at.button(key="sw-enter-go").click().run()
    assert not at.exception, at.exception
    assert "$32,571" in deck(at) and at.button(key="sw-sim-on")            # the made-up portfolio
    assert "Lock" not in [b.label for b in at.button]


def test_the_real_accounts_stay_out_of_reach_without_the_password(tmp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PW)
    run(FakeSource("kraken", kraken, derive=True))
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    at = app(sim=False)                                            # the switch forced off...
    at.session_state["_pass"] = "9999999999." + "0" * 64          # ...and a forged pass
    at.run()
    assert not at.exception, at.exception
    assert "$32,571" in deck(at) and at.button(key="sw-sim-on")   # still only the Simulation
    assert "Lock" not in [b.label for b in at.button]


def test_switching_the_simulation_off_asks_for_the_password(tmp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PW)
    run(FakeSource("kraken", kraken, derive=True))
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    at = app().run()
    at.button(key="sw-sim-on").click().run()
    assert not at.exception, at.exception
    assert at.text_input[0].label == "Password" and at.checkbox[0].value      # remember: on by default
    at.text_input[0].input("not-the-password-at-all")
    at.button(key="FormSubmitter:sw-unlock-Unlock").click().run()
    assert any("Wrong password" in e.value for e in at.error)
    assert "$32,571" in deck(at)
    at.text_input[0].input(PW)
    at.button(key="FormSubmitter:sw-unlock-Unlock").click().run()
    assert not at.exception, at.exception
    assert deck(at) and "$32,571" not in deck(at)                               # the real (test) accounts
    assert at.button(key="sw-sim-off") and at.button(key="sw-lock")
    saved = " ".join(str(e.proto) for e in at.get("html") if "sw-cookie" in str(e.proto))
    assert 'localStorage.setItem(\\"sw_pass\\"' in saved and PW not in saved
    at.button(key="sw-sim-off").click().run()                                   # back on: no password needed
    assert "$32,571" in deck(at)
    at.button(key="sw-sim-on").click().run()                                    # and off again, still unlocked
    assert "$32,571" not in deck(at) and not at.text_input
    at.button(key="sw-lock").click().run()                                      # Lock: forget this browser
    assert "$32,571" in deck(at) and "Lock" not in [b.label for b in at.button]
    saved = " ".join(str(e.proto) for e in at.get("html") if "sw-cookie" in str(e.proto))
    assert "localStorage.removeItem" in saved
    at.button(key="sw-sim-on").click().run()
    assert at.text_input[0].label == "Password"                                 # asks again


def test_a_remembered_browser_switches_off_without_asking(tmp_db, monkeypatch):
    from panel import auth

    monkeypatch.setenv("APP_PASSWORD", PW)
    run(FakeSource("kraken", kraken, derive=True))
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    monkeypatch.setattr(auth, "_reader", lambda **kw: {"token": auth.make_pass(PW, 3600)})  # the page sends it
    at = app().run()
    assert "$32,571" in deck(at)                                   # still opens on the Simulation
    at.button(key="sw-sim-on").click().run()
    assert not at.text_input and deck(at) and "$32,571" not in deck(at)
    monkeypatch.setenv("APP_PASSWORD", PW + "-changed")            # a new password voids every pass
    at.run()
    assert "$32,571" in deck(at)


def test_real_database_without_strong_password_offers_only_the_simulation(tmp_db, monkeypatch):
    import portfolio.db

    monkeypatch.setattr(portfolio.db, "is_postgres", lambda: True)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    for pw in (None, "short"):
        if pw:
            monkeypatch.setenv("APP_PASSWORD", pw)
        else:
            monkeypatch.delenv("APP_PASSWORD", raising=False)
        at = app(sim=False).run()
        assert not at.exception, at.exception
        assert "$32,571" in deck(at)
        at.button(key="sw-sim-on").click().run()
        assert "APP_PASSWORD" in at.error[0].value and not at.text_input and "$32,571" in deck(at)


def test_cloud_app_without_broker_keys_has_no_in_app_sync(tmp_db, monkeypatch):
    for k in ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("kraken", kraken, derive=True))
    at = app(sim=False).run()
    assert "Sync now" not in [b.label for b in at.button]


def test_simulation_is_on_by_default_and_switches_to_the_real_accounts(tmp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setattr(prices, "get_quotes", lambda wanted: ({}, []))
    run(FakeSource("snaptrade", lambda: fidelity([mstr(), spaxx()])), FakeSource("kraken", kraken, derive=True))
    at = app().run()
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
