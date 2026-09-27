"""The front page's "every holding" panel: the Cards format, plus four card variations to trial.

    Cards      the original: symbol and bold price, value, weight bar, gain, accounts
    Big price  the price is the headline; value and gain in a footer
    Trend      the original plus a 30-day pixel sparkline
    Cost       average cost to today's price, a paid / gain bar, and the gain
    Holo       a cockpit instrument: ring gauge of portfolio share beside the readouts
"""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from panel.views import MIN_SHOWN, esc, pct, tone, usd
from portfolio import lenses, models as m
from portfolio.pricehist import yahoo_for

FORMATS = ["Cards", "Big price", "Trend", "Cost", "Holo"]
ARROW = {"up": "▲", "down": "▼", "flat": "■"}


def price_fmt(v) -> str:
    """$84,400 for big prices, $158.6 above $100, $29.44 above $1, $0.786 below."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    a = abs(v)
    if a >= 1000:
        return f"${v:,.0f}"
    if a >= 100:
        return f"${v:,.1f}"
    if a >= 1:
        return f"${v:,.2f}"
    return f"${v:,.3f}"


def day_pct(r) -> float | None:
    prev = r.market_value - r.day_change
    return r.day_change / prev if prev else None


def price_tag(r, big: bool = False) -> str:
    """Bold price plus today's move, e.g.  $158.6 ▼0.28%"""
    if r.asset == lenses.CASH_ASSET or r.price is None or pd.isna(r.price):
        return ""
    c = day_pct(r)
    move = "" if c is None else f'<em class="{tone(c * 100)}">{ARROW[tone(c * 100)]}{abs(c):.2%}</em>'
    return f'<span class="sw-price{" big" if big else ""}">{price_fmt(float(r.price))}{move}</span>'


def sparks(assets: pd.DataFrame, prices: pd.DataFrame, days: int) -> dict[str, list[float]]:
    """asset -> the last `days` daily closes (options use nothing; cash has none)."""
    out: dict[str, list[float]] = {}
    if prices.empty:
        return out
    for r in assets.itertuples():
        if r.asset_class in (m.OPTION, m.CASH) or r.asset == lenses.CASH_ASSET:
            continue
        ysym = yahoo_for(r.symbol, r.asset_class in (m.CRYPTO, m.STABLECOIN))
        s = prices[prices["symbol"] == ysym].sort_values("date")["close"].tail(days)
        if len(s) >= 5:
            out[r.asset] = [float(x) for x in s]
    return out


def spark_html(series: list[float] | None, wide: bool = False) -> str:
    if not series:
        return '<div class="sw-spark empty"></div>'
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    cls = "up" if series[-1] >= series[0] else "down"
    bars = "".join(f'<i style="height:{12 + 88 * (v - lo) / span:.0f}%"></i>' for v in series)
    chg = series[-1] / series[0] - 1 if series[0] else 0.0
    return (f'<div class="sw-spark {cls}{" wide" if wide else ""}" title="{len(series)} days: {chg:+.1%}">{bars}</div>')


def _pnl(r) -> str:
    if pd.isna(r.unrealized) or r.asset == lenses.CASH_ASSET:
        return ""
    return f'<span class="{tone(r.unrealized)}">{usd(r.unrealized, signed=True)} ({pct(r.unrealized_pct, True)})</span>'


def _day(r) -> str:
    return f'<span class="{tone(r.day_change)}">{usd(r.day_change, signed=True)} today</span>' if abs(r.day_change) >= 0.5 else ""


def _chips(r) -> str:
    return "".join(f"<span>{esc(a)}</span>" for a in r.accounts)


# ---------------------------------------------------------------- formats

def cards(shown: pd.DataFrame, _spark) -> str:
    out = []
    for r in shown.itertuples():
        out.append(
            f'<div class="sw-card"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>{price_tag(r)}</div>'
            f'<div class="sw-name">{esc(r.name or r.asset_class)}</div>'
            f'<div class="sw-value">{usd(r.market_value)}</div>'
            f'<div class="sw-bar"><i style="width:{max(1.5, r.weight * 100):.1f}%"></i></div>'
            f'<div class="sw-row"><span class="flat">{pct(r.weight)} of portfolio</span></div>'
            f'<div class="sw-row">{_pnl(r)}{_day(r)}</div><div class="sw-chips">{_chips(r)}</div></div>')
    return f'<div class="sw-cards">{"".join(out)}</div>'


def _avg_cost(r) -> float | None:
    if pd.isna(r.cost_basis) or not r.quantity:
        return None
    return float(r.cost_basis) / (float(r.quantity) * (100.0 if r.asset_class == m.OPTION else 1.0))


def big_price(shown: pd.DataFrame, _spark) -> str:
    """The price is the headline; value and gain sit in a footer."""
    out = []
    for r in shown.itertuples():
        c = day_pct(r) or 0.0
        if r.asset == lenses.CASH_ASSET:
            head, move = '<div class="sw-bp">cash</div>', '<div class="sw-bp-move flat">balances and sweep funds</div>'
        else:
            head = f'<div class="sw-bp">{price_fmt(float(r.price))}</div>'
            extra = " · " + usd(r.day_change, signed=True) if abs(r.day_change) >= 0.5 else ""
            move = f'<div class="sw-bp-move {tone(c * 100)}">{ARROW[tone(c * 100)]} {abs(c):.2%} today{extra}</div>'
        out.append(
            f'<div class="sw-card sw-v-bp"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>'
            f'<span class="sw-weight">{pct(r.weight)}</span></div>{head}{move}'
            f'<div class="sw-bp-foot"><span><small>value</small>{usd(r.market_value)}</span>'
            f'<span><small>gain</small>{_pnl(r) or "—"}</span></div><div class="sw-chips">{_chips(r)}</div></div>')
    return f'<div class="sw-cards">{"".join(out)}</div>'


def trend(shown: pd.DataFrame, spark) -> str:
    """The original card plus a 30-day pixel sparkline."""
    out = []
    for r in shown.itertuples():
        series = spark.get(r.asset)
        if series and series[0]:
            chg = f'<span class="{tone(series[-1] - series[0])}">30 days {series[-1] / series[0] - 1:+.1%}</span>'
        elif r.asset == lenses.CASH_ASSET:
            chg = '<span class="flat">held as cash</span>'
        else:
            chg = '<span class="flat">no price history</span>'
        out.append(
            f'<div class="sw-card"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>{price_tag(r)}</div>'
            f'<div class="sw-name">{esc(r.name or r.asset_class)}</div>'
            f'<div class="sw-value">{usd(r.market_value)} <small class="flat">{pct(r.weight)}</small></div>'
            f'{"" if r.asset == lenses.CASH_ASSET else spark_html(series)}<div class="sw-row">{chg}</div>'
            f'<div class="sw-row">{_pnl(r)}{_day(r)}</div></div>')
    return f'<div class="sw-cards">{"".join(out)}</div>'


def cost(shown: pd.DataFrame, _spark) -> str:
    """What you paid against what it's worth: average cost to price, and a paid / gain bar."""
    out = []
    for r in shown.itertuples():
        avg = _avg_cost(r)
        if r.asset == lenses.CASH_ASSET or avg is None:
            body = (f'<div class="sw-value">{usd(r.market_value)}</div>'
                    '<div class="sw-row"><span class="flat">cash: nothing to gain or lose</span></div>')
        else:
            paid, worth = float(r.cost_basis), float(r.market_value)
            top = max(paid, worth) or 1.0
            cls = "up" if worth >= paid else "down"
            body = (f'<div class="sw-cost">avg {price_fmt(avg)} <b>→</b> {price_fmt(float(r.price))}</div>'
                    f'<div class="sw-split"><i class="paid" style="width:{min(paid, worth) / top * 100:.1f}%"></i>'
                    f'<i class="{cls}" style="width:{abs(worth - paid) / top * 100:.1f}%"></i></div>'
                    f'<div class="sw-row"><span class="flat">paid {usd(paid)}</span><span>worth {usd(worth)}</span></div>'
                    f'<div class="sw-gain {tone(r.unrealized)}">{usd(r.unrealized, signed=True)} '
                    f'<small>{pct(r.unrealized_pct, True)}</small></div>')
        out.append(f'<div class="sw-card"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>'
                   f'{price_tag(r)}</div>{body}</div>')
    return f'<div class="sw-cards">{"".join(out)}</div>'


def holo(shown: pd.DataFrame, _spark) -> str:
    """A cockpit instrument: a ring gauge of the holding's share of the portfolio beside its readouts."""
    out = []
    for r in shown.itertuples():
        w = max(0.5, float(r.weight) * 100)
        price = price_tag(r) or '<span class="sw-price">cash</span>'
        out.append(
            f'<div class="sw-card sw-v-holo"><div class="sw-ringbox"><div class="sw-ring" style="--p:{w:.1f}"></div>'
            f'<span class="sw-ring-n">{r.weight:.0%}</span></div>'
            f'<div class="sw-holo-read"><span class="sw-sym">{esc(r.asset)}</span>{price}'
            f'<div class="sw-value">{usd(r.market_value)}</div><div class="sw-row">{_pnl(r)}</div></div></div>')
    return f'<div class="sw-cards sw-cards-wide">{"".join(out)}</div>'


RENDER = {"Cards": (cards, 0), "Big price": (big_price, 0), "Trend": (trend, 30), "Cost": (cost, 0), "Holo": (holo, 0)}


def render(assets: pd.DataFrame, prices: pd.DataFrame) -> None:
    """The panel: heading, a format switcher for the trial, then the chosen format."""
    st.subheader("Every holding, all accounts combined")
    fmt = st.segmented_control("Format", FORMATS, default="Cards", key="holdings_format",
                               label_visibility="collapsed") or "Cards"
    shown = assets[assets["market_value"] > MIN_SHOWN]
    if shown.empty:
        st.caption("No holdings over $100 yet.")
        return
    fn, days = RENDER[fmt]
    st.html(fn(shown, sparks(shown, prices, days) if days else {}))
