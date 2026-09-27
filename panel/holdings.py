"""The front page's "every holding" panel, in four formats to trial side by side:

    Cards      one card per holding (the original), now with a bold live price
    Manifest   a terminal-style row per holding: price, 30-day pixel sparkline, value, weight, gain
    Territory  a treemap: each tile sized by value and lit by today's move
    Hero       the largest holding big, with a 60-day sparkline; the rest as compact cards
"""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from panel.views import MIN_SHOWN, esc, pct, tone, usd
from portfolio import lenses, models as m
from portfolio.pricehist import yahoo_for

FORMATS = ["Cards", "Manifest", "Territory", "Hero"]
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


def manifest(shown: pd.DataFrame, spark) -> str:
    rows = ['<div class="sw-mf-row sw-mf-head"><span>Holding</span><span>Price</span><span>30 days</span>'
            '<span>Value</span><span>Weight</span><span>Gain</span><span>Held in</span></div>']
    for r in shown.itertuples():
        rows.append(
            f'<div class="sw-mf-row"><span><b class="sw-sym">{esc(r.asset)}</b><small>{esc(r.name or r.asset_class)}</small></span>'
            f'<span>{price_tag(r) or "<span class=flat>$1.00</span>"}</span>'
            f'<span>{spark_html(spark.get(r.asset))}</span>'
            f'<span class="sw-mf-val">{usd(r.market_value)}<small class="{tone(r.day_change)}">'
            f'{usd(r.day_change, signed=True) if abs(r.day_change) >= 0.5 else ""}</small></span>'
            f'<span><div class="sw-bar"><i style="width:{max(1.5, r.weight * 100):.1f}%"></i></div><small>{pct(r.weight)}</small></span>'
            f'<span>{_pnl(r) or "<span class=flat>—</span>"}</span>'
            f'<span class="sw-chips">{_chips(r)}</span></div>')
    return f'<div class="sw-manifest">{"".join(rows)}</div>'


def _worst(row: list[float], side: float) -> float:
    s = sum(row)
    return max(max(side * side * a / (s * s), (s * s) / (side * side * a)) for a in row)


def squarify(values: list[float], w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap (Bruls et al.): rectangles (x, y, w, h) for values sorted largest first."""
    total = sum(values) or 1.0
    areas = [v * w * h / total for v in values]
    x = y = 0.0
    rects = []
    while areas:
        side = min(w, h)
        row, i = [areas[0]], 1
        while i < len(areas) and _worst(row + [areas[i]], side) <= _worst(row, side):
            row.append(areas[i])
            i += 1
        s = sum(row)
        if w >= h:
            cw, cy = s / h, y
            for a in row:
                rects.append((x, cy, cw, a / cw))
                cy += a / cw
            x, w = x + cw, w - cw
        else:
            rh, cx = s / w, x
            for a in row:
                rects.append((cx, y, a / rh, rh))
                cx += a / rh
            y, h = y + rh, h - rh
        areas = areas[i:]
    return rects


def territory(shown: pd.DataFrame, _spark) -> str:
    W, H = 240.0, 100.0                                  # the panel's shape, roughly 2.4 : 1
    rects = squarify(list(shown["market_value"]), W, H)
    tiles = []
    for (x, y, w, h), r in zip(rects, shown.itertuples()):
        c = day_pct(r) if r.asset != lenses.CASH_ASSET else 0.0
        heat = max(-1.0, min(1.0, (c or 0.0) / 0.03))
        bg = (f"rgba(57,255,20,{0.1 + 0.35 * heat:.2f})" if heat > 0.02 else
              f"rgba(255,59,48,{0.1 + 0.35 * -heat:.2f})" if heat < -0.02 else "rgba(138,143,152,.14)")
        small = w * h < 700
        body = (f'<b class="sw-sym">{esc(r.asset)}</b>'
                + ("" if small else f'{price_tag(r, big=w > 60)}<span class="v">{usd(r.market_value)}</span>'
                   f'<span class="w">{pct(r.weight)} · {_pnl(r) or "cash"}</span>'))
        tiles.append(f'<div class="sw-tile" style="left:{x / W * 100:.3f}%;top:{y / H * 100:.3f}%;'
                     f'width:{w / W * 100:.3f}%;height:{h / H * 100:.3f}%;background:{bg}">{body}</div>')
    return (f'<div class="sw-territory">{"".join(tiles)}</div>'
            '<div class="sw-legend">Tile size = value · color = today\'s move (green up, red down, grey flat)</div>')


def hero(shown: pd.DataFrame, spark) -> str:
    top, rest = shown.iloc[0], shown.iloc[1:]
    r = next(shown.head(1).itertuples())
    lead = (f'<div class="sw-hero"><div class="l"><div class="sw-card-top"><span class="sw-sym">{esc(top["asset"])}</span>'
            f'<span class="sw-weight">{pct(top["weight"])} of portfolio</span></div>'
            f'<div class="sw-name">{esc(top["name"] or top["asset_class"])}</div>{price_tag(r, big=True)}'
            f'<div class="sw-value">{usd(top["market_value"])}</div><div class="sw-row">{_pnl(r)}{_day(r)}</div>'
            f'<div class="sw-chips">{_chips(r)}</div></div>'
            f'<div class="r">{spark_html(spark.get(top["asset"]), wide=True)}<div class="cap">last 60 days</div></div></div>')
    small = []
    for r in rest.itertuples():
        small.append(f'<div class="sw-card sw-mini"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>'
                     f'<span class="sw-weight">{pct(r.weight)}</span></div>{price_tag(r) or "<span class=flat>cash</span>"}'
                     f'<div class="sw-value">{usd(r.market_value)}</div><div class="sw-row">{_pnl(r)}</div></div>')
    return lead + f'<div class="sw-cards">{"".join(small)}</div>'


RENDER = {"Cards": (cards, 0), "Manifest": (manifest, 30), "Territory": (territory, 0), "Hero": (hero, 60)}


def render(assets: pd.DataFrame, prices: pd.DataFrame) -> None:
    """The panel: heading, a format switcher for the trial, then the chosen format."""
    left, right = st.columns([1, 1.15], vertical_alignment="bottom")
    left.subheader("Every holding, all accounts combined")
    with right:
        fmt = st.segmented_control("Format", FORMATS, default="Cards", key="holdings_format",
                                   label_visibility="collapsed") or "Cards"
    shown = assets[assets["market_value"] > MIN_SHOWN]
    if shown.empty:
        st.caption("No holdings over $100 yet.")
        return
    fn, days = RENDER[fmt]
    st.html(fn(shown, sparks(shown, prices, days) if days else {}))
