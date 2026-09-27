"""Settings: config/portfolio.yaml for the account map and knobs; env vars (or .env) for secrets."""
from __future__ import annotations

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


@lru_cache(maxsize=None)
def settings() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def section(name: str) -> dict:
    return settings().get(name) or {}


def account_specs() -> dict[str, dict]:
    """key -> {label, institution, tax, source, match}"""
    return section("accounts")


def source_settings(name: str) -> dict:
    return section("sources").get(name) or {}


def pricing() -> dict:
    return section("pricing")
