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
MIN_SHOWN = 100  # the asset cards and the ticker skip holdings worth $100 or less (dust like $1 of STRC)
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


# (the front page's holdings panel lives in panel/holdings.py)


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
        '<div class="sw-crawl-logo">btc supernova</div>'
        f'<div class="sw-crawl-view"><div class="sw-crawl"><div class="ep">{esc(story["episode"])}</div>'
        f'<h2>{esc(story["title"])}</h2>{paras}</div></div></div>')
    left, _ = st.columns([1, 3])
    if left.button("Play again", key="crawl_replay"):
        st.session_state["crawl_n"] = n + 1
        st.rerun()
    with st.expander("Read the briefing"):
        st.markdown(f"**{story['episode']}: {story['title']}**\n\n" + "\n\n".join(story["paragraphs"]))


# ---------------------------------------------------------------- performance: you vs BTC vs S&P, value vs money in

def performance_label(hist) -> str:
    """The Performance panel's title, with the headline so it says something while collapsed."""
    from portfolio.history import summary

    if hist is None or hist.empty:
        return "Performance"
    s = summary(hist)
    beats = [n for k, n in (("btc", "bitcoin"), ("spy", "the S&P 500")) if s.get(k) is not None and s["value"] >= s[k]]
    lead = f"ahead of {' and '.join(beats)}" if beats else "behind bitcoin and the S&P 500"
    label = f"Performance · {usd(s['gain'], signed=True)} on {usd(s['net_in'])} put in · {lead}"
    return label.replace("$", r"\$")  # labels are Markdown: a pair of $ would render as math


def performance(hist) -> None:
    from portfolio.history import summary

    if hist is None or hist.empty or len(hist.daily) < 2:
        st.caption("Performance appears once there's transaction history and the first price backfill has run.")
        return
    s = summary(hist)
    d = hist.daily.reset_index()
    d["date"] = pd.to_datetime(d["date"])
    since = s["since"].strftime("%b %d, %Y") if s.get("since") else "the start"
    gain_pct = s["gain"] / s["net_in"] if s["net_in"] > 0 else None
    stats = [("Money in (net)", usd(s["net_in"]), f"its value on {since}, plus deposits minus withdrawals since"),
             ("Your gain", usd(s["gain"], signed=True), f"{pct(gain_pct, True)} on money in" if gain_pct is not None else "")]
    for key, name in (("btc", "Same money in bitcoin"), ("spy", "Same money in S&P 500")):
        if s.get(key) is not None:
            ahead = s["value"] - s[key]
            stats.append((name, usd(s[key]), f"you're {usd(abs(ahead))} {'ahead' if ahead >= 0 else 'behind'}"))
    st.html('<div class="sw-stats">' + "".join(
        f'<div class="sw-stat"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{esc(x)}</div></div>'
        for k, v, x in stats) + "</div>")

    st.subheader("You vs Bitcoin vs the market")  # Star Jedi has no "&"
    long = d.melt(id_vars="date", value_vars=["value", "btc", "spy"], var_name="series", value_name="usd").dropna()
    names = ["You", "Same money in BTC", "Same money in S&P 500"]
    long["series"] = long["series"].map(dict(zip(["value", "btc", "spy"], names)))
    lines = alt.Chart(long).mark_line(strokeWidth=2).encode(
        x=alt.X("date:T", title=None), y=alt.Y("usd:Q", title=None, axis=alt.Axis(format="$,.0f")),
        color=alt.Color("series:N", scale=alt.Scale(domain=names, range=[YELLOW, "#F7931A", "#4BD5EE"]),
                        legend=alt.Legend(orient="bottom", title=None)),
        strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=names, range=[[1, 0], [5, 3], [5, 3]]), legend=None),
        tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("series:N", title="Line"),
                 alt.Tooltip("usd:Q", title="Value", format="$,.0f")],
    ).properties(height=320)
    st.altair_chart(lines, width="stretch")
    st.caption("The same deposits, on the same days, put into BTC or an S&P 500 fund (SPY, dividends reinvested) instead.")

    st.subheader("Value vs money in")
    base = alt.Chart(d).encode(x=alt.X("date:T", title=None))
    money_in = base.mark_area(opacity=0.35, color=FLAT, interpolate="step-after").encode(
        y=alt.Y("net_in:Q", title=None, axis=alt.Axis(format="$,.0f")),
        tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("net_in:Q", title="Money in", format="$,.0f"),
                 alt.Tooltip("value:Q", title="Value", format="$,.0f")])
    value = base.mark_line(color=YELLOW, strokeWidth=2).encode(y="value:Q")
    st.altair_chart(alt.layer(money_in, value).properties(height=260), width="stretch")
    st.caption("Grey is what you've put in; yellow is what it's worth. The gap is your gain. Rebuilt from your "
               "transactions and daily closes." + (" " + " ".join(hist.notes) if hist.notes else ""))


# ---------------------------------------------------------------- what if BTC hits $X

def whatif_view(assets: pd.DataFrame, b: dict, btc_now: float | None) -> None:
    from portfolio import whatif

    if not btc_now or assets.empty:
        st.caption("Needs a live bitcoin price and your holdings.")
        return
    choices = whatif.price_choices(btc_now)
    start = min(choices, key=lambda p: abs(p - btc_now))
    target = st.select_slider("Bitcoin price", options=choices, value=start, format_func=lambda p: f"${p:,.0f}",
                              key="whatif_btc")
    proj = whatif.project(assets, b, btc_now, target)
    now, then = proj["now"].sum(), proj["then"].sum()
    move = f'<span class="{tone(then - now)}">{usd(then - now, signed=True)} ({pct(then / now - 1 if now else 0, True)})</span>'
    stats = [("Bitcoin", usd(target), f"{pct(target / btc_now - 1, True)} from {usd(btc_now)}"),
             ("Your portfolio", usd(then), move)]
    st.html('<div class="sw-stats">' + "".join(
        f'<div class="sw-stat"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{x}</div></div>'
        for k, v, x in stats) + "</div>")
    cards = []
    for r in proj[proj["now"] >= 1].sort_values("then", ascending=False).itertuples():
        info = b.get(r.asset, {})
        if r.beta is None:
            tag = "follows its stock"
        elif info.get("source") == "measured":
            tag = f"moves {r.beta:.2f}x BTC"
        else:
            tag = f"{r.beta:.2f}x, {info.get('source', '')}"
        cards.append(f'<div class="sw-card"><div class="sw-card-top"><span class="sw-sym">{esc(r.asset)}</span>'
                     f'<span class="sw-weight">{esc(tag)}</span></div><div class="sw-value">{usd(r.then)}</div>'
                     f'<div class="sw-row"><span class="flat">now {usd(r.now)}</span>'
                     f'<span class="{tone(r.change)}">{usd(r.change, signed=True)}</span></div></div>')
    st.html(f'<div class="sw-cards">{"".join(cards)}</div>')

    lo, hi = min(choices), max(choices)
    c = whatif.curve(assets, b, btc_now, lo, hi)
    xs = alt.Scale(type="log", domain=[lo, hi])
    line = alt.Chart(c).mark_line(color=YELLOW, strokeWidth=2).encode(
        x=alt.X("btc:Q", scale=xs, title="Bitcoin price", axis=alt.Axis(format="$,.0s")),
        y=alt.Y("total:Q", title=None, axis=alt.Axis(format="$,.0f")),
        tooltip=[alt.Tooltip("btc:Q", title="BTC", format="$,.0f"), alt.Tooltip("total:Q", title="Portfolio", format="$,.0f")])
    marks = pd.DataFrame([{"btc": btc_now, "total": now, "what": "today"}, {"btc": target, "total": then, "what": "what if"}])
    pts = alt.Chart(marks).mark_point(filled=True, size=90).encode(
        x=alt.X("btc:Q", scale=xs), y="total:Q",
        color=alt.Color("what:N", scale=alt.Scale(domain=["today", "what if"], range=[INK, YELLOW]), legend=None),
        tooltip=[alt.Tooltip("what:N", title=""), alt.Tooltip("btc:Q", title="BTC", format="$,.0f"),
                 alt.Tooltip("total:Q", title="Portfolio", format="$,.0f")])
    st.altair_chart(alt.layer(line, pts).properties(height=280), width="stretch")
    st.caption("Each holding moves with bitcoin by its beta: how much it has moved per 1% BTC move over the past "
               "year of daily closes. Options are valued at what they'd be worth on the new stock price plus "
               "today's time value. A rough guide, not a forecast.")


# ---------------------------------------------------------------- achievements

def achievements_view(badges: list[dict]) -> None:
    earned = sum(b["earned"] for b in badges)
    cells = []
    for b in badges:
        cls = "sw-badge on" if b["earned"] else "sw-badge"
        bar = "" if b["earned"] else f'<div class="sw-bar"><i style="width:{max(2, b["progress"] * 100):.0f}%"></i></div>'
        cells.append(f'<div class="{cls}"><div class="ins">{esc(b["code"])}</div><div class="txt">'
                     f'<div class="n">{esc(b["name"])}</div><div class="d">{esc(b["desc"])}</div>'
                     f'<div class="x">{esc(b["detail"])}</div>{bar}</div></div>')
    st.html(f'<div class="sw-stats"><div class="sw-stat"><div class="k">Earned</div><div class="v">{earned} of {len(badges)}</div>'
            f'<div class="s">badges from your real history</div></div></div><div class="sw-badges">{"".join(cells)}</div>')


# ---------------------------------------------------------------- ticker

def _price(v: float) -> str:
    from panel.holdings import price_fmt

    return price_fmt(v)


def ticker(assets: pd.DataFrame, btc_price: float | None, btc_open: float | None) -> None:
    items = []
    if btc_price:
        items.append(("BTC", btc_price, (btc_price / btc_open - 1) if btc_open else None))
    for r in assets.itertuples():
        if r.asset == lenses.CASH_ASSET or r.market_value <= MIN_SHOWN or r.price is None or pd.isna(r.price):
            continue
        prev_value = r.market_value - r.day_change
        items.append((r.asset, float(r.price), (r.day_change / prev_value) if prev_value else None))
    if not items:
        return
    arrows = {"up": "▲", "down": "▼", "flat": "■"}
    cells = "".join(
        f'<span class="t"><b>{esc(n)}</b> {_price(p)} '
        + (f'<i class="{tone(c * 100)}">{arrows[tone(c * 100)]} {abs(c):.2%}</i>' if c is not None else "")
        + "</span>" for n, p, c in items)
    secs = max(24, 6 * len(items))
    st.html(f'<div class="sw-ticker"><div class="sw-ticker-track" style="animation-duration:{secs}s">'
            f"{cells}{cells}</div></div>")
