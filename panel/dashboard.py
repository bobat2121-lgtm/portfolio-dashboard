"""The Dashboard page: ticker, headline numbers, and the tabs."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from panel import theme, views
from panel.common import betas, header, history, load, panel, portfolio_data, tax_report
from portfolio import achievements, lenses, queries
from portfolio.history import summary
from portfolio.timeutil import today_ny, utcnow

USD = st.column_config.NumberColumn(format="dollar")
PCT = st.column_config.NumberColumn(format="percent")
QTY = st.column_config.NumberColumn(format="%.6g")
TABS = ["Assets", "Explore", "Briefing", "Sync"]
EXPLORE = ["Accounts", "Themes", "Star map", "Achievements", "Positions", "Activity"]  # sub-tabs of Explore


def render() -> None:
    data = portfolio_data()
    if data is None:
        return
    accounts, holdings, cash, totals = data["accounts"], data["holdings"], data["cash"], data["totals"]
    assets, themes, btc_price = data["assets"], data["themes"], data["btc_price"]

    views.ticker(assets, btc_price, data["btc_open"])
    header("Portfolio")
    if accounts.empty:
        st.info("No data yet. It appears after the first sync (GitHub Actions, or `python -m jobs.sync` locally).")
        return
    last_sync = accounts["last_synced_at"].max()
    if pd.notna(last_sync) and utcnow() - last_sync.to_pydatetime() > timedelta(hours=6):
        st.warning(f"Last successful sync was {last_sync:%b %d %H:%M} UTC. Check the Sync tab.")
    for err in data["quote_errors"]:
        st.caption(f"Live prices partly unavailable, using broker prices: {err}")

    base = totals["total"] - totals["day_change"]
    day_pct = totals["day_change"] / base if base else 0.0
    theme.set_mood(day_pct)                           # the scene leans rebel on up days, imperial on down days

    c1, c2, c3, c4, c5, c6 = st.container(key="sw-metrics").columns(6)
    c1.metric("Total", f"${totals['total']:,.2f}")
    c2.metric("Today", f"${totals['day_change']:,.2f}", f"{day_pct:.2%}" if totals["total"] else None)
    c3.metric("Cost basis", f"${totals['cost_basis']:,.2f}",
              help="What you paid for the positions you hold now."
              + (f" {totals['unknown_basis']} position(s) have no known cost and are left out."
                 if totals["unknown_basis"] else ""))
    c4.metric("Invested", f"${totals['invested']:,.2f}", help="What your positions are worth now (excludes cash).")
    c5.metric("Cash", f"${totals['cash']:,.2f}")
    c6.metric("Unrealized", f"${totals['unrealized']:,.2f}",
              help=f"Invested minus cost basis. Cost basis known for {totals['basis_coverage']:.0%} of invested value.")

    assets_tab, explore_tab, brief_tab, sync_tab = st.container(key="sw-body").tabs(TABS)
    with explore_tab:
        overview, themes_tab, map_tab, badges_tab, holdings_tab, activity_tab = st.container(key="sw-explore").tabs(EXPLORE)
    hist = history(holdings, cash)

    # the front page: the holdings, then panels that open on demand
    with assets_tab, panel("assets"):
        st.subheader("Every holding, all accounts combined")
        views.assets_view(assets)
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

    with sync_tab, panel("sync-accounts"):
        st.subheader("Accounts")
        st.dataframe(accounts[["key", "label", "source", "mapped", "number_mask", "data_as_of", "last_synced_at",
                               "broker_total", "total", "unpriced", "last_error"]],
                     hide_index=True, width="stretch", column_config={"broker_total": USD, "total": USD})
        if not accounts["mapped"].astype(bool).all():
            st.info("Some accounts aren't in config/portfolio.yaml. They're tracked, but add them there to name them.")
    with sync_tab, panel("runs"):
        st.subheader("Recent runs")
        runs = load("sync_runs")
        st.dataframe(runs.drop(columns=["summary"]), hide_index=True, width="stretch")
        if not runs.empty:
            with st.expander("Last run details"):
                st.json(runs.iloc[0]["summary"])
