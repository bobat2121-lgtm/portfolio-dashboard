"""BTC Supernova: your accounts over a pixel-art galaxy (see panel/theme.py for the look).

    streamlit run streamlit_app.py              # your data
    streamlit run streamlit_app.py -- --demo    # made-up data from `python -m jobs.demo`

Two pages: the Dashboard (panel/dashboard.py) and Taxes (panel/taxes_page.py).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

from panel import auth, theme
from panel.common import boot

if "--demo" in sys.argv:
    os.environ["DEMO"] = "1"

# the bookmark icon: a pixel ₿ in Star Wars yellow on space black (16x16 art, saved at 64x64)
ICON = Path(__file__).parent / "panel" / "assets" / "favicon.png"
st.set_page_config(page_title="BTC Supernova", page_icon=str(ICON), layout="wide")
boot()
theme.apply(theme.current_style())  # the scene shows on the Enter screen too; it carries no data
auth.recall()                       # a pass this browser saved ("Remember this browser")
auth.flush()                        # a change to it from the last unlock / lock
auth.entrance()                     # the Enter button; the app opens on the Simulation (see panel/auth.py)

from panel import dashboard, taxes_page  # noqa: E402  (after boot: secrets must be in env first)

st.navigation([
    st.Page(dashboard.render, title="Dashboard", url_path="dashboard", default=True),
    st.Page(taxes_page.render, title="Taxes", url_path="taxes"),
], position="top").run()
