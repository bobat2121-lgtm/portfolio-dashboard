"""Kraken, straight from its REST API with a read-only key (Query Funds + Query Ledger Entries).

Balances are live. The ledger is the full history (deposits, trades, Earn rewards), which gives us
contributions and, since Kraken doesn't report cost basis, an average-cost basis per coin.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from portfolio import models as m
from portfolio.config import env, pricing
from portfolio.sources import SinceFn
from portfolio.sources._util import num
from portfolio.timeutil import utcnow

API = "https://api.kraken.com"
# The only private calls this app makes. Anything else (orders, withdrawals, Earn moves) is refused
# before a request is signed, so the code can't trade or move money even if the key could.
READ_ONLY = frozenset({"Balance", "Ledgers"})

# Kraken's legacy 4-letter codes, and names it uses that nobody else does.
LEGACY = {
    "XXBT": "BTC", "XETH": "ETH", "XLTC": "LTC", "XXRP": "XRP", "XXLM": "XLM", "XXDG": "DOGE",
    "XETC": "ETC", "XZEC": "ZEC", "XXMR": "XMR", "XREP": "REP", "XMLN": "MLN",
    "ZUSD": "USD", "ZEUR": "EUR", "ZGBP": "GBP", "ZCAD": "CAD", "ZJPY": "JPY", "ZAUD": "AUD",
}
ALIASES = {"XBT": "BTC", "XDG": "DOGE", "ETH2": "ETH"}
FIAT = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"}
# .S staked, .M opt-in rewards, .F auto-earn, .B yield products, .P parachain, .T tokenized, .HOLD
SUFFIX = re.compile(r"\.[A-Z]+$")

TRADE_TYPES = {"trade", "spend", "receive", "conversion", "sale"}
INTERNAL_SUBTYPES = {
    "spottostaking", "stakingfromspot", "stakingtospot", "spotfromstaking",
    "allocation", "deallocation", "autoallocation", "migration",
}
REWARD_SUBTYPES = {"staking-rewards", "reward", "reward-bonus", "airdrop", "equity-dividend"}


def normalize_asset(code: str) -> str:
    c = SUFFIX.sub("", (code or "").strip().upper())
    return LEGACY.get(c) or ALIASES.get(c, c)


def stablecoins() -> set[str]:
    return {s.upper() for s in pricing().get("stablecoins") or []}


def sign(path: str, postdata: str, nonce: str, secret_b64: str) -> str:
    message = path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class KrakenError(RuntimeError):
    pass


class KrakenClient:
    def __init__(self, key: str | None = None, secret: str | None = None, http: httpx.Client | None = None,
                 sleep=time.sleep, pause_after: int = 5, pause_seconds: float = 6.0):
        self.key = key if key is not None else env("KRAKEN_API_KEY")
        self.secret = secret if secret is not None else env("KRAKEN_API_SECRET")
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": "portfolio-dashboard"})
        self.sleep = sleep
        # Ledgers costs 2 points against a ~15-point bucket that drains ~0.33/s: burst a few, then pace.
        self.pause_after, self.pause_seconds = pause_after, pause_seconds
        self._last_nonce = 0

    def _nonce(self) -> str:
        n = time.time_ns() // 1000  # microseconds, so the app and the Actions job rarely collide
        self._last_nonce = max(n, self._last_nonce + 1)
        return str(self._last_nonce)

    @staticmethod
    def _result(r: httpx.Response) -> dict:
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise KrakenError("; ".join(j["error"]))
        return j.get("result") or {}

    def public(self, method: str, **params) -> dict:
        return self._result(self.http.get(f"{API}/0/public/{method}", params=params))

    def private(self, method: str, **params) -> dict:
        if method not in READ_ONLY:
            raise KrakenError(f"refused: {method} is not a read-only call")
        path = f"/0/private/{method}"
        last = None
        for attempt in range(4):
            data = {"nonce": self._nonce(), **{k: v for k, v in params.items() if v is not None}}
            body = urlencode(data)
            headers = {
                "API-Key": self.key or "",
                "API-Sign": sign(path, body, data["nonce"], self.secret or ""),
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            }
            try:
                return self._result(self.http.post(API + path, content=body, headers=headers))
            except KrakenError as e:
                last = e
                if "Rate limit" in str(e) or "Invalid nonce" in str(e):
                    self.sleep(5 * (attempt + 1))
                    continue
                raise
        raise last  # type: ignore[misc]

    def balances(self) -> dict[str, str]:
        return self.private("Balance")

    def ledger(self, start: int | None = None) -> list[dict]:
        out, ofs, calls = [], 0, 0
        while True:
            if calls >= self.pause_after:
                self.sleep(self.pause_seconds)
            res = self.private("Ledgers", ofs=ofs, start=start)
            calls += 1
            page = res.get("ledger") or {}
            out.extend({"id": k, **v} for k, v in page.items())
            ofs += len(page)
            if not page or ofs >= int(res.get("count") or 0):
                break
        return out

    def usd_pairs(self) -> dict[str, str]:
        """Normalized base asset -> Kraken pair name quoted in USD (BTC -> XXBTZUSD)."""
        out: dict[str, str] = {}
        for name, p in self.public("AssetPairs").items():
            if name.endswith(".d") or p.get("quote") not in ("ZUSD", "USD"):
                continue
            out.setdefault(normalize_asset(p.get("base", "")), name)
        return out


# ---------------------------------------------------------------- normalization

def balances_to_holdings(balances: dict[str, str], stables: set[str]) -> tuple[list[m.Position], list[m.Cash]]:
    """Fold Kraken's per-wallet codes (XXBT, XBT.F, XBT.S ...) into one holding per coin; USD is cash."""
    qty: dict[str, float] = defaultdict(float)
    codes: dict[str, dict] = defaultdict(dict)
    for code, amount in balances.items():
        q = num(amount) or 0.0
        if q == 0:
            continue
        a = normalize_asset(code)
        qty[a] += q
        codes[a][code] = q
    cash = [m.Cash(currency="USD", amount=qty.pop("USD", 0.0))]
    positions = []
    for a, q in sorted(qty.items()):
        cls = m.CASH if a in FIAT else m.STABLECOIN if a in stables else m.CRYPTO
        positions.append(m.Position(symbol=a, quantity=q, asset_class=cls, name=a, meta={"kraken_codes": codes[a]}))
    return positions, cash


def _when(e: dict) -> datetime | None:
    t = num(e.get("time"))
    return datetime.fromtimestamp(t, tz=timezone.utc) if t else None


def _single(e: dict) -> m.Txn:
    a = normalize_asset(e.get("asset", ""))
    net = (num(e.get("amount")) or 0.0) - (num(e.get("fee")) or 0.0)
    t, sub = (e.get("type") or "").lower(), (e.get("subtype") or "").lower()
    usd = a == "USD"
    if sub in INTERNAL_SUBTYPES or (t == "earn" and sub not in REWARD_SUBTYPES):
        typ = m.INTERNAL
    elif t == "deposit":
        typ = m.DEPOSIT if usd else m.TRANSFER_IN
    elif t == "withdrawal":
        typ = m.WITHDRAWAL if usd else m.TRANSFER_OUT
    elif t in {"staking", "reward", "dividend", "airdrop"} or sub in REWARD_SUBTYPES:
        typ = m.INTEREST if usd else m.REWARD
    elif t == "transfer":
        typ = m.TRANSFER
    elif t in {"adjustment", "credit", "rollover", "settled", "margin"}:
        typ = m.ADJUSTMENT
    else:
        typ = m.OTHER
    return m.Txn(
        external_id=f"kraken:{e['id']}",
        type=typ,
        raw_type=f"{t}/{sub}" if sub else t,
        occurred_at=_when(e),
        symbol="" if usd else a,
        quantity=None if usd else net,
        amount=net if usd else None,
        fee=num(e.get("fee")) if usd else None,
        currency="USD" if usd else a,
        raw=e,
    )


def _trade(refid: str, legs: list[dict], stables: set[str]) -> list[m.Txn]:
    rows = [(normalize_asset(e.get("asset", "")), num(e.get("amount")) or 0.0, num(e.get("fee")) or 0.0, e) for e in legs]
    when = min((w for w in (_when(e) for e in legs) if w), default=None)
    raw_type = ",".join(sorted({(e.get("type") or "") for e in legs}))
    usd = [r for r in rows if r[0] == "USD"]
    coins = [r for r in rows if r[0] != "USD"]
    raw = {"legs": legs}

    if usd and len({r[0] for r in coins}) == 1:  # a plain USD buy or sell
        asset = coins[0][0]
        gross_q = sum(r[1] for r in coins)
        q = gross_q - sum(r[2] for r in coins)
        usd_gross, usd_fee = sum(r[1] for r in usd), sum(r[2] for r in usd)
        price = abs(usd_gross / gross_q) if gross_q else None
        return [m.Txn(
            external_id=f"kraken:{refid}", type=m.BUY if q > 0 else m.SELL, raw_type=raw_type, occurred_at=when,
            symbol=asset, quantity=q, price=price, amount=usd_gross - usd_fee, value_usd=abs(usd_gross),
            fee=usd_fee + sum(r[2] for r in coins) * (price or 0.0), raw=raw,
        )]

    # Coin for coin. A stablecoin leg prices the other side at ~$1 per unit.
    stable_value = next((abs(r[1]) for r in coins if r[0] in stables), None)
    by_asset: dict[str, list] = defaultdict(list)
    for r in coins:
        by_asset[r[0]].append(r)
    out = []
    for asset, group in by_asset.items():
        q = sum(r[1] - r[2] for r in group)
        value = abs(sum(r[1] for r in group)) if asset in stables else stable_value
        out.append(m.Txn(
            external_id=f"kraken:{refid}:{asset}", type=m.BUY if q > 0 else m.SELL, raw_type=raw_type,
            occurred_at=when, symbol=asset, quantity=q, price=value / abs(q) if value and q else None,
            value_usd=value, raw=raw, currency=asset,
        ))
    for r in usd:  # USD alongside several coins: keep the cash effect visible
        out.append(m.Txn(external_id=f"kraken:{refid}:USD", type=m.OTHER, raw_type=raw_type, occurred_at=when,
                         amount=r[1] - r[2], fee=r[2], raw=raw))
    return out


def ledger_to_txns(entries: list[dict], stables: set[str]) -> list[m.Txn]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e.get("refid") or e["id"]].append(e)
    out: list[m.Txn] = []
    for refid, legs in groups.items():
        if len(legs) >= 2 and any((e.get("type") or "").lower() in TRADE_TYPES for e in legs):
            out.extend(_trade(refid, legs, stables))
        else:
            out.extend(_single(e) for e in legs)
    return out


class KrakenSource:
    name = "kraken"
    derive_cost_basis = True
    ACCOUNT_ID = "kraken"

    def __init__(self, settings: dict | None = None, client: KrakenClient | None = None):
        self.settings = settings or {}
        self._client = client

    def missing_config(self) -> str | None:
        if self._client is None and not (env("KRAKEN_API_KEY") and env("KRAKEN_API_SECRET")):
            return "KRAKEN_API_KEY / KRAKEN_API_SECRET not set"
        return None

    @property
    def client(self) -> KrakenClient:
        if self._client is None:
            self._client = KrakenClient()
        return self._client

    def fetch(self, since: SinceFn, refresh: bool = False) -> list[m.AccountSnapshot]:
        acct = m.SourceAccount(external_id=self.ACCOUNT_ID, institution="Kraken", name="Kraken", raw_type="spot")
        snap = m.AccountSnapshot(account=acct, data_as_of=utcnow())
        stables = stablecoins()
        try:
            snap.positions, snap.cash = balances_to_holdings(self.client.balances(), stables)
        except Exception as e:  # noqa: BLE001
            snap.error = f"{type(e).__name__}: {e}"
            return [snap]
        try:
            last = since(self.ACCOUNT_ID)
            start = None
            if last:
                first_day = last - timedelta(days=int(self.settings.get("overlap_days", 3)))
                start = int(datetime.combine(first_day, dtime.min, tzinfo=timezone.utc).timestamp())
            snap.transactions = ledger_to_txns(self.client.ledger(start), stables)
        except Exception as e:  # noqa: BLE001
            snap.warnings.append(f"ledger: {type(e).__name__}: {e}")
        return [snap]
