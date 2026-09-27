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


def test_treemap_tiles_fill_the_panel_in_proportion():
    from panel.holdings import squarify
    vals = [23760, 13877, 2392, 1165]
    rects = squarify(vals, 240, 100)
    areas = [w * h for _, _, w, h in rects]
    assert sum(areas) == pytest.approx(24000)
    for a, v in zip(areas, vals):
        assert a / 24000 == pytest.approx(v / sum(vals))
    assert all(x >= -1e-9 and y >= -1e-9 and x + w <= 240 + 1e-6 and y + h <= 100 + 1e-6 for x, y, w, h in rects)
