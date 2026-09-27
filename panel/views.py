"""Four more ways to present the portfolio: by asset, by theme, as a star map, and as an opening crawl.

Each view takes frames from portfolio.lenses. Cards and the crawl are plain HTML (styled in
panel/assets/base.css); charts are Altair with the Star Wars palette.
"""
from __future__ import annotations

import html
import math

import altair as alt
import pandas as pd
import streamlit as st

from portfolio import lenses

UP, DOWN, FLAT = "#39FF14", "#FF3B30", "#8A8F98"
INK, DIM, YELLOW = "#E9E4CC", "#9DA3AE", "#FFE81F"


# ---------------------------------------------------------------- formatting

def usd(v, signed: bool = False, compact: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    sign = ("+" if v > 0 else "−" if v < 0 else "") if signed else ("−" if v < 0 else "")
    a = abs(v)
    if compact and a >= 1000:
        return f"{sign}${a / 1000:,.1f}k" if a < 1_000_000 else f"{sign}${a / 1_000_000:,.2f}M"
    return f"{sign}${a:,.0f}" if a >= 100 else f"{sign}${a:,.2f}"


def btc(v, price: float) -> str:
    b = v / price
    return f"{b * 1e8:,.0f} sats" if abs(b) < 0.01 else f"₿{b:,.4f}"


def pct(v, signed: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{'+' if signed and v > 0 else ''}{v:.1%}"


def tone(v) -> str:
    return "flat" if v is None or (isinstance(v, float) and math.isnan(v)) or abs(v) < 0.005 else "up" if v > 0 else "down"


def esc(s) -> str:
    return html.escape(str(s))


# ---------------------------------------------------------------- 1. by asset (the main tab)

def assets_view(assets: pd.DataFrame) -> None:
    if assets.empty:
        st.caption("No holdings yet.")
        return
    shown = assets[assets["market_value"] >= 1]
    cards = []
    for r in shown.itertuples():
        pnl = "" if pd.isna(r.unrealized) or r.asset == lenses.CASH_ASSET else (
            f'<span class="{tone(r.unrealized)}">{usd(r.unrealized, signed=True)} ({pct(r.unrealized_pct, True)})</span>')
        day = f'<span class="{tone(r.day_change)}">{usd(r.day_change, signed=True)} today</span>' if abs(r.day_change) >= 0.5 else ""
        chips = "".join(f"<span>{esc(a)}</span>" for a in r.accounts)
        cards.append(
            f'<div class="sw-card"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>'
            f'<span class="sw-weight">{pct(r.weight)}</span></div>'
            f'<div class="sw-name">{esc(r.name or r.asset_class)}</div>'
            f'<div class="sw-value">{usd(r.market_value)}</div>'
            f'<div class="sw-bar"><i style="width:{max(1.5, r.weight * 100):.1f}%"></i></div>'
            f'<div class="sw-row">{pnl}{day}</div><div class="sw-chips">{chips}</div></div>')
    st.html(f'<div class="sw-cards">{"".join(cards)}</div>')


# ---------------------------------------------------------------- 2. by theme

def themes_view(assets: pd.DataFrame, themes: pd.DataFrame, btc_price: float | None) -> None:
    if themes.empty:
        st.caption("No holdings yet.")
        return
    in_btc = st.toggle("Price in bitcoin", key="themes_btc", disabled=not btc_price,
                       help="Every value in BTC (sats below ₿0.01) at Kraken's live price")
    money = (lambda v: btc(v, btc_price)) if in_btc and btc_price else (lambda v: usd(v))
    share = lenses.bitcoin_share(assets)
    lead = themes.iloc[0]
    stats = [
        ("Bitcoin-linked", pct(share), "held in Bitcoin, Bitcoin treasuries and digital credit"),
        ("Largest theme", esc(lead["theme"]), f"{pct(lead['weight'])} of the portfolio"),
        ("Themes", str(len(themes)), f"across {len(assets[assets['market_value'] >= 1])} holdings"),
    ]
    if btc_price:
        stats.append(("Bitcoin", usd(btc_price), f"the portfolio is {btc(themes['market_value'].sum(), btc_price)}"))
    st.html('<div class="sw-stats">' + "".join(
        f'<div class="sw-stat"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
        for k, v, s in stats) + "</div>")

    strip = themes.assign(order=range(len(themes)))
    bar = alt.Chart(strip).mark_bar(height=26).encode(
        x=alt.X("weight:Q", stack="normalize", axis=None),
        color=alt.Color("theme:N", legend=None,  # the cards below name each color
                        scale=alt.Scale(domain=list(themes["theme"]), range=list(themes["theme_color"]))),
        order="order:Q",
        tooltip=[alt.Tooltip("theme:N", title="Theme"), alt.Tooltip("market_value:Q", title="Value", format="$,.2f"),
                 alt.Tooltip("weight:Q", title="Weight", format=".1%")],
    ).properties(height=34)
    st.altair_chart(bar, width="stretch")

    cards = []
    for t in themes.itertuples():
        members = sorted(zip(t.members, t.member_values), key=lambda x: -x[1])
        chips = "".join(f'<span><b>{esc(a)}</b> {money(v)}</span>' for a, v in members if v >= 1)
        pnl = "" if pd.isna(t.unrealized) else f'<span class="{tone(t.unrealized)}">{usd(t.unrealized, signed=True)} unrealized</span>'
        day = f'<span class="{tone(t.day_change)}">{usd(t.day_change, signed=True)} today</span>' if abs(t.day_change) >= 0.5 else ""
        cards.append(
            f'<div class="sw-card sw-theme" style="--c:{esc(t.theme_color)}"><div class="sw-card-top">'
            f'<span class="sw-sym">{esc(t.theme)}</span><span class="sw-weight">{pct(t.weight)}</span></div>'
            f'<div class="sw-value">{money(t.market_value)}</div>'
            f'<div class="sw-bar"><i style="width:{max(1.5, t.weight * 100):.1f}%"></i></div>'
            f'<div class="sw-row">{pnl}{day}</div><div class="sw-chips">{chips}</div></div>')
    st.html(f'<div class="sw-cards sw-cards-wide">{"".join(cards)}</div>')
    st.caption("Themes and their members are set in config/portfolio.yaml (themes). Options follow their underlying.")


# ---------------------------------------------------------------- 3. star map

def star_map(assets: pd.DataFrame, themes: pd.DataFrame, total: float) -> None:
    """The portfolio as a star system: each theme an orbit, each holding a planet sized by value,
    a halo for gain (green) or loss (red), and a moon for every extra account holding it."""
    a = assets[assets["market_value"] >= 1]
    if a.empty:
        st.caption("No holdings yet.")
        return
    tilt, gap = 0.42, 1.0
    order = [t for t in themes["theme"] if t in set(a["theme"])]
    planets, orbits, moons = [], [], []
    for i, theme in enumerate(order, start=1):
        r = 0.9 + i * gap
        orbits += [{"theme": theme, "x": x, "y": y, "i": j} for j, (x, y) in enumerate(lenses.ellipse(160, r, r * tilt))]
        members = a[a["theme"] == theme].sort_values("market_value", ascending=False)
        phase = i * 2.1
        for j, p in enumerate(members.itertuples()):
            ang = phase + j * 2 * math.pi / len(members)
            x, y = r * math.cos(ang), r * math.sin(ang) * tilt
            size = 90 + 9000 * p.weight
            planets.append({"asset": p.asset, "theme": theme, "x": x, "y": y, "size": size, "halo": size * 2.1,
                            "value": p.market_value, "weight": p.weight, "unrealized": p.unrealized,
                            "pnl": "gain" if (p.unrealized or 0) > 0 else "loss" if (p.unrealized or 0) < 0 else "flat",
                            "today": p.day_change, "held_in": ", ".join(p.accounts),
                            "label_y": y - (0.2 + 0.012 * math.sqrt(size))})
            for k in range(max(0, p.n_accounts - 1)):
                mang = ang + 0.8 + k * 0.9
                moons.append({"x": x + 0.34 * math.cos(mang), "y": y + 0.34 * math.sin(mang) * 0.8})
    R = 0.9 + len(order) * gap
    xs = alt.Scale(domain=[-R - 0.5, R + 0.5], nice=False)
    ys = alt.Scale(domain=[-(R * tilt) - 0.7, R * tilt + 0.5], nice=False)
    enc = {"x": alt.X("x:Q", scale=xs, axis=None), "y": alt.Y("y:Q", scale=ys, axis=None)}
    colors = alt.Scale(domain=list(themes["theme"]), range=list(themes["theme_color"]))
    P = pd.DataFrame(planets)
    layers = [
        alt.Chart(pd.DataFrame(orbits)).mark_line(strokeDash=[2, 4], strokeWidth=1, opacity=0.35).encode(
            **enc, order="i:Q", detail="theme:N", color=alt.Color("theme:N", scale=colors, legend=None)),
        alt.Chart(pd.DataFrame([{"x": 0, "y": 0}])).mark_circle(size=2600, color=YELLOW, opacity=0.18).encode(**enc),
        alt.Chart(pd.DataFrame([{"x": 0, "y": 0}])).mark_circle(size=900, color=YELLOW, opacity=0.95).encode(**enc),
        alt.Chart(pd.DataFrame([{"x": 0, "y": -0.42, "t": usd(total)}])).mark_text(
            color=YELLOW, fontSize=15, font="Share Tech Mono", baseline="top").encode(**enc, text="t:N"),
        alt.Chart(P).mark_circle(filled=False, strokeWidth=1.5, opacity=0.55).encode(
            **enc, size=alt.Size("halo:Q", scale=None, legend=None),
            stroke=alt.Stroke("pnl:N", scale=alt.Scale(domain=["gain", "loss", "flat"], range=[UP, DOWN, FLAT]), legend=None)),
        alt.Chart(P).mark_circle(opacity=1).encode(
            **enc, size=alt.Size("size:Q", scale=None, legend=None),
            color=alt.Color("theme:N", scale=colors, legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("asset:N", title="Holding"), alt.Tooltip("theme:N", title="Theme"),
                     alt.Tooltip("value:Q", title="Value", format="$,.2f"), alt.Tooltip("weight:Q", title="Weight", format=".1%"),
                     alt.Tooltip("unrealized:Q", title="Unrealized", format="$,.2f"),
                     alt.Tooltip("today:Q", title="Today", format="$,.2f"), alt.Tooltip("held_in:N", title="Held in")]),
        alt.Chart(P).mark_text(color=INK, fontSize=11, font="Share Tech Mono").encode(
            x=enc["x"], y=alt.Y("label_y:Q", scale=ys, axis=None), text="asset:N"),
    ]
    if moons:
        layers.append(alt.Chart(pd.DataFrame(moons)).mark_circle(size=22, color="#cfd3d8").encode(**enc))
    chart = alt.layer(*layers).properties(height=560).configure_view(stroke=None)
    st.altair_chart(chart, width="stretch")
    st.caption("Each orbit is a theme and each planet a holding, sized by value. The halo is green for a gain and "
               "red for a loss. Each moon is one more account holding it. Hover a planet for details.")


# ---------------------------------------------------------------- 4. briefing

def briefing(story: dict) -> None:
    """A Star Wars opening crawl written from today's numbers."""
    n = st.session_state.get("crawl_n", 0)
    paras = "".join(f"<p>{esc(p)}</p>" for p in story["paragraphs"])
    st.html(
        f'<div class="sw-crawl-stage" data-run="{n}">'
        '<div class="sw-crawl-intro">A short time ago in a brokerage far,<br>far away....</div>'
        '<div class="sw-crawl-logo">portfolio</div>'
        f'<div class="sw-crawl-view"><div class="sw-crawl"><div class="ep">{esc(story["episode"])}</div>'
        f'<h2>{esc(story["title"])}</h2>{paras}</div></div></div>')
    left, _ = st.columns([1, 3])
    if left.button("Play again", key="crawl_replay"):
        st.session_state["crawl_n"] = n + 1
        st.rerun()
    with st.expander("Read the briefing"):
        st.markdown(f"**{story['episode']}: {story['title']}**\n\n" + "\n\n".join(story["paragraphs"]))
