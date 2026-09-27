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
    from portfolio.sources import all_sources

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
