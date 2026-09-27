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


def test_every_card_variation_renders_with_prices():
    import pandas as pd
    from panel import holdings as H
    shown = pd.DataFrame([
        {"asset": "MSTR", "symbol": "MSTR", "asset_class": "equity", "name": "Strategy", "quantity": 150.0, "price": 158.61,
         "market_value": 23760.0, "day_change": -449.0, "cost_basis": 18053.0, "unrealized": 5707.0,
         "unrealized_pct": 0.316, "weight": 0.577, "accounts": ["Robinhood IRA", "Robinhood Taxable"]},
        {"asset": "Cash", "symbol": "Cash", "asset_class": "cash", "name": "Cash", "quantity": 2392.0, "price": 1.0,
         "market_value": 2392.0, "day_change": 0.0, "cost_basis": 2392.0, "unrealized": 0.0, "unrealized_pct": 0.0,
         "weight": 0.058, "accounts": ["Fidelity Taxable"]},
    ])
    spark = {"MSTR": [150.0, 152.0, 149.0, 158.61, 158.0]}
    assert set(H.FORMATS) == set(H.RENDER) and "Manifest" not in H.FORMATS and len(H.FORMATS) == 5
    for name, (fn, _days) in H.RENDER.items():
        html = fn(shown, spark)
        assert "MSTR" in html and "Cash" in html, name
        assert "$158.6" in html, name
    assert "avg $120.4" in H.cost(shown, {}) and "30 days" in H.trend(shown, spark)
