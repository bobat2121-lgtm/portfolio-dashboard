"""Average-cost basis from a transaction history, for sources that don't report one (Kraken).

Conventions: rewards (staking/Earn/airdrops) come in at $0 basis. Coins deposited from elsewhere
have unknown cost, so their asset's basis is marked incomplete unless you set an override in
config/portfolio.yaml (cost_basis_overrides).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from portfolio import models as m
from portfolio.timeutil import aware

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class Basis:
    units: float = 0.0
    cost: float = 0.0
    complete: bool = True

    def reduce(self, qty: float) -> None:
        """Take `qty` units out at average cost."""
        if self.units <= 0:
            return
        frac = min(abs(qty) / self.units, 1.0)
        self.cost -= self.cost * frac
        self.units = max(self.units - abs(qty), 0.0)


def average_cost(txns: list) -> dict[str, Basis]:
    """txns: objects with type, symbol, quantity, value_usd, fee, occurred_at (models.Txn or db rows)."""
    out: dict[str, Basis] = {}
    ordered = sorted((t for t in txns if t.symbol and t.quantity), key=lambda t: aware(t.occurred_at) or EPOCH)
    for t in ordered:
        b = out.setdefault(t.symbol, Basis())
        q = t.quantity
        if t.type in (m.BUY, m.REINVEST) and q > 0:
            b.units += q
            if t.value_usd is None:
                b.complete = False
            else:
                b.cost += t.value_usd + (t.fee or 0.0)
        elif t.type in (m.SELL, m.TRANSFER_OUT) or (t.type in (m.BUY, m.TRANSFER) and q < 0):
            b.reduce(q)
        elif t.type == m.REWARD and q > 0:
            b.units += q
        elif t.type in (m.TRANSFER_IN, m.TRANSFER) and q > 0:
            b.units += q
            b.complete = False
    return out


def basis_for(position_qty: float, basis: Basis | None, tolerance: float = 0.01) -> float | None:
    """Only trust the derived basis when the history accounts for what's actually held."""
    if basis is None or not basis.complete or position_qty <= 0:
        return None
    if abs(basis.units - position_qty) > max(abs(position_qty) * tolerance, 1e-8):
        return None
    return basis.cost
