"""The look: a pixel-art space scene behind the dashboard, Star Wars palette and Star Jedi type.

Three panel styles:
    distinct  solid panels sitting on top of space
    melded    no panels: space shows through everything
    mix       header and numbers float on space, data sits in solid panels

The scene itself is panel/assets/space.js (canvas, runs in the page). The Star Jedi font (Boba Fonts,
freeware) ships as its original, intact zip in panel/assets/fonts/, as its license asks, and is read
straight out of it.
"""
from __future__ import annotations

import base64
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent / "assets"
STYLES = {"distinct": "Distinct", "melded": "Melded", "mix": "Mix"}
DEFAULT_STYLE = "mix"
EVENTS = {"shooting_star", "meteor_shower", "supernova", "comet", "asteroid", "pulsar", "hyperspace", "superlaser",
          "rebel_attack", "blackhole"}
BLACK_HOLE_ODDS = 1 / 300  # per random event; events come every 20 s - 2 min, so roughly once in 6 hours


@lru_cache(maxsize=1)
def _font_face() -> str:
    path = ASSETS / "fonts" / "star_jedi.zip"
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("starjedi/Starjedi.ttf")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""  # falls back to Orbitron
    b64 = base64.b64encode(data).decode()
    return (f"@font-face{{font-family:'Star Jedi';src:url(data:font/ttf;base64,{b64}) format('truetype');"
            "font-display:swap;}")


def _read(name: str) -> str:
    return _read_version(name, (ASSETS / name).stat().st_mtime_ns)  # edits show up without a restart


@lru_cache(maxsize=32)
def _read_version(name: str, _mtime: int) -> str:
    text = (ASSETS / name).read_text(encoding="utf-8")
    if name.endswith(".js") and (m := TAG_LIKE.search(text)):
        # DOMPurify silently drops a script whose text contains "<" + letter/slash, so fail loudly instead.
        raise ValueError(f"{name} contains {m.group(0)!r}; add a space after '<' or reword it")
    return text


TAG_LIKE = re.compile(r"<[/\w!]")


def current_style() -> str:
    """Mix, unless the URL asks for another (?style=distinct|melded); there's no on-page switcher."""
    s = st.query_params.get("style") or DEFAULT_STYLE
    s = str(s).lower()
    return s if s in STYLES else DEFAULT_STYLE


def scope(css: str, style: str) -> str:
    """Prefix every rule with html[data-space-style="<style>"], so all three styles can load at once."""
    out = []
    for block in re.sub(r"/\*.*?\*/", "", css, flags=re.S).split("}"):
        if "{" not in block:
            continue
        selectors, body = block.split("{", 1)
        scoped = ", ".join(f'html[data-space-style="{style}"] {s.strip()}' for s in selectors.split(",") if s.strip())
        out.append(f"{scoped} {{{' '.join(body.split())}}}")  # one rule per line
    return "\n".join(out)


def apply(style: str) -> None:
    """Inject the fonts, the CSS, and the space scene. Safe to call on every rerun.

    The style sheet is the same on every run: all three panel styles are in it, each scoped to the
    data-space-style attribute on <html>, which space.js sets from `style`. (Streamlit keeps style-only
    st.html blocks around between reruns, so swapping the CSS itself would leave the old style winning.)
    """
    base = _read("base.css")
    imports, rest = base.split("/*END-IMPORTS*/", 1)  # @import has to come before @font-face
    variants = "\n".join(scope(_read(f"{s}.css"), s) for s in STYLES)
    st.html(f"<style>{imports}{_font_face()}{rest}{variants}</style>")
    force = st.query_params.get("space")  # ?space=supernova etc., for trying events out
    cfg = {"style": style, "force": force if force in EVENTS else None, "odds": BLACK_HOLE_ODDS,
           "minGap": 20, "maxGap": 120}
    js = _read("space.js").replace("__SPACE_CFG__", json.dumps(cfg))
    st.html(f'<div id="space-boot"></div><script>{js}</script>', unsafe_allow_javascript=True)


def set_mood(day_pct: float) -> None:
    """Tell the scene how the day is going: -1 (down 2%+) .. +1 (up 2%+)."""
    mood = max(-1.0, min(1.0, float(day_pct) / 0.02))
    st.html(f'<div class="space-mood"></div><script>window.__space && window.__space.setMood({mood:.3f})</script>',
            unsafe_allow_javascript=True)
