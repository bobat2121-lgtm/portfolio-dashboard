"""Who may see the real accounts.

Every visit starts on an Enter screen and opens on the Simulation (portfolio/simulation.py: made-up
numbers, safe to show anyone). The real accounts need the password (APP_PASSWORD, 16+ characters, in the
app's secrets), asked for when the Simulation switch is turned off. The check is on the server, in
`authorized()`, and every data read goes through it (panel.common.simulated), so nothing a visitor's
browser sends can reach real data without the password.

Unlocking can remember the browser for 30 days: a signed, expiring pass is saved in the browser (never
the password), and the page hands it back to the server on each visit. "Lock" in the header forgets the
browser; changing APP_PASSWORD (or AUTH_SECRET) voids every pass at once. Wrong passwords are limited per address and across the whole app, so guessing is hopeless
against a long random password.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from functools import lru_cache

import streamlit as st

from portfolio import db
from portfolio.config import env

MIN_PASSWORD = 16
REMEMBER_DAYS = 30
SESSION_HOURS = 12                 # an unlock that isn't remembered lasts this long in its tab
STORE = "sw_pass"                  # the browser's localStorage key (and the first version's cookie name)
IP_TRIES, IP_WINDOW = 5, 300       # wrong passwords from one address before it waits 5 minutes
ALL_TRIES, ALL_WINDOW = 20, 900    # wrong passwords from anywhere before everyone waits 15 minutes

ENTERED = "entered"                # session_state keys
PASS = "_pass"                     # this tab's signed pass, once unlocked
FORGET = "_forget"                 # after Lock: ignore a pass the page sends from before it
PENDING = "_store_js"              # a change to the saved pass, for the browser on the next full run


def password() -> str | None:
    """APP_PASSWORD when it is long enough to use. Without one the real accounts stay off; the
    Simulation still works."""
    pw = env("APP_PASSWORD") or ""
    return pw if len(pw) >= MIN_PASSWORD else None


def open_locally() -> bool:
    """No lock needed: made-up demo data, or your own machine's SQLite file with no password set."""
    if env("DEMO"):
        return True
    with db.using(None):  # the real database, never the Simulation's
        return not db.is_postgres() and not env("APP_PASSWORD")


def authorized() -> bool:
    """May this tab see the real accounts?"""
    if open_locally():
        return True
    pw = password()
    if pw is None:
        return False
    return valid(st.session_state.get(PASS), pw)


# ---------------------------------------------------------------- the signed pass

@lru_cache(maxsize=4)
def _key(pw: str, secret: str) -> bytes:
    # slow on purpose (once per process): a pass can't be used to test password guesses quickly
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), b"btc-supernova/pass/" + secret.encode(), 300_000)


def _sign(pw: str, exp: int) -> str:
    return hmac.new(_key(pw, env("AUTH_SECRET") or ""), f"v1|{exp}".encode(), hashlib.sha256).hexdigest()


def make_pass(pw: str, seconds: float, now: float | None = None) -> str:
    """'<expiry>.<signature>': good until the expiry, and only while the password stays the same."""
    exp = int((time.time() if now is None else now) + seconds)
    return f"{exp}.{_sign(pw, exp)}"


def valid(token: str | None, pw: str | None, now: float | None = None) -> bool:
    if not isinstance(token, str) or not token or not pw or len(token) > 100:
        return False
    now = time.time() if now is None else now
    exp_s, _, sig = token.partition(".")
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if not now < exp <= now + REMEMBER_DAYS * 86400 + 60:
        return False
    return hmac.compare_digest(sig.encode(), _sign(pw, exp).encode())


# ---------------------------------------------------------------- the pass this browser saved
# Streamlit Cloud doesn't pass cookies through to the app, so the page reads the saved pass itself and sends
# it over the app's own connection, once per page load. Whatever arrives is checked like any other pass.
_READ_JS = r"""
export default function ({ setTriggerValue }) {
  if (window.__swPassSent) return;
  window.__swPassSent = true;
  let pass = null;
  try { pass = window.localStorage.getItem("sw_pass"); } catch (e) {}
  const old = document.cookie.match(/(?:^|;\s*)sw_pass=([^;]+)/);   // the first version kept it in a cookie
  if (old) {
    pass = pass || old[1];
    try { window.localStorage.setItem("sw_pass", pass); } catch (e) {}
    document.cookie = "sw_pass=; Max-Age=0; Path=/";
  }
  if (pass) setTriggerValue("token", pass);
}
"""
READER = "sw_pass_reader"
_mount = None


def _reader(**kw):
    """Mount the reader, registering it once per server runtime (a registration doesn't outlive its runtime)."""
    global _mount
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    if _mount is None or get_bidi_component_manager().get(READER) is None:
        _mount = st.components.v2.component(READER, js=_READ_JS)
    return _mount(**kw)


def recall() -> None:
    """Take a pass this browser saved ("Remember this browser"), when the page sends one. Call on every run."""
    with st.container(key="sw-pass-reader"):
        got = _reader(key="sw-pass", on_token_change=_ignore)
    token = got.get("token") if got else None
    if token and not st.session_state.get(FORGET) and valid(token, password()):
        st.session_state[PASS] = token


def _ignore() -> None:
    pass


# ---------------------------------------------------------------- wrong passwords

_misses: dict[str, list[float]] = {}   # "ip:<address>" and "all" -> times of recent wrong passwords
_misses_lock = threading.Lock()


def _who() -> str:
    try:
        ip = st.context.ip_address
    except Exception:  # noqa: BLE001
        ip = None
    return f"ip:{ip or '?'}"


def _wait(times: list[float], limit: int, window: float, now: float) -> float:
    recent = [t for t in times if now - t < window]
    return window - (now - recent[-limit]) if len(recent) >= limit else 0.0


def wait_seconds(who: str, now: float | None = None) -> int:
    """Seconds before `who` may try again (0: now)."""
    now = time.time() if now is None else now
    with _misses_lock:
        w = max(_wait(_misses.get(who, []), IP_TRIES, IP_WINDOW, now),
                _wait(_misses.get("all", []), ALL_TRIES, ALL_WINDOW, now))
    return int(w) + 1 if w else 0


def _miss(who: str, now: float) -> None:
    with _misses_lock:
        for k in (who, "all"):
            _misses[k] = [t for t in _misses.get(k, []) if now - t < ALL_WINDOW] + [now]
        for k in [k for k, v in _misses.items() if now - v[-1] >= ALL_WINDOW]:
            del _misses[k]


# ---------------------------------------------------------------- unlocking and locking

def unlock(entered: str, remember: bool) -> str | None:
    """Check a password typed into the unlock box. None when it was right (this tab is unlocked, and the
    browser remembered if asked); otherwise what to tell the visitor."""
    pw = password()
    if pw is None:
        return (f"The real accounts are off on this server: set APP_PASSWORD (at least {MIN_PASSWORD} "
                "characters) in the app's secrets.")
    who, now = _who(), time.time()
    if wait := wait_seconds(who, now):
        return f"Too many wrong passwords. Try again in {wait} s."
    if not hmac.compare_digest(entered.encode(), pw.encode()):
        _miss(who, now)
        time.sleep(1.5)  # slows guessing further; the real defense is a long random password
        return "Wrong password."
    token = make_pass(pw, REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600, now)
    st.session_state[PASS] = token
    if remember:
        st.session_state[PENDING] = _store_js(token)
    return None


def lock() -> None:
    """Back to the Simulation-only view, and this browser forgotten."""
    st.session_state.pop(PASS, None)
    st.session_state[FORGET] = True
    st.session_state[PENDING] = _store_js("")


def _store_js(token: str) -> str:
    # the pass is digits, a dot and hex, so it sits safely in the script
    act = f'localStorage.setItem("{STORE}", "{token}")' if token else f'localStorage.removeItem("{STORE}")'
    return f'<script>try {{ {act}; }} catch (e) {{}} document.cookie = "{STORE}=; Max-Age=0; Path=/";</script>'


def flush() -> None:
    """Hand a pending change to the saved pass (after unlocking or locking) to the browser."""
    if js := st.session_state.pop(PENDING, None):
        st.html(f'<div class="sw-cookie"></div>{js}', unsafe_allow_javascript=True)


# ---------------------------------------------------------------- the start screen

def entrance() -> None:
    """The start screen: the scene and one Enter button. Nothing is read until it's pressed, and it
    opens on the Simulation."""
    if st.session_state.get(ENTERED):
        return
    with st.container(horizontal=True, horizontal_alignment="center", key="sw-enter"):
        st.button("Enter", key="sw-enter-go", on_click=_enter)
    st.stop()


def _enter() -> None:
    st.session_state[ENTERED] = True
