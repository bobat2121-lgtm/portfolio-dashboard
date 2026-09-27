"""Streamlit plumbing: secrets -> env, the password gate, cached data loaders."""
from __future__ import annotations

import hmac
import os
import time

import streamlit as st

MIN_PASSWORD = 16
MAX_TRIES = 5
LOCKOUT_SECONDS = 300


def boot() -> None:
    """Copy Streamlit secrets into env on every run, so edits in the Secrets panel apply without a restart."""
    try:
        changed = False
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float)) and os.environ.get(k) != str(v):
                os.environ[k] = str(v)
                changed = True
        if changed:
            from portfolio.config import settings

            settings.cache_clear()
            st.cache_data.clear()  # results read under the old settings
    except Exception:  # noqa: BLE001 - no secrets.toml locally is fine
        pass


def gate() -> None:
    """Nothing loads until the password is right. Against the real database (Postgres) there is no way
    around it: a missing or short APP_PASSWORD locks the app instead of opening it. Local SQLite and
    demo data stay open."""
    from portfolio.config import env
    from portfolio.db import is_postgres

    pw = env("APP_PASSWORD") or ""
    if env("DEMO") or (not is_postgres() and not pw):
        return  # demo data is made up; local SQLite without a password is your own machine
    if len(pw) < MIN_PASSWORD:
        st.error(f"Locked: set APP_PASSWORD (at least {MIN_PASSWORD} characters) in the app's secrets.")
        st.stop()
    if st.session_state.get("_ok"):
        return

    locked_until = st.session_state.get("_locked_until", 0.0)
    if time.time() < locked_until:
        st.error(f"Too many wrong passwords. Try again in {int(locked_until - time.time()) + 1} s.")
        st.stop()
    with st.form("unlock", clear_on_submit=True):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Unlock")
    if submitted and entered:
        if hmac.compare_digest(entered.encode(), pw.encode()):
            st.session_state["_ok"] = True
            st.session_state.pop("_tries", None)
            st.rerun()
        time.sleep(1.5)  # slows guessing; the real defense is a long random password
        tries = st.session_state.get("_tries", 0) + 1
        st.session_state["_tries"] = tries
        if tries >= MAX_TRIES:
            st.session_state["_locked_until"] = time.time() + LOCKOUT_SECONDS
            st.session_state["_tries"] = 0
        st.error("Wrong password.")
    st.stop()


def can_sync_here() -> bool:
    """Broker keys live only in GitHub Actions (and your local .env), never in the cloud app."""
    from portfolio.config import env
    from portfolio.sources import all_sources

    if env("DEMO"):
        return False  # never pull real accounts into the made-up demo database
    return any(src.missing_config() is None for src in all_sources())


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str, *args):
    """Cached read of any portfolio.queries function by name."""
    from portfolio import queries

    return getattr(queries, name)(*args)


@st.cache_data(ttl=120, show_spinner=False)
def live_quotes(wanted: frozenset):
    from portfolio.prices import get_quotes

    return get_quotes(set(wanted))


# ---------------------------------------------------------------- shared by the Dashboard and Taxes pages

BTC_QUOTE = ("kraken", "BTC")  # always fetched: the Themes and What-if views price things in bitcoin


def panel(name: str):
    """A keyed container: the panel styles (panel/assets/*.css) find it by its st-key-sw-panel-* class."""
    return st.container(key=f"sw-panel-{name}")


def portfolio_data() -> dict | None:
    """Everything the pages draw from, repriced live. None (after saying why) if the database isn't ready."""
    from portfolio import lenses, queries

    try:
        accounts, stored, cash = load("accounts"), load("holdings"), load("cash")
    except Exception:  # noqa: BLE001 - e.g. read-only login before the first sync has created the tables
        st.info("The database isn't ready yet. It fills in after the first sync runs.")
        return None
    wanted = frozenset((queries.wanted_quotes(stored) if not stored.empty else set()) | {BTC_QUOTE})
    quotes, quote_errors = live_quotes(wanted)
    holdings = queries.reprice(stored, quotes) if not stored.empty else stored
    btc = quotes.get(BTC_QUOTE)
    assets = lenses.with_themes(lenses.combine_assets(holdings, cash))
    return {"accounts": accounts, "holdings": holdings, "cash": cash, "quote_errors": quote_errors,
            "totals": queries.totals(holdings, cash) if not accounts.empty else None,
            "btc_price": btc.price if btc else None, "btc_open": btc.prev_close if btc else None,
            "assets": assets, "themes": lenses.by_theme(assets)}


@st.cache_data(ttl=300, show_spinner="Rebuilding your history…")
def history(holdings, cash):
    from portfolio import history as hist
    from portfolio.timeutil import today_ny

    return hist.build(load("all_transactions"), holdings, cash, load("account_rows"), load("price_history"),
                      load("splits"), today_ny())


@st.cache_data(ttl=300, show_spinner=False)
def tax_report(holdings):
    from portfolio import taxes
    from portfolio.timeutil import today_ny

    return taxes.build(load("all_transactions"), holdings, load("account_rows"), load("splits"), today_ny())


@st.cache_data(ttl=3600, show_spinner=False)
def betas(assets_min):
    from portfolio import whatif

    return whatif.betas(load("price_history"), assets_min)


def header(title: str) -> None:
    """Title, the panel-style switcher and the sync buttons; shows the result of an in-app sync."""
    from portfolio import sync
    from portfolio.config import section

    box = st.container(key="sw-header")
    left, right = box.columns([3, 2], vertical_alignment="center")
    left.title(title)
    with right:
        b1, b2 = st.columns(2)
        with b1:
            if can_sync_here():
                if st.button("Sync now", width="stretch", help="Pull every account now (asks SnapTrade to refresh too)"):
                    with st.spinner("Syncing accounts…"):
                        res = sync.run(trigger="manual", refresh=True)
                    st.cache_data.clear()
                    st.session_state["_last_sync"] = res
                    st.rerun()
            elif url := section("app").get("sync_workflow_url"):
                st.link_button("Sync now", url, width="stretch",
                               help="Opens the sync workflow on GitHub: press 'Run workflow', then refresh here in a minute")
        with b2:
            if st.button("Refresh prices", width="stretch"):
                live_quotes.clear()
                st.rerun()
    if res := st.session_state.pop("_last_sync", None):
        msg = " · ".join(f"{k}: {v['status']}" for k, v in res["accounts"].items()) or "no accounts synced"
        skipped = [f"{k} skipped ({v['reason']})" for k, v in res["sources"].items() if v["status"] == "skipped"]
        (st.success if res["status"] == "ok" else st.warning)(f"Sync {res['status']}: {msg}" + (
            "  \n" + "  \n".join(skipped) if skipped else ""))
