import pytest

from panel import theme


def test_scene_script_survives_streamlits_sanitizer():
    js = theme._read("space.js")  # raises if anything in it looks like a tag to DOMPurify
    assert "__SPACE_CFG__" in js


def test_tag_like_text_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "bad.js").write_text("// see ?space=<event>\n", encoding="utf-8")
    monkeypatch.setattr(theme, "ASSETS", tmp_path)
    with pytest.raises(ValueError):
        theme._read("bad.js")


def test_every_style_has_css_and_the_font_loads():
    for style in theme.STYLES:
        assert theme._read(f"{style}.css").strip()
    assert "/*END-IMPORTS*/" in theme._read("base.css")
    assert theme._font_face().startswith("@font-face{font-family:'Star Jedi'")


def test_islands_builds_on_mix_and_keeps_its_rules_scoped():
    assert theme.STYLE_FILES["islands"] == ["mix.css", "islands.css"] and set(theme.STYLE_FILES) == set(theme.STYLES)
    scoped = theme.scope("\n".join(theme._read(f) for f in theme.STYLE_FILES["islands"]), "islands")
    rules = [r for r in scoped.splitlines() if r.strip()]
    assert all(r.startswith('html[data-space-style="islands"]') for r in rules)
    assert any(".st-key-sw-panel-assets" in r and "transparent" in r for r in rules)   # the holdings panel dissolves
    assert any(".sw-card" in r and "sw-bob" in r for r in rules)                      # its cards float
    assert "@keyframes sw-bob" in theme._read("base.css")


def test_forced_event_is_whitelisted():
    assert "blackhole" in theme.EVENTS and "<script>" not in theme.EVENTS


def test_all_styles_load_together_scoped_to_the_page_attribute():
    css = theme.scope('/* note */\n.a, [data-testid="stTab"]::before { color: red; clip-path: polygon(0 0, 1px 1px); }\nh1 { x: y }', "mix")
    assert css == ('html[data-space-style="mix"] .a, html[data-space-style="mix"] [data-testid="stTab"]::before '
                   '{color: red; clip-path: polygon(0 0, 1px 1px);}\nhtml[data-space-style="mix"] h1 {x: y}')
    for style in theme.STYLES:  # every variant rule is scoped, none leaks into the others
        scoped = theme.scope(theme._read(f"{style}.css"), style)
        rules = [r for r in scoped.splitlines() if r.strip()]
        assert rules and all(r.startswith(f'html[data-space-style="{style}"]') for r in rules)


def test_prices_round_the_way_the_cards_show_them():
    from panel.holdings import price_fmt
    assert price_fmt(158.61) == "$158.6" and price_fmt(84523.4) == "$84,523" and price_fmt(29.444) == "$29.44"
    assert price_fmt(0.7861) == "$0.786" and price_fmt(None) == "—"


def test_holding_cards_show_value_price_today_gain_and_share():
    import pandas as pd
    from panel import holdings as H
    shown = pd.DataFrame([
        {"asset": "MSTR", "symbol": "MSTR", "asset_class": "equity", "name": "Strategy", "quantity": 150.0, "price": 158.61,
         "market_value": 23760.0, "day_change": -449.0, "cost_basis": 18053.0, "unrealized": 5707.0,
         "unrealized_pct": 0.316, "weight": 0.577, "accounts": ["Robinhood IRA", "Robinhood Taxable"]},
        {"asset": "VTI", "symbol": "VTI", "asset_class": "equity", "name": "VTI", "quantity": 10.0, "price": 300.0,
         "market_value": 3000.0, "day_change": 0.0, "cost_basis": float("nan"), "unrealized": float("nan"),
         "unrealized_pct": float("nan"), "weight": 0.07, "accounts": ["Fidelity Taxable"]},
        {"asset": "Cash", "symbol": "Cash", "asset_class": "cash", "name": "Cash and money-market funds", "quantity": 2392.0,
         "price": 1.0, "market_value": 2392.0, "day_change": 0.0, "cost_basis": 2392.0, "unrealized": 0.0,
         "unrealized_pct": 0.0, "weight": 0.058, "accounts": ["Fidelity Taxable"]},
        {"asset": "ASST $35 call Jan '28", "symbol": "ASST280121C00035000", "asset_class": "option", "name": "",
         "underlying": "ASST", "quantity": 1.0, "price": 11.65, "market_value": 1165.0, "day_change": 20.0,
         "cost_basis": 900.0, "unrealized": 265.0, "unrealized_pct": 0.294, "weight": 0.028, "accounts": ["Robinhood Taxable"]},
    ])
    html = H.cards(shown)
    assert not hasattr(H, "FORMATS") and not hasattr(H, "RENDER")  # one format now: cards
    mstr, vti, cash, call = html.split('class="sw-card sw-hold"')[1:]
    for text in ("$23,760", "$158.6", "−$449", "▼ 1.85%", "+$5,707", "+31.6%", "57.7%", "Strategy", "Robinhood IRA"):
        assert text in mstr, text
    assert ">Stock<" in vti and "no cost basis" in vti   # a name that only repeats the symbol gives way to the kind
    from types import SimpleNamespace
    names = {n: H._title(SimpleNamespace(asset="X", asset_class="equity", name=n))[1] for n in
             ("Strive, Inc. Class A Common Stock", "Strategy Inc.", "Coinbase Global, Inc. - Class A Common Stock", "Apple Inc")}
    assert names == {"Strive, Inc. Class A Common Stock": "Strive", "Strategy Inc.": "Strategy",
                     "Coinbase Global, Inc. - Class A Common Stock": "Coinbase Global", "Apple Inc": "Apple"}
    assert '<span class="sw-sym">ASST</span>' in call and "$35 call Jan &#x27;28" in call and "$11.65" in call
    assert "Price" not in cash and "Share" in cash and "$2,392" in cash
