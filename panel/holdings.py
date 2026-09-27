"""The front page's "every holding" panel: one card per holding, all accounts combined.

Each card leads with the symbol and what the holding is worth, then four readouts on their own lines
so nothing crowds: price, today's move, gain on cost, and share of the portfolio. Click a card and a
drawer opens right under its row (panel/drawer.py) with the tranches, trades and taxes behind it.
"""
from __future__ import annotations

import math
import re

import pandas as pd
import streamlit as st

from panel.views import MIN_SHOWN, esc, pct, tone, usd
from portfolio import lenses, models as m

ARROW = {"up": "▲", "down": "▼", "flat": "■"}
KIND = {m.EQUITY: "Stock", m.ETF: "ETF", m.FUND: "Fund", m.OPTION: "Option", m.CRYPTO: "Crypto",
        m.STABLECOIN: "Stablecoin", m.CASH: "Cash", m.BOND: "Bond"}
# trailing boilerplate in broker names: "Strive, Inc. Class A Common Stock" -> "Strive"
BOILERPLATE = re.compile(r"(?:[\s,-]+(?:inc\.?|corp\.?|corporation|co\.|ltd\.?|plc|class [a-z]|common stock|"
                         r"ordinary shares|ads|adr))+$", re.IGNORECASE)


def price_fmt(v) -> str:
    """$84,400 for big prices, $158.6 above $100, $29.44 above $1, $0.786 below."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    a = abs(v)
    if a >= 1000:
        return f"${v:,.0f}"
    if a >= 100:
        return f"${v:,.1f}"
    if a >= 1 or a == 0:
        return f"${v:,.2f}"
    return f"${v:,.3f}"


def day_pct(r) -> float | None:
    prev = r.market_value - r.day_change
    return r.day_change / prev if prev else None


def _cell(label: str, value: str, sub: str = "", cls: str = "", wide: bool = False) -> str:
    return (f'<div class="sw-cell{" wide" if wide else ""}"><span class="k">{label}</span>'
            f'<span class="v {cls}">{value}</span>{sub}</div>')


def _sub(text: str, cls: str = "flat") -> str:
    return f'<span class="s {cls}">{text}</span>'


def _cells(r) -> str:
    share = _cell("Share", pct(r.weight), f'<div class="sw-bar"><i style="width:{max(1.5, r.weight * 100):.1f}%"></i></div>',
                  wide=r.asset == lenses.CASH_ASSET)
    if r.asset == lenses.CASH_ASSET:
        return share
    c = day_pct(r)
    move = "" if c is None else _sub(f"{ARROW[tone(c * 100)]} {abs(c):.2%}", tone(c * 100))
    if pd.isna(r.unrealized):
        gain = _cell("Gain", "—", _sub("no cost basis"))
    else:
        gain = _cell("Gain", usd(r.unrealized, signed=True), _sub(pct(r.unrealized_pct, True), tone(r.unrealized)),
                     tone(r.unrealized))
    price = "—" if r.price is None or pd.isna(r.price) else price_fmt(float(r.price))
    return (_cell("Price", price, cls="price") + _cell("Today", usd(r.day_change, signed=True), move, tone(r.day_change))
            + gain + share)


def _title(r) -> tuple[str, str, str]:
    """(symbol, the line under it, its full text for the tooltip). Options read as the ticker over the
    contract: ASST / $35 call Jan '28. A name that only repeats the symbol gives way to the kind of
    holding, so every card lines up."""
    under = getattr(r, "underlying", None)
    if r.asset_class == m.OPTION and isinstance(under, str) and under and str(r.asset).startswith(under + " "):
        line = str(r.asset)[len(under) + 1:]
        return under, line, line
    full = r.name if isinstance(r.name, str) else ""
    name = BOILERPLATE.sub("", full).strip() if r.asset_class in (m.EQUITY, m.FUND) else full
    if not name or name.upper() == str(r.asset).upper():
        name = full = KIND.get(r.asset_class, str(r.asset_class).title())
    return str(r.asset), name, full


def card(r, is_open: bool = False) -> str:
    sym, name, full = _title(r)
    chips = "".join(f"<span>{esc(a)}</span>" for a in r.accounts)
    return (f'<div class="sw-card sw-hold{" open" if is_open else ""}"><div class="sw-hold-head"><span class="sw-sym">{esc(sym)}</span>'
            f'<span class="sw-hold-name" title="{esc(full)}">{esc(name)}</span></div>'
            f'<div class="sw-hold-value">{usd(r.market_value)}</div><div class="sw-cells">{_cells(r)}</div>'
            f'<div class="sw-chips">{chips}</div></div>')


def cards(shown: pd.DataFrame) -> str:
    return f'<div class="sw-cards sw-cards-hold">{"".join(card(r) for r in shown.itertuples())}</div>'


def _toggle(asset: str) -> None:
    st.session_state["sw_open"] = None if st.session_state.get("sw_open") == asset else asset


def render(assets: pd.DataFrame, ctx: dict | None = None) -> None:
    st.subheader("Every holding, all accounts combined")
    shown = assets[assets["market_value"] > MIN_SHOWN]
    if shown.empty:
        st.caption("No holdings over $100 yet.")
        return
    if ctx is None:
        st.html(cards(shown))
        return
    _grid(shown, ctx)


@st.fragment
def _grid(shown: pd.DataFrame, ctx: dict) -> None:
    """The cards, each with an invisible full-card button; the open one's drawer follows it. The grid is
    CSS (base.css): auto-fill columns with dense packing, so a full-width drawer placed right after a card
    lands under that card's row at any width, and the cards after it fill the row back in. A fragment, so
    opening a card reruns only this panel."""
    from panel import drawer  # imports price_fmt from here

    open_ = st.session_state.get("sw_open")
    if open_ not in set(shown["asset"]):
        open_ = None
    with st.container(key="sw-holdgrid"):
        for i, r in enumerate(shown.itertuples()):
            with st.container(key=f"sw-slot-{i}"):
                st.html(card(r, r.asset == open_))
                st.button(f"{'Close' if r.asset == open_ else 'Open'} {r.asset}", key=f"sw-open-{i}",
                          on_click=_toggle, args=(r.asset,))
            if r.asset == open_:
                with st.container(key="sw-drawer"):
                    drawer.render(r, ctx)
