"""Data sources. Each one lists its accounts and returns AccountSnapshots (see portfolio.models).

  snaptrade  Fidelity + Robinhood (taxable and IRA), read-only, via a free SnapTrade Personal key
  kraken     Kraken's own API with a read-only key (real time, full ledger)
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Protocol

from portfolio.config import source_settings
from portfolio.models import AccountSnapshot

SinceFn = Callable[[str], "date | None"]  # external account id -> last trade date we already hold


class Source(Protocol):
    name: str
    derive_cost_basis: bool  # True when the provider doesn't report cost basis and we compute it from txns

    def missing_config(self) -> str | None: ...

    def fetch(self, since: SinceFn, refresh: bool = False) -> list[AccountSnapshot]: ...


def all_sources(only: str | None = None) -> list[Source]:
    from portfolio.sources.kraken import KrakenSource
    from portfolio.sources.snaptrade import SnapTradeSource

    out: list[Source] = []
    for cls in (SnapTradeSource, KrakenSource):
        if only and cls.name != only:
            continue
        if source_settings(cls.name).get("enabled", True):
            out.append(cls(source_settings(cls.name)))
    return out
