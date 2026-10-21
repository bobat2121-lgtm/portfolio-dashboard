"""Normalize security transfers before they reach cash, performance, or tax calculations.

Some broker activities call a coin/share transfer a deposit or withdrawal and put
its market value in ``amount``. That is an in-kind movement, not USD cash. Apply
the same rules when ingesting new activities and reading already-stored history.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from portfolio import models as m
from portfolio.pricehist import cash_like


# Fiat balances and sweep funds remain cash. Stablecoins such as USDG and USDC
# deliberately are not in this set: their quantities represent transferable assets.
FIAT_SYMBOLS = frozenset({
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "NZD", "HKD", "SGD",
    "CNY", "CNH", "SEK", "NOK", "DKK", "MXN", "BRL", "INR", "ZAR", "KRW",
    "PLN", "CZK", "HUF", "ILS", "TRY", "RUB", "THB",
})
TRANSFER_TYPES = frozenset({m.DEPOSIT, m.WITHDRAWAL, m.TRANSFER_IN, m.TRANSFER_OUT, m.TRANSFER})


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _transfer_updates(row: Mapping) -> dict:
    typ = row.get("type")
    symbol = row.get("symbol")
    if typ not in TRANSFER_TYPES or not isinstance(symbol, str):
        return {}
    symbol = symbol.strip().upper()
    if not symbol or symbol in FIAT_SYMBOLS or symbol == "CASH" or symbol.startswith("CASH:") or cash_like(symbol):
        return {}
    quantity = _number(row.get("quantity"))
    if quantity is None or quantity == 0:
        return {}

    if typ in (m.DEPOSIT, m.TRANSFER_IN):
        typ, quantity = m.TRANSFER_IN, abs(quantity)
    elif typ in (m.WITHDRAWAL, m.TRANSFER_OUT):
        typ, quantity = m.TRANSFER_OUT, -abs(quantity)
    # A generic transfer already carries its direction in its signed quantity.
    updates = {"type": typ, "quantity": quantity}
    amount = _number(row.get("amount"))
    if "amount" in row:
        updates["amount"] = None
    if "value_usd" in row and pd.isna(row["value_usd"]) and amount is not None:
        updates["value_usd"] = abs(amount)  # reported valuation only; never a purchase cost
    return updates


def normalize_security_transfer(row: Mapping) -> dict:
    """Return a normalized copy, retaining the input's keys and raw provider data.

    Missing fields are allowed. A non-cash symbol and finite, nonzero quantity
    are both required before a deposit/withdrawal can become an in-kind transfer.
    Price, fee, currency, and raw payload are never rewritten. Reapplying this
    function to an already-normalized row leaves its values unchanged.
    """
    result = dict(row)
    result.update(_transfer_updates(row))
    return result


def normalize_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize legacy or fresh rows without changing the index or column set."""
    result = frame.copy()
    if frame.empty:
        return result
    updates = [_transfer_updates(row) for row in frame.to_dict("records")]
    changed = {column for update in updates for column in update}
    for column in frame.columns:
        if column in changed:
            # Assign by position, so duplicate index labels are safe. Only the
            # affected scalar columns are rebuilt; dates and raw objects stay intact.
            result[column] = [update.get(column, value) for update, value in zip(updates, frame[column])]
    return result
