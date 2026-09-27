"""What every source hands back, whatever the broker. The sync only ever sees these shapes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# Normalized transaction types. `amount` on a Txn is the signed effect on the account's USD cash.
BUY, SELL = "buy", "sell"
DEPOSIT, WITHDRAWAL = "deposit", "withdrawal"  # cash moving in/out of the account: your contributions
DIVIDEND, INTEREST, REINVEST = "dividend", "interest", "reinvest"
FEE, TAX = "fee", "tax"
TRANSFER_IN, TRANSFER_OUT = "transfer_in", "transfer_out"  # securities/coins moving in/out
REWARD = "reward"  # staking / earn / airdrops paid in coin
STOCK_DIVIDEND, SPLIT = "stock_dividend", "split"
OPTION_EVENT, TRANSFER, ADJUSTMENT = "option_event", "transfer", "adjustment"
INTERNAL = "internal"  # moves inside one account (Kraken spot <-> earn); nets to zero
OTHER = "other"

# Asset classes used for allocation.
EQUITY, ETF, FUND, OPTION, CRYPTO, STABLECOIN, CASH, BOND, OTHER_CLASS = (
    "equity", "etf", "fund", "option", "crypto", "stablecoin", "cash", "bond", "other",
)


@dataclass
class SourceAccount:
    external_id: str  # the provider's id for the account
    institution: str
    name: str = ""
    number: str = ""
    raw_type: str = ""
    category: str = ""
    broker_total: float | None = None  # the provider's own total, kept to reconcile against ours
    meta: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    quantity: float
    asset_class: str = OTHER_CLASS
    name: str = ""
    price: float | None = None  # broker-reported price per unit (per share for options)
    currency: str = "USD"
    cost_basis: float | None = None  # TOTAL cost of the position, not per unit
    multiplier: float = 1.0  # 100 for equity options
    in_cash_balance: bool = False  # already counted in the account's cash (e.g. Fidelity SPAXX core)
    meta: dict = field(default_factory=dict)


@dataclass
class Cash:
    currency: str
    amount: float
    buying_power: float | None = None


@dataclass
class Txn:
    external_id: str
    type: str
    occurred_at: datetime | None
    raw_type: str = ""
    symbol: str = ""
    quantity: float | None = None  # signed: + received, - given up
    price: float | None = None
    amount: float | None = None  # signed USD cash effect
    value_usd: float | None = None  # gross USD value of the quantity (for cost basis when no cash moved)
    fee: float | None = None
    currency: str = "USD"
    description: str = ""
    settle_date: date | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AccountSnapshot:
    account: SourceAccount
    positions: list[Position] = field(default_factory=list)
    cash: list[Cash] = field(default_factory=list)
    transactions: list[Txn] = field(default_factory=list)
    data_as_of: datetime | None = None  # when the provider last pulled from the broker
    holdings_ready: bool = True  # False while a new connection is still on its first sync
    error: str | None = None  # set when this account failed; the sync then leaves its stored data alone
    warnings: list[str] = field(default_factory=list)  # partial trouble (e.g. activity fetch) that didn't stop it
