"""The drawer that opens under a holding card: the position at a glance, the option contract (for
options), the price with your buys and sells on it, every purchase tranche, and what selling would mean
for taxes. Cash gets where it sits instead. Data from portfolio/detail.py; styles are the .sw-dr rules in
panel/assets/base.css.
"""
from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from panel.holdings import _title, price_fmt
from panel.views import esc, pct, tone, usd
from portfolio import detail, lenses, models as m
from portfolio.pricehist import yahoo_for

BLUE, YELLOW, GREEN, RED, ORANGE, DIM = "#4BD5EE", "#FFE81F", "#39FF14", "#FF3B30", "#F26B1D", "#9DA3AE"
RANGES = ["YTD", "3M", "6M", "1Y", "All"]  # the chart opens on YTD


def close() -> None:
    st.session_state["sw_open"] = None


def _d(d) -> str:
    return "—" if d is None or pd.isna(d) else f"{d:%b} {d.day}, {d:%Y}"


def _cell(label: str, value: str, sub: str = "", cls: str = "", tip: str = "") -> str:
    attr = f' title="{esc(tip)}"' if tip else ""
    return (f'<div class="sw-st"{attr}><span class="k{" tip" if tip else ""}">{label}</span>'
            f'<span class="v {cls}">{value}</span><span class="s">{sub or "&nbsp;"}</span></div>')


def _section(title: str, extra: str = "") -> str:
    return f'<div class="sw-dr-k"><b>{title}</b>{f"<i>{extra}</i>" if extra else ""}</div>'


def _qty(q: float) -> str:
    return f"{q:,.0f}" if abs(q - round(q)) < 1e-6 else f"{q:,.4f}".rstrip("0")


# ---------------------------------------------------------------- sections

def _summary(r, s: dict, since: date) -> str:
    """One line of six: shares, avg cost, value, paid, unrealized, total return."""
    opt = r.asset_class == m.OPTION
    unreal = s["unrealized"]
    cells = [
        _cell("Contracts" if opt else "Shares", _qty(s["shares"]), f"{_qty(s['shares'] * 100)} shares" if opt else ""),
        _cell("Avg cost", price_fmt(s["avg_cost"]), "per share"),
        _cell("Value", usd(s["value"]), "now"),
        _cell("Paid", usd(s["paid"]) if s["paid"] is not None else "—", "cost basis"),
        _cell("Unrealized", usd(unreal, signed=True) if unreal is not None else "—",
              pct(unreal / s["paid"], True) if unreal is not None and s["paid"] else "", tone(unreal)),
        _cell("Total return", usd(s["total"], signed=True),
              pct(s["total"] / s["paid"], True) + " of paid" if s["paid"] else "", tone(s["total"]),
              tip=f"Unrealized, plus what you've made or lost selling it ({usd(s['realized'], signed=True)}) and its "
                  f"dividends and interest ({usd(s['income'])}) since {since:%b} {since.day}, {since:%Y}."),
    ]
    return _section("The position") + f'<div class="sw-dr-cells one">{"".join(cells)}</div>'


def _option(o: dict) -> str:
    kind = "Call" if o["call"] else "Put"
    money = o["money"]
    where = "—" if money is None else (f"{abs(money):.1%} in the money" if money > 0 else f"{abs(money):.1%} out of the money")
    cells = [
        _cell("Contract", f"{kind} ${o['strike']:g}", f"on {o['underlying']}"),
        _cell("Expires", _d(o["expiry"]), f"{o['days']:,} days left" if o["days"] >= 0 else "expired",
              "down" if o["days"] < 30 else ""),
        _cell("Paid", price_fmt(o["premium"]), f"per share · {usd(o['paid'])} total" if o["paid"] else "per share"),
        _cell("Break-even", price_fmt(o["breakeven"]),
              f"{o['underlying']} {pct(o['move'], True)} from {price_fmt(o['spot'])}" if o["move"] is not None else "at expiry",
              tip="Where the underlying has to be at expiry for the contract to be worth what you paid."),
        _cell(o["underlying"], price_fmt(o["spot"]), where, "up" if money and money > 0 else ""),
        _cell("Intrinsic", price_fmt(o["intrinsic"]), "what it's worth exercised now",
              tip="Call: underlying minus strike, if positive. Put: strike minus underlying."),
        _cell("Time value", price_fmt(o["time_value"]),
              f"{o['time_value'] / o['price']:.0%} of its price" if o["price"] and o["time_value"] is not None else "",
              tip="The part of the price that's paying for time left, not value today. It shrinks toward expiry."),
    ]
    return _section("The contract") + f'<div class="sw-dr-cells">{"".join(cells)}</div>'


def _tranches(lots: pd.DataFrame, mult: float) -> str:
    top = max(1.0, float(lots["unrealized"].abs().max() or 1.0))
    rows = []
    for t in lots.itertuples():
        g = t.unrealized
        bar = "" if pd.isna(g) else f'<i class="{tone(g)}" style="width:{abs(g) / top * 100:.0f}%"></i>'
        if t.tax == "ira":
            term = '<span class="flat">IRA</span>'
        elif t.term == "long":
            term = '<span class="up">long</span>'
        else:
            term = f'short <small>long {_d(t.long_term_on)}</small>'
        rows.append(
            f"<tr><td>{_d(t.acquired)}</td><td>{esc(t.account)}</td><td>{_qty(t.qty)}</td>"
            f"<td>{price_fmt(t.paid)}</td><td>{usd(t.cost)}</td><td>{usd(t.value)}</td>"
            f'<td class="g"><span class="{tone(g)}">{usd(g, signed=True)}</span>'
            f'<small class="{tone(g)}">{pct(t.gain_pct, True)}</small><b class="sw-dr-bar">{bar}</b></td>'
            f"<td>{t.days:,}d</td><td>{term}</td></tr>")
    unit = "Contracts" if mult > 1 else "Shares"
    head = "".join(f"<th>{h}</th>" for h in ("Bought", "Account", unit, "Paid", "Cost", "Now", "Gain", "Held", "Term"))
    return f'<div class="sw-dr-lots"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _taxes(t: dict, lots: pd.DataFrame, pr: dict, since: date) -> str:
    r = t["rates"]
    nxt = t["next"]
    cells = [
        _cell("Short-term", usd(t["short"], signed=True), "held a year or less", tone(t["short"])),
        _cell("Long-term", usd(t["long"], signed=True), "held over a year", tone(t["long"])),
        _cell("If sold today", "~" + usd(t["bill"]),
              f"{r.get('short_term_rate', 0):.0%} / {r.get('long_term_rate', 0):.0%} rates",
              tip="A rough federal bill on this holding's unrealized gains in taxable accounts, with losses of one "
                  "term offsetting gains of the other. Set your rates in the PORTFOLIO_CONFIG secret."),
        _cell("Next long-term", _d(nxt["on"]) if nxt else "—",
              f"{nxt['days']} days · {_qty(nxt['qty'])} sh" if nxt else "nothing waiting"),
        _cell("Loss lots", str(t["loss_lots"]), usd(t["loss"], signed=True) + " to harvest" if t["loss_lots"] else "none",
              "down" if t["loss_lots"] else ""),
        _cell("Portfolio realized", usd(pr["total"], signed=True), f"taxable {pr['year']}: {usd(pr['taxable_year'], signed=True)}",
              tone(pr["total"]),
              tip=f"Every sale across the whole portfolio since {since:%b} {since.day}, {since:%Y} ({pr['sales']} lots): "
                  f"the total gain or loss. Under it, this year's in taxable accounts, the part that goes on this "
                  f"year's return."),
    ]
    notes = []
    if t["wash_until"]:
        notes.append(f'<span class="warn">▲ Wash-sale window:</span> you bought on {_d(t["last_buy"])}. Selling other '
                     f"shares at a loss on or before {_d(t['wash_until'])} defers the loss (IRA buys count too).")
    if t["ira_qty"]:
        notes.append(f"{_qty(t['ira_qty'])} shares ({usd(t['ira_value'])}) sit in the IRA: no tax when they're sold, "
                     "so they're left out above.")
    if lots.empty:
        notes.append("No purchases since the start, so there are no tax lots to show.")
    note_html = "".join(f'<div class="sw-dr-note">{n}</div>' for n in notes)
    return _section("Taxes") + f'<div class="sw-dr-cells one">{"".join(cells)}</div>{note_html}'


def _cash(r, ctx) -> None:
    by = detail.cash_by_account(ctx["holdings"], ctx["cash"])
    total = float(by["amount"].sum()) if not by.empty else 0.0
    rows = "".join(
        f'<div class="sw-dr-acct"><span>{esc(a.account)} <small>{a.amount / total:.1%}</small></span><b>{usd(a.amount)}</b>'
        f'<div class="sw-bar"><i style="width:{max(1.5, a.amount / total * 100):.1f}%"></i></div></div>'
        for a in by.itertuples()) if total else ""
    st.html(_head(r, ctx["since"]) + _section("Where it sits")
            + f'<div class="sw-dr-accts">{rows or "<div class=sw-dr-note>No cash right now.</div>"}</div>')


def _head(r, since: date) -> str:
    sym, line, _full = _title(r)                 # the card's own title: MSTR / Strategy, ASST / $35 call Jan '28
    return (f'<div class="sw-dr-head"><span class="sw-sym">{esc(sym)}</span><span class="sw-dr-name">{esc(line)}</span>'
            f'<span class="sw-dr-since">since {since:%b} {since.day}, {since:%Y}</span></div>')


# ---------------------------------------------------------------- the chart

def _series(r, ctx, sym: str, crypto: bool, live: float | None) -> pd.DataFrame:
    ysym = yahoo_for(sym, crypto)
    ph = ctx["prices"]
    s = ph[ph["symbol"] == ysym][["date", "close"]].copy() if ysym and not ph.empty else pd.DataFrame(columns=["date", "close"])
    s["date"] = pd.to_datetime(s["date"])
    s = s[s["date"] > pd.Timestamp(ctx["since"])].sort_values("date")
    today = pd.Timestamp(ctx["today"])
    if live and (s.empty or s["date"].iloc[-1] < today):
        s = pd.concat([s, pd.DataFrame({"date": [today], "close": [live]})], ignore_index=True)
    return s


def _chart(px: pd.DataFrame, marks: pd.DataFrame, lines: list[tuple]) -> alt.LayerChart:
    """lines: (value, color, label, label_dy); a negative dy puts the label above its line."""
    long_span = (px["date"].max() - px["date"].min()).days > 120          # past ~4 months, show the year
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%b '%y" if long_span else "%b %d", labelAngle=0, tickCount=6))
    lo = min([px["close"].min()] + [ln[0] for ln in lines] + (list(marks["price"].dropna()) if not marks.empty else []))
    hi = max([px["close"].max()] + [ln[0] for ln in lines] + (list(marks["price"].dropna()) if not marks.empty else []))
    pad = (hi - lo) * 0.08 or hi * 0.05
    y = alt.Y("close:Q", title=None, scale=alt.Scale(domain=[lo - pad, hi + pad], nice=False), axis=alt.Axis(format="$,.2~f"))
    area = alt.Chart(px).mark_area(color=alt.Gradient(
        gradient="linear", x1=1, x2=1, y1=1, y2=0,
        stops=[alt.GradientStop(color="rgba(75,213,238,0)", offset=0), alt.GradientStop(color="rgba(75,213,238,.22)", offset=1)]),
        interpolate="monotone").encode(x=x, y=y)
    line = alt.Chart(px).mark_line(color=BLUE, strokeWidth=2, interpolate="monotone").encode(
        x=x, y=y, tooltip=[alt.Tooltip("date:T", title="Date", format="%b %d, %Y"), alt.Tooltip("close:Q", title="Close", format="$,.2f")])
    layers = [area, line]
    for value, color, label, dy in lines:
        d = pd.DataFrame({"close": [value], "label": [label]})
        layers.append(alt.Chart(d).mark_rule(color=color, strokeDash=[5, 4], strokeWidth=1.4).encode(y=alt.Y("close:Q")))
        layers.append(alt.Chart(d).mark_text(color=color, align="left", dx=4, dy=dy, fontSize=11, font="Share Tech Mono")
                      .encode(y=alt.Y("close:Q"), x=alt.value(0), text="label:N"))
    if not marks.empty:
        mk = marks.assign(date=pd.to_datetime(marks["date"]), close=marks["price"])
        layers.append(alt.Chart(mk).mark_circle(opacity=0.9, stroke="#04050B", strokeWidth=1.2).encode(
            x=x, y=alt.Y("close:Q"),
            color=alt.Color("side:N", scale=alt.Scale(domain=[m.BUY, m.SELL], range=[GREEN, RED]), legend=None),
            size=alt.Size("amount:Q", scale=alt.Scale(range=[140, 900]), legend=None),
            tooltip=[alt.Tooltip("date:T", title="Date", format="%b %d, %Y"), alt.Tooltip("side:N", title="Trade"),
                     alt.Tooltip("qty:Q", title="Shares", format=",.4~f"), alt.Tooltip("price:Q", title="Price", format="$,.2f"),
                     alt.Tooltip("amount:Q", title="Amount", format="$,.0f")]))
    return (alt.layer(*layers).properties(height=250)
            .configure(background="transparent", font="Share Tech Mono")
            .configure_axis(labelColor=DIM, labelFont="Share Tech Mono", labelFontSize=11, gridColor="rgba(138,143,152,.12)",
                            domain=False, tickColor="rgba(138,143,152,.25)")
            .configure_view(strokeWidth=0))


def _price_section(r, ctx, s: dict, opt: dict | None, marks: pd.DataFrame) -> None:
    crypto = r.asset_class in (m.CRYPTO, m.STABLECOIN)
    if opt:
        px = _series(r, ctx, opt["underlying"], False, opt["spot"])
        lines = [(opt["strike"], ORANGE, f"strike {price_fmt(opt['strike'])}", 12)]          # label under its line
        if opt["breakeven"]:
            lines.append((opt["breakeven"], YELLOW, f"break-even {price_fmt(opt['breakeven'])}", -7))
        title, marks = f"{opt['underlying']} vs your strike", marks.iloc[0:0]
    else:
        px = _series(r, ctx, r.symbol, crypto, float(r.price) if r.price is not None and pd.notna(r.price) else None)
        lines = [(s["avg_cost"], YELLOW, f"avg cost {price_fmt(s['avg_cost'])}", -7)] if s["avg_cost"] else []
        title = "Price & your trades"
    if px.empty:
        st.html(_section(title) + '<div class="sw-dr-note">No price history for this one yet.</div>')
        return
    st.html(_section(title, '<span class="up">●</span> bought  <span class="down">●</span> sold, sized by amount'
                     if not marks.empty else ""))
    pick = st.segmented_control("Range", RANGES, default="YTD", key="sw-drawer-range", label_visibility="collapsed") or "YTD"
    today = ctx["today"]
    start = {"YTD": date(today.year, 1, 1), "3M": today - timedelta(days=91), "6M": today - timedelta(days=182),
             "1Y": today - timedelta(days=365)}.get(pick)
    if start:
        px = px[px["date"] >= pd.Timestamp(start)]
        marks = marks[pd.to_datetime(marks["date"]) >= pd.Timestamp(start)] if not marks.empty else marks
    if px.empty:
        st.html('<div class="sw-dr-note">No prices in that range.</div>')
        return
    st.altair_chart(_chart(px, marks, lines), width="stretch")


# ---------------------------------------------------------------- the drawer

def render(r, ctx: dict) -> None:
    st.button("✕", key="sw-drawer-close", on_click=close, help="Close")
    if r.asset == lenses.CASH_ASSET:
        _cash(r, ctx)
        return
    since, today = ctx["since"], ctx["today"]
    syms = detail.symbols(r, ctx["lots"])
    mult = detail.multiplier(r)
    lots = detail.tranches(ctx["lots"], r, since)
    tr = detail.trades(ctx["txns"], syms, since, ctx["splits"], mult)
    s = detail.summary(r, lots, ctx["realized"], ctx["txns"], syms, since, today)
    opt = None
    if r.asset_class == m.OPTION:
        under = ctx["assets"][ctx["assets"]["asset"] == r.underlying]
        spot = float(under["price"].iloc[0]) if not under.empty and pd.notna(under["price"].iloc[0]) else None
        opt = detail.option(r, spot, today)
    st.html(_head(r, since) + _summary(r, s, since) + (_option(opt) if opt else ""))
    _price_section(r, ctx, s, opt, detail.by_day(tr))
    if lots.empty:
        st.html(_section("Purchase tranches") + '<div class="sw-dr-note">No purchases since the start.</div>')
    else:
        with st.expander(f"Purchase tranches · {len(lots)} open · oldest first", expanded=False):   # starts folded
            st.html(_tranches(lots, mult))
    st.html(_taxes(detail.taxes(lots, tr, ctx["rates"], today), lots,
                   detail.portfolio_realized(ctx["realized"], since, today), since))
