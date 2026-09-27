"""SnapTrade Personal: one free, read-only key that covers Fidelity and Robinhood (taxable + IRA).

Freshness, per SnapTrade's docs: holdings are either live or cached daily depending on the plan and
brokerage (each positions call reports `data_freshness.as_of`, which we store), and transactions
arrive once a day, a day late. The sync fills that gap by diffing holdings between runs (see the
`changes` table), so a buy, sell or deposit shows up on the next run that sees fresh holdings.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from portfolio import models as m
from portfolio.config import env, pricing
from portfolio.sources import SinceFn
from portfolio.sources._util import num, plain
from portfolio.timeutil import parse_dt

KIND_TO_CLASS = {
    "stock": m.EQUITY, "adr": m.EQUITY, "etf": m.ETF, "mutualfund": m.FUND, "cef": m.FUND,
    "option": m.OPTION, "future_option": m.OPTION, "crypto": m.CRYPTO, "bond": m.BOND,
}
OPTION_KINDS = {"option", "future_option"}

TYPE_MAP = {
    "BUY": m.BUY, "SELL": m.SELL,
    "DIVIDEND": m.DIVIDEND, "SUBSTITUTE_DIVIDEND": m.DIVIDEND,
    "CONTRIBUTION": m.DEPOSIT, "WITHDRAWAL": m.WITHDRAWAL,
    "REI": m.REINVEST, "STOCK_DIVIDEND": m.STOCK_DIVIDEND,
    "INTEREST": m.INTEREST, "FEE": m.FEE, "TAX": m.TAX,
    "OPTIONEXPIRATION": m.OPTION_EVENT, "OPTIONASSIGNMENT": m.OPTION_EVENT, "OPTIONEXERCISE": m.OPTION_EVENT,
    "TRANSFER": m.TRANSFER,
    "EXTERNAL_ASSET_TRANSFER_IN": m.TRANSFER_IN, "EXTERNAL_ASSET_TRANSFER_OUT": m.TRANSFER_OUT,
    "SPLIT": m.SPLIT, "ADJUSTMENT": m.ADJUSTMENT,
}
CASH_OUT = {m.BUY, m.WITHDRAWAL, m.FEE, m.TAX}
CASH_IN = {m.SELL, m.DIVIDEND, m.INTEREST, m.DEPOSIT}
UNITS_IN = {m.BUY, m.REINVEST, m.STOCK_DIVIDEND, m.TRANSFER_IN}
UNITS_OUT = {m.SELL, m.TRANSFER_OUT}

RETIREMENT = re.compile(r"\bira\b|roth|401|403|retire|\bsep\b", re.I)


def _body(resp):
    return plain(getattr(resp, "body", resp))


def is_retirement(acct: m.SourceAccount) -> bool:
    return bool(RETIREMENT.search(" ".join([acct.raw_type, acct.name, acct.category])))


def parse_account(a: dict) -> m.SourceAccount:
    total = (a.get("balance") or {}).get("total") or {}
    sync = a.get("sync_status") or {}
    holdings = sync.get("holdings") or {}
    txns = sync.get("transactions") or {}
    return m.SourceAccount(
        external_id=str(a.get("id")),
        institution=a.get("institution_name") or "",
        name=a.get("name") or "",
        number=str(a.get("number") or ""),
        raw_type=a.get("raw_type") or "",
        category=a.get("account_category") or "",
        broker_total=num(total.get("amount")),
        meta={
            "authorization_id": a.get("brokerage_authorization"),
            "status": a.get("status"),
            "holdings_ready": holdings.get("initial_sync_completed"),
            "holdings_last_sync": holdings.get("last_successful_sync"),
            "holdings_unavailable": holdings.get("holdings_unavailable"),
            "transactions_last_sync": txns.get("last_successful_sync"),
            "first_transaction_date": txns.get("first_transaction_date"),
            "is_paper": a.get("is_paper"),
        },
    )


def parse_positions(body) -> tuple[list[m.Position], object]:
    results = body.get("results") if isinstance(body, dict) else body
    as_of = parse_dt(((body.get("data_freshness") or {}) if isinstance(body, dict) else {}).get("as_of"))
    cash_like = {s.upper() for s in pricing().get("cash_symbols") or []}
    out: list[m.Position] = []
    for p in results or []:
        inst = p.get("instrument") or {}
        kind = (inst.get("kind") or "other").lower()
        symbol = (inst.get("symbol") or inst.get("raw_symbol") or "").strip()
        units = num(p.get("units")) or 0.0
        if not symbol or units == 0:
            continue
        multiplier = 100.0 if kind in OPTION_KINDS else 1.0
        per_unit_cost = num(p.get("cost_basis"))  # per share, also for options
        in_cash = bool(p.get("cash_equivalent"))
        asset_class = m.CASH if in_cash or symbol.upper() in cash_like else KIND_TO_CLASS.get(kind, m.OTHER_CLASS)
        out.append(m.Position(
            symbol=symbol,
            quantity=units,
            asset_class=asset_class,
            name=inst.get("description") or "",
            price=num(p.get("price")),
            currency=p.get("currency") or "USD",
            cost_basis=per_unit_cost * units * multiplier if per_unit_cost is not None else None,
            multiplier=multiplier,
            in_cash_balance=in_cash,
            meta={"kind": kind, "figi": (inst.get("figi_instrument") or {}).get("figi_code")},
        ))
    return out, as_of


def parse_balances(body) -> list[m.Cash]:
    out = []
    for b in body or []:
        code = ((b.get("currency") or {}).get("code") or "USD").upper()
        out.append(m.Cash(currency=code, amount=num(b.get("cash")) or 0.0, buying_power=num(b.get("buying_power"))))
    return out


def parse_activity(r: dict) -> m.Txn:
    raw_type = (r.get("type") or "").upper()
    t = TYPE_MAP.get(raw_type, m.OTHER)
    sym = r.get("symbol") or {}
    opt = r.get("option_symbol") or {}
    units, price = num(r.get("units")), num(r.get("price"))
    amount, fee = num(r.get("amount")), num(r.get("fee"))
    if amount is None and units is not None and price is not None and t in {m.BUY, m.SELL}:
        amount = units * price * (100.0 if opt else 1.0)
    # Brokers disagree on signs; pin them by what the transaction is.
    if amount is not None:
        if t in CASH_OUT:
            amount = -abs(amount)
        elif t in CASH_IN:
            amount = abs(amount)
    if units is not None:
        if t in UNITS_IN:
            units = abs(units)
        elif t in UNITS_OUT:
            units = -abs(units)
    trade = parse_dt(r.get("trade_date")) or parse_dt(r.get("settlement_date"))
    settle = parse_dt(r.get("settlement_date"))
    currency = ((r.get("currency") or {}).get("code") or (sym.get("currency") or {}).get("code") or "USD").upper()
    return m.Txn(
        external_id=str(r.get("id")),
        type=t,
        raw_type=raw_type,
        occurred_at=trade,
        symbol=opt.get("ticker") or sym.get("symbol") or "",
        quantity=units,
        price=price,
        amount=amount,
        value_usd=abs(amount) if amount is not None and t in {m.BUY, m.SELL, m.REINVEST} else None,
        fee=fee,
        currency=currency,
        description=r.get("description") or "",
        settle_date=settle.date() if settle else None,
        raw=r,
    )


class SnapTradeSource:
    name = "snaptrade"
    derive_cost_basis = False

    def __init__(self, settings: dict | None = None, client=None):
        self.settings = settings or {}
        self._client = client

    def missing_config(self) -> str | None:
        if self._client is None and not (env("SNAPTRADE_CLIENT_ID") and env("SNAPTRADE_CONSUMER_KEY")):
            return "SNAPTRADE_CLIENT_ID / SNAPTRADE_CONSUMER_KEY not set"
        return None

    @property
    def client(self):
        if self._client is None:
            from snaptrade_client import SnapTrade, SnapTradeAuth

            self._client = SnapTrade(auth=SnapTradeAuth.personal_api_key(
                consumer_key=env("SNAPTRADE_CONSUMER_KEY"), client_id=env("SNAPTRADE_CLIENT_ID"),
            ))
        return self._client

    # ------------------------------------------------------------ connections

    def connections(self) -> list[dict]:
        return _body(self.client.connections.list_brokerage_authorizations()) or []

    def refresh_connections(self) -> list[str]:
        """Ask SnapTrade to re-pull every broker now. Runs async: fresh data lands a few minutes later."""
        errors = []
        for c in self.connections():
            try:
                self.client.connections.refresh_brokerage_authorization(authorization_id=c.get("id"))
            except Exception as e:  # noqa: BLE001 - plan may not allow it; the cached data still syncs
                errors.append(f"{c.get('name') or c.get('id')}: {e}")
        return errors

    def portal_url(self, broker: str | None = None, reconnect: str | None = None) -> str:
        kwargs = {"connection_type": "read"}
        if broker:
            kwargs["broker"] = broker
        if reconnect:
            kwargs["reconnect"] = reconnect
        body = _body(self.client.authentication.login_snap_trade_user(**kwargs)) or {}
        return body.get("redirectURI") or body.get("redirect_uri") or ""

    # ------------------------------------------------------------ data

    def list_accounts(self) -> list[m.SourceAccount]:
        return [parse_account(a) for a in _body(self.client.account_information.list_user_accounts()) or []]

    def activities(self, account_id: str, start: date | None) -> list[m.Txn]:
        rows, offset, limit = [], 0, 1000
        while True:
            kwargs = {"account_id": account_id, "offset": offset, "limit": limit}
            if start:
                kwargs["start_date"] = start
            body = _body(self.client.account_information.get_account_activities(**kwargs))
            page = (body.get("data") if isinstance(body, dict) else body) or []
            rows.extend(page)
            total = ((body.get("pagination") or {}) if isinstance(body, dict) else {}).get("total")
            offset += len(page)
            if len(page) < limit or (total is not None and offset >= total):
                break
        return [parse_activity(r) for r in rows]

    def fetch(self, since: SinceFn, refresh: bool = False) -> list[m.AccountSnapshot]:
        if refresh:
            self.refresh_connections()
        overlap = timedelta(days=int(self.settings.get("overlap_days", 10)))
        snaps = []
        for acct in self.list_accounts():
            if acct.meta.get("is_paper"):
                continue
            snap = m.AccountSnapshot(account=acct, holdings_ready=acct.meta.get("holdings_ready") is not False)
            try:
                positions, as_of = parse_positions(_body(
                    self.client.account_information.get_all_account_positions(account_id=acct.external_id)))
                snap.positions = positions
                snap.data_as_of = as_of or parse_dt(acct.meta.get("holdings_last_sync"))
                snap.cash = parse_balances(_body(
                    self.client.account_information.get_user_account_balance(account_id=acct.external_id)))
            except Exception as e:  # noqa: BLE001 - one broken connection must not sink the others
                snap.error = f"{type(e).__name__}: {e}"
                snaps.append(snap)
                continue
            try:
                last = since(acct.external_id)
                snap.transactions = self.activities(acct.external_id, last - overlap if last else None)
            except Exception as e:  # noqa: BLE001 - holdings are still good; activity retries next run
                snap.warnings.append(f"activities: {type(e).__name__}: {e}")
            snaps.append(snap)
        return snaps
