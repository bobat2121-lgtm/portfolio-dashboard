"""Portfolio dashboard. Plain on purpose: this is the data wiring; the look comes later.

    streamlit run streamlit_app.py              # your data
    streamlit run streamlit_app.py -- --demo    # made-up data from `python -m jobs.demo`
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pandas as pd
import streamlit as st

from panel.common import boot, can_sync_here, gate, live_quotes, load

if "--demo" in sys.argv:
    os.environ["DEMO"] = "1"

st.set_page_config(page_title="Portfolio", layout="wide")
boot()
gate()

from portfolio import queries, sync  # noqa: E402  (after boot: secrets must be in env first)
from portfolio.config import section  # noqa: E402
from portfolio.timeutil import utcnow  # noqa: E402

USD = st.column_config.NumberColumn(format="dollar")
PCT = st.column_config.NumberColumn(format="percent")
QTY = st.column_config.NumberColumn(format="%.6g")

# ---------------------------------------------------------------- data

try:
    accounts = load("accounts")
    stored = load("holdings")
    cash = load("cash")
except Exception:  # noqa: BLE001 - e.g. read-only login before the first sync has created the tables
    st.info("The database isn't ready yet. It fills in after the first sync runs.")
    st.stop()
wanted = frozenset(queries.wanted_quotes(stored)) if not stored.empty else frozenset()
quotes, quote_errors = live_quotes(wanted) if wanted else ({}, [])
holdings = queries.reprice(stored, quotes) if not stored.empty else stored
totals = queries.totals(holdings, cash) if not accounts.empty else None

# ---------------------------------------------------------------- header

left, right = st.columns([4, 1])
left.title("Portfolio")
with right:
    if can_sync_here():
        if st.button("Sync now", width="stretch", help="Pull every account now (asks SnapTrade to refresh too)"):
            with st.spinner("Syncing accounts…"):
                res = sync.run(trigger="manual", refresh=True)
            st.cache_data.clear()
            st.session_state["_last_sync"] = res
            st.rerun()
    elif url := section("app").get("sync_workflow_url"):
        st.link_button("Sync now (GitHub)", url, width="stretch",
                       help="Opens the sync workflow on GitHub: press 'Run workflow', then refresh here in a minute")
    if st.button("Refresh prices", width="stretch"):
        live_quotes.clear()
        st.rerun()

if res := st.session_state.pop("_last_sync", None):
    msg = " · ".join(f"{k}: {v['status']}" for k, v in res["accounts"].items()) or "no accounts synced"
    skipped = [f"{k} skipped ({v['reason']})" for k, v in res["sources"].items() if v["status"] == "skipped"]
    (st.success if res["status"] == "ok" else st.warning)(f"Sync {res['status']}: {msg}" + (
        "  \n" + "  \n".join(skipped) if skipped else ""))

if accounts.empty:
    st.info("No data yet. It appears after the first sync (GitHub Actions, or `python -m jobs.sync` locally).")
    st.stop()

last_sync = accounts["last_synced_at"].max()
if pd.notna(last_sync) and utcnow() - last_sync.to_pydatetime() > timedelta(hours=6):
    st.warning(f"Last successful sync was {last_sync:%b %d %H:%M} UTC. Check the Sync tab.")
for err in quote_errors:
    st.caption(f"Live prices partly unavailable, using broker prices: {err}")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total", f"${totals['total']:,.2f}")
c2.metric("Today", f"${totals['day_change']:,.2f}",
          f"{totals['day_change'] / (totals['total'] - totals['day_change']):.2%}" if totals["total"] else None)
c3.metric("Invested", f"${totals['invested']:,.2f}", help="What your positions are worth now (excludes cash).")
c4.metric("Cost basis", f"${totals['cost_basis']:,.2f}",
          help="What you paid for the positions you hold now."
          + (f" {totals['unknown_basis']} position(s) have no known cost and are left out."
             if totals["unknown_basis"] else ""))
c5.metric("Cash", f"${totals['cash']:,.2f}")
c6.metric("Unrealized", f"${totals['unrealized']:,.2f}",
          help=f"Invested minus cost basis. Cost basis known for {totals['basis_coverage']:.0%} of invested value.")

overview, holdings_tab, activity_tab, sync_tab = st.tabs(["Overview", "Holdings", "Activity", "Sync"])

# ---------------------------------------------------------------- overview

with overview:
    by_acct = queries.allocation(holdings, cash, by="account")
    st.subheader("Accounts")
    st.dataframe(
        accounts.merge(by_acct.rename(columns={"account": "label", "market_value": "live_value"}), on="label", how="left")
        [["label", "tax", "live_value", "weight", "cash", "day_change", "data_as_of", "last_synced_at"]],
        hide_index=True, width="stretch",
        column_config={"live_value": USD, "weight": PCT, "cash": USD, "day_change": USD},
    )
    a1, a2 = st.columns(2)
    with a1:
        st.subheader("By asset class")
        alloc = queries.allocation(holdings, cash, by="asset_class")
        st.bar_chart(alloc, x="asset_class", y="market_value", horizontal=True)
    with a2:
        st.subheader("Taxable vs IRA")
        st.bar_chart(queries.allocation(holdings, cash, by="tax"), x="tax", y="market_value", horizontal=True)

    st.subheader("Value over time")
    hist = load("value_history")
    if hist.empty:
        st.caption("History starts with your first sync; one point per day.")
    else:
        wide = hist.pivot_table(index="as_of", columns="account_key", values="value", aggfunc="sum").fillna(0)
        st.area_chart(wide)

# ---------------------------------------------------------------- holdings

with holdings_tab:
    if holdings.empty:
        st.caption("No open positions.")
    else:
        combine = st.toggle("Combine across accounts", value=True)
        view = holdings[~holdings["in_cash_balance"].astype(bool)]
        if combine:
            view = (view.groupby(["symbol", "asset_class"], as_index=False)
                    .agg(quantity=("quantity", "sum"), price=("price", "first"),
                         market_value=("market_value", "sum"), cost_basis=("cost_basis", lambda s: s.sum(min_count=len(s))),
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
        st.subheader("Cash")
        st.dataframe(cash[["account", "currency", "amount", "buying_power", "updated_at"]], hide_index=True,
                     column_config={"amount": USD, "buying_power": USD})

# ---------------------------------------------------------------- activity

with activity_tab:
    st.subheader("Detected since last sync")
    st.caption("Holdings and cash that moved between syncs. This shows buys, sells and deposits "
               "before the broker posts the transaction.")
    st.dataframe(load("changes"), hide_index=True, width="stretch",
                 column_config={"value_delta": USD, "qty_before": QTY, "qty_after": QTY})
    st.subheader("Transactions")
    st.dataframe(load("transactions"), hide_index=True, width="stretch",
                 column_config={"amount": USD, "price": USD, "fee": USD, "quantity": QTY})
    st.subheader("Contributions by month")
    contrib = load("contributions")
    if contrib.empty:
        st.caption("No deposits or withdrawals recorded yet.")
    else:
        st.bar_chart(contrib, x="month", y="amount", color="account")

# ---------------------------------------------------------------- sync health

with sync_tab:
    st.subheader("Accounts")
    st.dataframe(accounts[["key", "label", "source", "mapped", "number_mask", "data_as_of", "last_synced_at",
                           "broker_total", "total", "unpriced", "last_error"]],
                 hide_index=True, width="stretch", column_config={"broker_total": USD, "total": USD})
    if not accounts["mapped"].astype(bool).all():
        st.info("Some accounts aren't in config/portfolio.yaml. They're tracked, but add them there to name them.")
    st.subheader("Recent runs")
    runs = load("sync_runs")
    st.dataframe(runs.drop(columns=["summary"]), hide_index=True, width="stretch")
    if not runs.empty:
        with st.expander("Last run details"):
            st.json(runs.iloc[0]["summary"])
