"""Streamlit plumbing: secrets -> env, the password gate, cached data loaders."""
from __future__ import annotations

import hmac
import os

import streamlit as st


def boot() -> None:
    """Copy Streamlit secrets into env (cloud) so portfolio.config sees them like a local .env."""
    try:
        for k, v in st.secrets.items():
            if isinstance(v, (str, int, float)) and not os.environ.get(k):
                os.environ[k] = str(v)
    except Exception:  # noqa: BLE001 - no secrets.toml locally is fine
        pass


def gate() -> None:
    """APP_PASSWORD set = nobody sees a number without it. Unset = local dev, open."""
    from portfolio.config import env
    from portfolio.db import is_postgres

    pw = env("APP_PASSWORD")
    if not pw:
        if is_postgres():
            st.warning("APP_PASSWORD isn't set, so anyone who can reach this app can see your balances.")
        return
    if st.session_state.get("_ok"):
        return
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered.encode(), pw.encode()):
        st.session_state["_ok"] = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    st.stop()


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str, *args):
    """Cached read of any portfolio.queries function by name."""
    from portfolio import queries

    return getattr(queries, name)(*args)


@st.cache_data(ttl=120, show_spinner=False)
def live_quotes(wanted: frozenset):
    from portfolio.prices import get_quotes

    return get_quotes(set(wanted))
