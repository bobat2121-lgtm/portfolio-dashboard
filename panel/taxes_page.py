"""The Taxes page: realized gains by year and term, lots about to go long-term, loss-harvest candidates,
wash-sale flags and a rough estimate. First-in-first-out lots rebuilt from the transaction history."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from panel.common import header, panel, portfolio_data, tax_report
from panel.views import esc, pct, tone, usd
from portfolio import taxes
from portfolio.timeutil import today_ny

USD = st.column_config.NumberColumn(format="dollar")
QTY = st.column_config.NumberColumn(format="%.6g")
SOON_DAYS = 90


def _stats(items) -> None:
    st.html('<div class="sw-stats">' + "".join(
        f'<div class="sw-stat"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
        for k, v, s in items) + "</div>")


def render() -> None:
    data = portfolio_data()
    if data is None:
        return
    header("Taxes")
    rep = tax_report(data["holdings"])
    today = today_ny()
    R, L = rep.realized, rep.lots
    years = sorted({int(y) for y in R["year"]} | {today.year}, reverse=True) if not R.empty else [today.year]
    year = st.segmented_control("Tax year", years, default=today.year, key="tax_year") or today.year
    ys = taxes.year_summary(rep, year)
    rt = ys["rates"]

    with panel("tax-summary"):
        st.subheader(f"{year} at a glance")
        _stats([
            ("Short-term gains", f'<span class="{tone(ys["short"])}">{usd(ys["short"], signed=True)}</span>',
             "held a year or less; taxed like income"),
            ("Long-term gains", f'<span class="{tone(ys["long"])}">{usd(ys["long"], signed=True)}</span>',
             "held over a year; lower rates"),
            ("Dividends + interest", usd(ys["income"]), "taxable accounts"),
            ("Rough tax", usd(ys["estimate"]),
             f"at {pct(rt['short_term_rate'])} short / {pct(rt['long_term_rate'])} long"
             + (f" + {pct(rt['state_rate'])} state" if rt["state_rate"] else "")),
            ("Possible wash sales", str(ys["wash"]), "loss sales with a buy within 30 days"),
        ])
        if ys["unknown_proceeds"]:
            st.caption(f"{usd(ys['unknown_proceeds'])} of {year} sales were shares bought before the history SnapTrade "
                       "has, so their gain isn't counted above.")

    taxable = L[L["tax"] == "taxable"] if not L.empty else L
    soon = taxable[taxable["long_term_on"].notna()] if not taxable.empty else taxable
    if not soon.empty:
        soon = soon[(soon["term"] == "short") & (pd.to_datetime(soon["long_term_on"]).dt.date <= today + timedelta(days=SOON_DAYS))]
    with panel("tax-soon"):
        st.subheader("Turning long-term soon")
        if soon.empty:
            st.caption(f"No taxable lots go long-term in the next {SOON_DAYS} days.")
        else:
            cards = []
            for r in soon.sort_values("long_term_on").itertuples():
                wait = (r.long_term_on - today).days
                gain = "" if r.unrealized is None or pd.isna(r.unrealized) else f'<span class="{tone(r.unrealized)}">{usd(r.unrealized, signed=True)}</span>'
                cards.append(f'<div class="sw-card"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>'
                             f'<span class="sw-weight">{wait} days</span></div><div class="sw-name">{esc(r.account)}</div>'
                             f'<div class="sw-value">{r.long_term_on:%b %d, %Y}</div>'
                             f'<div class="sw-row"><span class="flat">{r.qty:g} sh, bought {r.acquired:%b %d}</span>{gain}</div></div>')
            st.html(f'<div class="sw-cards">{"".join(cards)}</div>')
            st.caption("Selling these after the date turns a short-term gain into a long-term one.")

    with panel("tax-harvest"):
        st.subheader("Loss-harvest candidates")
        losers = taxable[taxable["unrealized"].notna() & (taxable["unrealized"] < -1)] if not taxable.empty else taxable
        if losers.empty:
            st.caption("No taxable lots are below what you paid.")
        else:
            recent = _recent_buys(rep, today)
            rows = losers.sort_values("unrealized").assign(
                wash_window=[f"bought within 30 days; wait until {recent[s]:%b %d}" if s in recent else ""
                             for s in losers.sort_values("unrealized")["symbol"]])
            st.dataframe(rows[["account", "asset", "acquired", "qty", "cost", "value", "unrealized", "term", "wash_window"]],
                         hide_index=True, width="stretch",
                         column_config={"qty": QTY, "cost": USD, "value": USD, "unrealized": USD,
                                        "wash_window": "Wash-sale window"})
            st.caption(f"Selling these would realize {usd(losers['unrealized'].sum())} of losses, which offset gains "
                       "(and up to $3,000 of income a year). Buying the same thing back within 30 days makes it a wash sale.")

    with panel("tax-realized"):
        st.subheader(f"Sales in {year}")
        yr = R[R["year"] == year] if not R.empty else R
        if yr.empty:
            st.caption("No sales this year.")
        else:
            st.dataframe(yr.sort_values("sold", ascending=False)[
                ["sold", "account", "tax", "asset", "qty", "acquired", "days", "term", "proceeds", "cost", "gain", "wash_sale"]],
                hide_index=True, width="stretch",
                column_config={"qty": QTY, "proceeds": USD, "cost": USD, "gain": USD, "wash_sale": "Wash sale?"})
            ira = yr[yr["tax"] == "ira"]
            if not ira.empty:
                st.caption(f"IRA sales ({usd(ira['gain'].sum(), signed=True)}) aren't taxed when they happen, so they're "
                           "left out of the summary above.")

    with panel("tax-lots"):
        st.subheader("Open lots")
        if L.empty:
            st.caption("No open lots.")
        else:
            st.dataframe(L.sort_values(["account", "asset", "acquired"])[
                ["account", "tax", "asset", "acquired", "qty", "cost", "value", "unrealized", "term", "long_term_on"]],
                hide_index=True, width="stretch",
                column_config={"qty": QTY, "cost": USD, "value": USD, "unrealized": USD, "long_term_on": "Long-term on"})

    st.caption("An estimate from your transaction history using first-in-first-out lots (Robinhood's and Fidelity's "
               "default), with stock splits applied. Your broker's 1099 is the record, and this isn't tax advice. Set "
               "your own rates in the PORTFOLIO_CONFIG secret: taxes: {short_term_rate: 0.24, long_term_rate: 0.15, "
               "state_rate: 0.05}." + (" " + " ".join(rep.notes) if rep.notes else ""))


def _recent_buys(rep, today) -> dict:
    """symbol -> date its wash-sale window closes, for anything bought in the last 30 days."""
    L = rep.lots
    if L.empty:
        return {}
    recent = L[L["acquired"].notna()]
    recent = recent[[(today - a).days <= 30 for a in recent["acquired"]]]
    return {s: g["acquired"].max() + timedelta(days=31) for s, g in recent.groupby("symbol")}
