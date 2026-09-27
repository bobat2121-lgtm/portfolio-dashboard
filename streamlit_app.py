"""Portfolio dashboard: your accounts over a pixel-art galaxy (see panel/theme.py for the look).

    streamlit run streamlit_app.py              # your data
    streamlit run streamlit_app.py -- --demo    # made-up data from `python -m jobs.demo`

Two pages: the Dashboard (panel/dashboard.py) and Taxes (panel/taxes_page.py).
"""
from __future__ import annotations

import os
import sys

import streamlit as st

from panel import theme
from panel.common import boot, gate

if "--demo" in sys.argv:
    os.environ["DEMO"] = "1"

st.set_page_config(page_title="Portfolio", layout="wide")
boot()
theme.apply(theme.current_style())  # the scene shows on the lock screen too; it carries no data
gate()

from panel import dashboard, taxes_page  # noqa: E402  (after boot: secrets must be in env first)

st.navigation([
    st.Page(dashboard.render, title="Dashboard", url_path="dashboard", default=True),
    st.Page(taxes_page.render, title="Taxes", url_path="taxes"),
], position="top").run()
