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
