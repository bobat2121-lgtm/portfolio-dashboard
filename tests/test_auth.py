"""The signed pass behind "remember this browser", and the limit on wrong passwords."""
import pytest

from panel import auth

PW = "a-long-enough-test-password"
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    auth._misses.clear()
    yield
    auth._misses.clear()


def test_a_pass_is_good_until_it_expires():
    token = auth.make_pass(PW, 30 * 86400, NOW)
    assert PW not in token
    assert auth.valid(token, PW, NOW)
    assert auth.valid(token, PW, NOW + 30 * 86400 - 1)
    assert not auth.valid(token, PW, NOW + 30 * 86400)


def test_a_tampered_or_foreign_pass_is_refused(monkeypatch):
    token = auth.make_pass(PW, 3600, NOW)
    exp, sig = token.split(".")
    assert not auth.valid(f"{int(exp) + 86400}.{sig}", PW, NOW)          # a later expiry, same signature
    assert not auth.valid(f"{exp}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}", PW, NOW)
    assert not auth.valid(token, PW + "-changed", NOW)                     # a new password voids it
    monkeypatch.setenv("AUTH_SECRET", "rotated")
    assert not auth.valid(token, PW, NOW)                                  # so does a new AUTH_SECRET
    far = auth.make_pass(PW, 365 * 86400, NOW)
    assert not auth.valid(far, PW, NOW)                                    # never longer than 30 days
    for junk in (None, "", ".", "abc", "1.2.3", "²." + sig, "9" * 200, token + "x"):
        assert not auth.valid(junk, PW, NOW), junk
    assert not auth.valid(token, None, NOW)


def test_wrong_passwords_are_limited_per_address():
    for k in range(auth.IP_TRIES - 1):
        auth._miss("ip:1.2.3.4", NOW + k)
    assert auth.wait_seconds("ip:1.2.3.4", NOW + 10) == 0
    auth._miss("ip:1.2.3.4", NOW + 10)
    assert 0 < auth.wait_seconds("ip:1.2.3.4", NOW + 11) <= auth.IP_WINDOW
    assert auth.wait_seconds("ip:5.6.7.8", NOW + 11) == 0                  # another address may still try
    assert auth.wait_seconds("ip:1.2.3.4", NOW + auth.IP_WINDOW + 1) == 0  # and the wait ends


def test_wrong_passwords_from_everywhere_lock_everyone_for_a_while():
    for k in range(auth.ALL_TRIES):
        auth._miss(f"ip:10.0.0.{k}", NOW + k)                              # one miss each from many addresses
    assert auth.wait_seconds("ip:10.9.9.9", NOW + auth.ALL_TRIES) > auth.IP_WINDOW
    assert auth.wait_seconds("ip:10.9.9.9", NOW + auth.ALL_TRIES + auth.ALL_WINDOW) == 0


def test_unlocking_checks_the_password(monkeypatch):
    import streamlit as st

    state: dict = {}
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(auth.time, "sleep", lambda s: None)
    monkeypatch.setenv("APP_PASSWORD", PW)
    assert auth.unlock("wrong", True) == "Wrong password."
    assert auth.PASS not in state
    assert auth.unlock(PW, False) is None
    assert auth.valid(state[auth.PASS], PW) and auth.PENDING not in state   # this tab only
    assert auth.unlock(PW, True) is None and "Max-Age=2592000" in state[auth.PENDING]
    for _ in range(auth.IP_TRIES):
        auth.unlock("wrong", True)
    assert "Too many" in auth.unlock(PW, True)                               # even the right one waits
    monkeypatch.setenv("APP_PASSWORD", "short")
    auth._misses.clear()
    assert "APP_PASSWORD" in auth.unlock("short", True)                      # too short: never unlocks
