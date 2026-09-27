"""Settings: config/portfolio.yaml for the account map and knobs; env vars (or .env) for secrets.

Anything personal (account-number pins, cost-basis overrides) goes in the PORTFOLIO_CONFIG secret instead
of the committed YAML. It's YAML in the same shape, merged on top, e.g.
    accounts: {robinhood_ira: {match: {number_last4: "1234"}}, kraken: {cost_basis_overrides: {BTC: 25000}}}
"""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "portfolio.yaml"
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env", override=False)


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = copy.deepcopy(base)
        for k, v in override.items():
            out[k] = deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(override if override is not None else base)


@lru_cache(maxsize=None)
def settings() -> dict:
    base = {}
    if CONFIG_PATH.exists():
        base = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    private = env("PORTFOLIO_CONFIG")
    return deep_merge(base, yaml.safe_load(private) or {}) if private else base


def section(name: str) -> dict:
    return settings().get(name) or {}


def account_specs() -> dict[str, dict]:
    """key -> {label, institution, tax, source, match}"""
    return section("accounts")


def source_settings(name: str) -> dict:
    return section("sources").get(name) or {}


def pricing() -> dict:
    return section("pricing")


def performance_start():
    """First day of the Performance timeline (config: performance.start), or None for all history."""
    from datetime import date

    v = section("performance").get("start")
    return v if isinstance(v, date) or v is None else date.fromisoformat(str(v))
