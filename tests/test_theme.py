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
