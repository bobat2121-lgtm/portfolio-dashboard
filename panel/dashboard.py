"""The Dashboard page: ticker, headline numbers, and the tabs."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from panel import holdings as holdings_panel, theme, views
from panel.common import betas, header, history, load, panel, portfolio_data, tax_report
from portfolio import achievements, lenses, queries
from portfolio.taxes import rates as tax_rates
from portfolio.config import performance_start
from portfolio.history import rebase, summary, ytd
from portfolio.timeutil import today_ny, utcnow

USD = st.column_config.NumberColumn(format="dollar")
PCT = st.column_config.NumberColumn(format="percent")
QTY = st.column_config.NumberColumn(format="%.6g")
TABS = ["Assets", "Explore", "Briefing"]  # syncing lives in the header
EXPLORE = ["Accounts", "Themes", "Star map", "Achievements", "Positions", "Activity"]  # sub-tabs of Explore


def render() -> None:
    data = portfolio_data()
    if data is None:
        return
    accounts, holdings, cash, totals = data["accounts"], data["holdings"], data["cash"], data["totals"]
    assets, themes, btc_price = data["assets"], data["themes"], data["btc_price"]

    views.ticker(assets, btc_price, data["btc_open"], data["watch"])
    header("BTC Supernova")
    if accounts.empty:
        st.info("No data yet. It appears after the first sync (GitHub Actions, or `python -m jobs.sync` locally).")
        return
    last_sync = accounts["last_synced_at"].max()
    if pd.notna(last_sync) and utcnow() - last_sync.to_pydatetime() > timedelta(hours=6):
        st.warning(f"Last successful sync was {last_sync:%b %d %H:%M} UTC. Try Sync now at the top.")
    for err in data["quote_errors"]:
        st.caption(f"Live prices partly unavailable, using broker prices: {err}")

    base = totals["total"] - totals["day_change"]
    day_pct = totals["day_change"] / base if base else 0.0
    theme.set_mood(day_pct)                           # the scene leans rebel on up days, imperial on down days

    hist = rebase(history(holdings, cash), performance_start())
    with st.container(key="sw-metrics"):             # total value and YTD as hero tags, six readouts beside
        views.headline(totals, ytd(hist, today_ny()), btc_price, data["btc_open"], len(accounts))

    assets_tab, explore_tab, brief_tab = st.container(key="sw-body").tabs(TABS)
    with explore_tab:
        overview, themes_tab, map_tab, badges_tab, holdings_tab, activity_tab = st.container(key="sw-explore").tabs(EXPLORE)

    # the front page: the holdings, then panels that open on demand
    with assets_tab, panel("assets"):
        rep = tax_report(holdings)                   # the drawer under a card reads its lots and sales
        holdings_panel.render(assets, {
            "lots": rep.lots, "realized": rep.realized, "txns": load("all_transactions"), "prices": load("price_history"),
            "splits": load("splits"), "holdings": holdings, "cash": cash, "assets": assets,
            "since": performance_start(), "today": today_ny(), "rates": tax_rates()})
    with assets_tab, panel("performance"):
        with st.expander(views.performance_label(hist), expanded=False):
            views.performance(hist)
    with assets_tab, panel("whatif"):
        with st.expander("What if bitcoin hits…", expanded=False):
            slim = assets[["asset", "symbol", "underlying", "asset_class", "theme"]]
            views.whatif_view(assets, betas(slim), btc_price)

    with overview, panel("accounts"):
        by_acct = queries.allocation(holdings, cash, by="account")
        st.subheader("Accounts")
        st.dataframe(
            accounts.merge(by_acct.rename(columns={"account": "label", "market_value": "live_value"}), on="label", how="left")
            [["label", "tax", "live_value", "weight", "cash", "day_change", "data_as_of", "last_synced_at"]],
            hide_index=True, width="stretch",
            column_config={"live_value": USD, "weight": PCT, "cash": USD, "day_change": USD},
        )
    with overview:
        a1, a2 = st.columns(2)
        with a1, panel("asset-class"):
            st.subheader("By asset class")
            st.bar_chart(queries.allocation(holdings, cash, by="asset_class"), x="asset_class", y="market_value",
                         horizontal=True)
        with a2, panel("tax"):
            st.subheader("Taxable vs IRA")
            st.bar_chart(queries.allocation(holdings, cash, by="tax"), x="tax", y="market_value", horizontal=True)
    with overview, panel("history"):
        st.subheader("Value over time")
        daily = load("value_history")
        if daily.empty:
            st.caption("History starts with your first sync; one point per day.")
        else:
            st.area_chart(daily.pivot_table(index="as_of", columns="account_key", values="value", aggfunc="sum").fillna(0))

    with themes_tab, panel("themes"):
        st.subheader("By theme")
        views.themes_view(assets, themes, btc_price)

    with map_tab, panel("star-map"):
        st.subheader("Star map")
        views.star_map(assets, themes, totals["total"])

    with brief_tab, panel("briefing"):
        tracked = load("value_history")
        first_day = lenses.as_date(tracked["as_of"].min()) if not tracked.empty else None
        live_accounts = [a.label for a in accounts.itertuples() if (a.total or 0) >= 1]
        views.briefing(lenses.crawl(totals, assets, live_accounts, first_day, today_ny()))

    with badges_tab, panel("achievements"):
        st.subheader("Achievements")
        badges = achievements.evaluate(assets, totals, summary(hist) if not hist.empty else {}, load("all_transactions"),
                                       tax_report(holdings).lots, accounts, load("price_history"), today_ny())
        views.achievements_view(badges)

    with holdings_tab, panel("positions"):
        st.subheader("Positions")
        if holdings.empty:
            st.caption("No open positions.")
        else:
            combine = st.toggle("Combine across accounts", value=True)
            view = holdings[~holdings["in_cash_balance"].astype(bool)]
            if combine:
                view = (view.groupby(["symbol", "asset_class"], as_index=False)
                        .agg(quantity=("quantity", "sum"), price=("price", "first"),
                             market_value=("market_value", "sum"),
                             cost_basis=("cost_basis", lambda s: s.sum(min_count=len(s))),
                             day_change=("day_change", "sum"), accounts=("account", lambda s: ", ".join(sorted(set(s))))))
                view["unrealized"] = view["market_value"] - view["cost_basis"]
                view["weight"] = view["market_value"] / view["market_value"].sum()
                cols = ["symbol", "asset_class", "quantity", "price", "market_value", "weight", "day_change",
                        "cost_basis", "unrealized", "accounts"]
            else:
                cols = ["account", "symbol", "name", "asset_class", "quantity", "price", "price_source", "market_value",
                        "weight", "day_change", "cost_basis", "unrealized", "unrealized_pct"]
            st.dataframe(view.sort_values("market_value", ascending=False)[cols], hide_index=True, width="stretch",
                         column_config={"quantity": QTY, "price": USD, "market_value": USD, "weight": PCT,
                                        "day_change": USD, "cost_basis": USD, "unrealized": USD, "unrealized_pct": PCT})
            swept = holdings[holdings["in_cash_balance"].astype(bool)]
            if not swept.empty:
                st.caption("Counted inside cash already: " + ", ".join(f"{r.symbol} ({r.account})" for r in swept.itertuples()))
    if not cash.empty:
        with holdings_tab, panel("cash"):
            st.subheader("Cash")
            st.dataframe(cash[["account", "currency", "amount", "buying_power", "updated_at"]], hide_index=True,
                         column_config={"amount": USD, "buying_power": USD})

    with activity_tab, panel("changes"):
        st.subheader("Detected since last sync")
        st.caption("Holdings and cash that moved between syncs. This shows buys, sells and deposits "
                   "before the broker posts the transaction.")
        st.dataframe(load("changes"), hide_index=True, width="stretch",
                     column_config={"value_delta": USD, "qty_before": QTY, "qty_after": QTY})
    with activity_tab, panel("transactions"):
        st.subheader("Transactions")
        st.dataframe(load("transactions"), hide_index=True, width="stretch",
                     column_config={"amount": USD, "price": USD, "fee": USD, "quantity": QTY})
    with activity_tab, panel("contributions"):
        st.subheader("Contributions by month")
        contrib = load("contributions")
        if contrib.empty:
            st.caption("No deposits or withdrawals recorded yet.")
        else:
            st.bar_chart(contrib, x="month", y="amount", color="account")
