"""The made-up portfolio behind the header's Simulation switch, so the dashboard can be shown to anyone
without real balances: worth $32,571 today, in SPCX, MSTR, BTC, QQQ, an AAPL call and cash across four
accounts, with a year of buys, some sales (MSTR at a loss, SPCX and BTC at a gain), QQQ dividends and cash
interest, and invented daily prices for the charts. It's static: the same numbers every day, with its
dates ending today.

It's written into a throwaway SQLite file with the same tables the real sync fills, so every page reads it
exactly as it reads the real database (db.using switches a session over). Priced from its own closes,
never live quotes. Nothing here reads or writes the real database.
"""
from __future__ import annotations

import math
import random
import tempfile
import threading
from functools import lru_cache
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from portfolio import db, models as m
from portfolio.prices import KRAKEN, YAHOO, QuoteData
from portfolio.timeutil import today_ny, utcnow

VERSION = 2                       # bump to rebuild everyone's file after changing what's in here
TOTAL = 32_571.00                 # what the whole portfolio is worth today
START = date(2025, 5, 1)          # the price series start (a year+ of closes for betas)
OPTION = "AAPL  270115C00250000"  # AAPL $250 call, Jan 15 2027

ACCOUNTS = {  # key: (label, institution, tax, source, raw_type)
    "fidelity_taxable": ("Fidelity Taxable", "Fidelity", "taxable", "snaptrade", "INDIVIDUAL"),
    "robinhood_taxable": ("Robinhood Taxable", "Robinhood", "taxable", "snaptrade", "individual"),
    "robinhood_ira": ("Robinhood IRA", "Robinhood", "ira", "snaptrade", "ira_roth"),
    "kraken": ("Kraken", "Kraken", "taxable", "kraken", "spot"),
}
CASH = {"fidelity_taxable": 2_100.00, "robinhood_taxable": 1_200.12, "robinhood_ira": 338.25, "kraken": 0.0}

# Invented price paths: anchors (date, close), with a random walk pinned between them. "today" is the
# latest close (the last weekday for stocks).
PATHS = {  # yahoo symbol: (daily volatility, anchors)
    "MSTR": (0.040, [("2025-05-01", 380), ("2025-07-18", 450), ("2025-09-15", 330), ("2025-11-20", 205),
                     ("2026-02-05", 120), ("2026-04-15", 170), ("2026-06-22", 150), ("today", 161.20)]),
    "SPCX": (0.024, [("2025-05-01", 88), ("2025-09-02", 105), ("2025-12-01", 98), ("2026-03-02", 121),
                     ("2026-06-01", 138), ("2026-08-03", 131), ("today", 142.35)]),
    "BTC-USD": (0.024, [("2025-05-01", 96_000), ("2025-08-14", 123_000), ("2025-10-06", 125_000),
                        ("2025-11-21", 84_000), ("2026-02-06", 64_000), ("2026-04-10", 78_000),
                        ("2026-07-10", 90_000), ("today", 84_220)]),
    "QQQ": (0.009, [("2025-05-01", 520), ("2025-10-28", 632), ("2026-04-01", 568), ("2026-07-01", 598),
                    ("today", 612.80)]),
    "AAPL": (0.013, [("2025-05-01", 210), ("2025-09-02", 232), ("2025-12-01", 278), ("2026-03-02", 245),
                     ("2026-06-01", 219), ("today", 226.50)]),
    "SPY": (0.007, [("2025-05-01", 565), ("2025-12-31", 682), ("2026-03-16", 655), ("today", 712)]),
}
NAMES = {"MSTR": "Strategy Inc.", "SPCX": "SpaceX", "QQQ": "Invesco QQQ Trust", "BTC": "Bitcoin", OPTION: ""}

# (date, account, side, symbol, quantity); prices come from the paths on that day
TRADES = [
    ("2025-07-15", "robinhood_taxable", m.BUY, "SPCX", 20),
    ("2025-07-21", "robinhood_ira", m.BUY, "QQQ", 5),
    ("2025-08-12", "fidelity_taxable", m.BUY, "MSTR", 5),
    ("2025-09-02", "kraken", m.BUY, "BTC", 0.02),
    ("2025-10-02", "fidelity_taxable", m.BUY, "MSTR", 6),
    ("2025-11-03", "robinhood_taxable", m.BUY, "SPCX", 15),
    ("2025-12-10", "fidelity_taxable", m.BUY, "MSTR", 10),
    ("2025-12-15", "kraken", m.BUY, "BTC", 0.03),
    ("2026-01-12", "robinhood_ira", m.BUY, "QQQ", 4),
    ("2026-01-20", "fidelity_taxable", m.BUY, "MSTR", 12),
    ("2026-02-06", "kraken", m.BUY, "BTC", 0.035),
    ("2026-02-10", "fidelity_taxable", m.SELL, "MSTR", 8),      # sold near the lows: a loss
    ("2026-02-20", "robinhood_taxable", m.BUY, "SPCX", 15),
    ("2026-03-05", "fidelity_taxable", m.BUY, "MSTR", 8),
    ("2026-03-20", "kraken", m.BUY, "BTC", 0.02),
    ("2026-04-06", "robinhood_ira", m.BUY, "QQQ", 3),
    ("2026-05-12", "fidelity_taxable", m.BUY, "MSTR", 5),
    ("2026-05-18", "fidelity_taxable", m.BUY, OPTION, 2),       # 2 contracts at $13.20
    ("2026-06-15", "robinhood_taxable", m.SELL, "SPCX", 10),    # a gain
    ("2026-07-10", "kraken", m.SELL, "BTC", 0.0125),            # a gain
]
OPTION_PRICES = (13.20, 10.30, 9.85)  # paid per share, yesterday, today
DIVIDEND_DATES = ["2025-09-22", "2025-12-22", "2026-03-23", "2026-06-22", "2026-09-21"]  # QQQ, $0.65 a share
WATCH = {  # the top bar's watchlist: (price, previous close), made up
    ("yahoo", "SPCX"): None, ("yahoo", "QQQ"): None, ("yahoo", "SPY"): None,  # from the paths
    ("yahoo", "TSLA"): (318.40, 322.95), ("kraken", "ETH"): (3_105.20, 3_061.80), ("yahoo", "BMNR"): (31.12, 30.07),
    ("yahoo", "STRC"): (99.60, 99.44), ("yahoo", "SATA"): (100.02, 100.02), ("kraken", "ZEC"): (58.40, 60.25),
    ("yahoo", "^RUT"): (2_415.30, 2_401.62),
}

_lock = threading.Lock()


def _day(s: str, today: date) -> date:
    return today if s == "today" else date.fromisoformat(s)


def _last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@lru_cache(maxsize=4)
def closes(today: date) -> dict[str, dict[date, float]]:
    """symbol -> {day: close}: stocks on weekdays, BTC every day, pinned to the anchors."""
    out = {}
    for sym, (vol, anchors) in PATHS.items():
        crypto = sym.endswith("-USD")
        pts = [(_day(d, today) if crypto else _last_weekday(_day(d, today)), float(p)) for d, p in anchors]
        rnd = random.Random(f"{sym}-{VERSION}")
        series: dict[date, float] = {}
        for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
            n = max(1, (d1 - d0).days)
            walk = [0.0]
            for _ in range(n):
                walk.append(walk[-1] + rnd.gauss(0, vol))
            for k in range(n + 1):
                t = k / n
                bridge = walk[k] - t * walk[n]                 # zero at both anchors
                series[d0 + timedelta(days=k)] = math.exp(math.log(p0) + t * (math.log(p1) - math.log(p0)) + bridge)
        if not crypto:
            series = {d: v for d, v in series.items() if d.weekday() < 5}
        out[sym] = series
    return out


def _price_on(series: dict[date, float], d: date) -> float:
    while d not in series:
        d -= timedelta(days=1)
    return series[d]


def _yahoo(sym: str) -> str:
    return "BTC-USD" if sym == "BTC" else sym


def _utc(d: date, hour: int = 15) -> datetime:
    return datetime.combine(d, time(hour), tzinfo=timezone.utc)


def build(path: Path, today: date) -> None:
    """Write the simulated portfolio into a fresh SQLite file at `path`."""
    px = closes(today)
    now = utcnow()
    path.unlink(missing_ok=True)
    url = f"sqlite:///{path.as_posix()}"
    txns, lots = [], {}                                          # lots: (acct, sym) -> [[qty, cost], ...] FIFO
    cash = {k: 0.0 for k in ACCOUNTS}
    events = []
    for d, acct, side, sym, qty in TRADES:
        events.append((date.fromisoformat(d), 1, acct, side, sym, qty))
    for d in DIVIDEND_DATES:
        events.append((date.fromisoformat(d), 2, "robinhood_ira", m.DIVIDEND, "QQQ", None))
    month = date(2025, 7, 28)
    while month <= today:                                        # cash interest, last Monday-ish of each month
        events.append((month, 3, "fidelity_taxable", m.INTEREST, "", None))
        month = (month.replace(day=1) + timedelta(days=32)).replace(day=28)
    events.sort(key=lambda e: (e[0], e[1]))
    n = 0

    def add(d, acct, typ, sym="", qty=None, price=None, amount=None, value=None):
        nonlocal n
        n += 1
        txns.append({"account_key": acct, "external_id": f"sim-{n:04d}", "type": typ, "raw_type": typ.upper(),
                     "occurred_at": _utc(d), "trade_date": d, "settle_date": d, "symbol": sym, "quantity": qty,
                     "price": price, "amount": amount, "value_usd": value, "fee": 0.0, "currency": "USD",
                     "description": "", "raw": {}, "first_seen_at": now, "updated_at": now})

    for d, _, acct, typ, sym, qty in events:
        if typ in (m.BUY, m.SELL):
            mult = 100 if sym == OPTION else 1
            price = OPTION_PRICES[0] if sym == OPTION else round(_price_on(px[_yahoo(sym)], d), 2 if sym != "BTC" else 0)
            value = round(price * qty * mult, 2)
            book = lots.setdefault((acct, sym), [])
            if typ == m.BUY:
                if cash[acct] < value:                           # top the account up the day before
                    top = math.ceil((value - cash[acct]) / 250) * 250
                    add(d - timedelta(days=1), acct, m.DEPOSIT, amount=float(top), value=float(top))
                    cash[acct] += top
                book.append([qty, value])
                cash[acct] -= value
                add(d, acct, m.BUY, sym, qty, price, -value, value)
            else:
                left = qty
                while left > 1e-12:
                    take = min(left, book[0][0])
                    book[0][1] -= book[0][1] * take / book[0][0]
                    book[0][0] -= take
                    left -= take
                    if book[0][0] <= 1e-12:
                        book.pop(0)
                cash[acct] += value
                add(d, acct, m.SELL, sym, -qty, price, value, value)
        elif typ == m.DIVIDEND:
            held = sum(q for q, _ in lots.get((acct, "QQQ"), []))
            amt = round(held * 0.65, 2)
            if amt:
                cash[acct] += amt
                add(d, acct, m.DIVIDEND, "QQQ", amount=amt, value=amt)
        else:
            amt = round(max(1.0, cash[acct]) * 0.0035, 2)
            cash[acct] += amt
            add(d, acct, m.INTEREST, "SPAXX", amount=amt, value=amt)
    for acct, target in CASH.items():                            # land every account on its cash balance
        gap = round(target - cash[acct], 2)
        if abs(gap) >= 0.01:
            add(today - timedelta(days=9), acct, m.DEPOSIT if gap > 0 else m.WITHDRAWAL, amount=gap, value=abs(gap))
            cash[acct] = target

    # today's positions, priced from the paths (the option from its own made-up quote)
    positions = []
    for (acct, sym), book in lots.items():
        qty = round(sum(q for q, _ in book), 8)
        if qty <= 1e-9:
            continue
        cost = round(sum(c for _, c in book), 2)
        if sym == OPTION:
            price, prev, cls, mult = OPTION_PRICES[2], OPTION_PRICES[1], m.OPTION, 100.0
        else:
            series = px[_yahoo(sym)]
            last = max(series)
            price, prev = round(series[last], 2), round(series[max(d for d in series if d < last)], 2)
            cls, mult = (m.CRYPTO if sym == "BTC" else m.ETF if sym == "QQQ" else m.EQUITY), 1.0
        positions.append({"account_key": acct, "symbol": sym, "name": NAMES.get(sym, sym), "asset_class": cls,
                          "quantity": qty, "multiplier": mult, "broker_price": price, "price": price, "prev_close": prev,
                          "price_source": "sim", "price_as_of": now, "market_value": round(qty * mult * price, 2),
                          "cost_basis": cost, "currency": "USD", "in_cash_balance": False, "opened_at": now,
                          "closed_at": None, "updated_at": now, "meta": {}})
    # pin the total to TOTAL exactly: whatever the rounding left goes to Fidelity's cash
    invested = sum(p["market_value"] for p in positions)
    CASHX = dict(CASH)
    CASHX["fidelity_taxable"] = round(TOTAL - invested - sum(v for k, v in CASH.items() if k != "fidelity_taxable"), 2)
    if abs(CASHX["fidelity_taxable"] - CASH["fidelity_taxable"]) >= 0.01:
        gap = round(CASHX["fidelity_taxable"] - CASH["fidelity_taxable"], 2)
        add(today - timedelta(days=5), "fidelity_taxable", m.DEPOSIT if gap > 0 else m.WITHDRAWAL, amount=gap, value=abs(gap))

    with db.using(url):
        e = db.engine()
        with db.session() as s:
            for key, (label, inst, tax, source, raw) in ACCOUNTS.items():
                s.add(db.Account(key=key, label=label, institution=inst, tax=tax, source=source, external_id=f"sim-{key}",
                                 number_mask="SIM", raw_type=raw, mapped=True, data_as_of=now, last_synced_at=now,
                                 first_seen_at=_utc(date(2025, 6, 30)), meta={}))
            s.add_all(db.Position(**p) for p in positions)
            s.add_all(db.CashBalance(account_key=k, currency="USD", amount=v, updated_at=now) for k, v in CASHX.items() if v)
            s.add_all(db.Transaction(**t) for t in txns)
            for sym, series in px.items():
                years = lambda d: (d - START).days / 365.0  # noqa: E731 - SPY's dividends, as a total-return series
                s.add_all(db.PriceHistory(symbol=sym, date=d, close=round(v, 4),
                                          adj_close=round(v * (1 + 0.013 * years(d)), 4) if sym == "SPY" else round(v, 4),
                                          fetched_at=now) for d, v in series.items())
            s.add(db.SyncRun(started_at=now, finished_at=now, trigger="sim", status="ok", summary={}))
            _daily(s, px, txns, positions, CASHX, today, now)
            s.commit()
        e.dispose()


def _daily(s, px, txns, positions, cash_today, today, now) -> None:
    """End-of-day values per account (the Accounts chart) and each account's latest tick."""
    qty = {}
    cash = {k: 0.0 for k in ACCOUNTS}
    by_day = {}
    for t in txns:
        by_day.setdefault(t["trade_date"], []).append(t)
    opt_paid, opt_now = OPTION_PRICES[0], OPTION_PRICES[2]
    d, first = date(2025, 7, 1), min(t["trade_date"] for t in txns)
    d = min(d, first)
    snaps, ticks = [], []
    while d <= today:
        for t in by_day.get(d, []):
            a = t["account_key"]
            cash[a] += t["amount"] or 0.0
            if t["type"] in (m.BUY, m.SELL):
                qty[(a, t["symbol"])] = qty.get((a, t["symbol"]), 0.0) + t["quantity"]
        for a in ACCOUNTS:
            for (acct, sym), q in qty.items():
                if acct != a or q <= 1e-9:
                    continue
                if sym == OPTION:
                    bought = date(2026, 5, 18)
                    k = min(1.0, (d - bought).days / max(1, (today - bought).days))
                    price, mult, cls = opt_paid + (opt_now - opt_paid) * k, 100.0, m.OPTION
                else:
                    price, mult = _price_on(px[_yahoo(sym)], d), 1.0
                    cls = m.CRYPTO if sym == "BTC" else m.EQUITY
                snaps.append({"as_of": d, "account_key": a, "symbol": sym, "asset_class": cls, "quantity": q,
                              "price": price, "market_value": q * mult * price, "cost_basis": None,
                              "in_cash_balance": False, "taken_at": now})
            if abs(cash[a]) >= 0.005:
                snaps.append({"as_of": d, "account_key": a, "symbol": "CASH:USD", "asset_class": m.CASH, "quantity": cash[a],
                              "price": 1.0, "market_value": cash[a], "cost_basis": None, "in_cash_balance": False,
                              "taken_at": now})
        d += timedelta(days=1)
    db.upsert(s, db.HoldingSnapshot, snaps, keys=["as_of", "account_key", "symbol"])
    for a in ACCOUNTS:
        invested = sum(p["market_value"] for p in positions if p["account_key"] == a)
        day = sum((p["price"] - p["prev_close"]) * p["quantity"] * p["multiplier"] for p in positions if p["account_key"] == a)
        ticks.append(db.ValueTick(account_key=a, taken_at=now, invested=invested, cash=cash_today.get(a, 0.0),
                                  total=invested + cash_today.get(a, 0.0), day_change=day, unpriced=0))
    s.add_all(ticks)


def db_url(today: date | None = None) -> str:
    """The simulated database for today, built on first use (one small file per day, per version)."""
    today = today or today_ny()
    path = Path(tempfile.gettempdir()) / f"btc_supernova_sim_v{VERSION}_{today:%Y%m%d}.db"
    with _lock:
        if not path.exists():
            tmp = path.with_suffix(".building")
            build(tmp, today)
            tmp.replace(path)
    return f"sqlite:///{path.as_posix()}"


def quotes(today: date | None = None) -> dict[tuple[str, str], QuoteData]:
    """Made-up 'live' quotes: the holdings' last two closes from the paths, BTC, and the watchlist."""
    today = today or today_ny()
    px, now = closes(today), utcnow()

    def last_two(sym):
        series = px[sym]
        last = max(series)
        return round(series[last], 2), round(series[max(d for d in series if d < last)], 2)

    out = {}
    for venue, sym, ysym in ((YAHOO, "MSTR", "MSTR"), (YAHOO, "SPCX", "SPCX"), (YAHOO, "QQQ", "QQQ"), (YAHOO, "SPY", "SPY"),
                             (YAHOO, "AAPL", "AAPL"), (KRAKEN, "BTC", "BTC-USD")):
        p, prev = last_two(ysym)
        out[(venue, sym)] = QuoteData(venue, sym, p, prev, now)
    for key, v in WATCH.items():
        if v is not None and key not in out:
            out[key] = QuoteData(key[0], key[1], v[0], v[1], now)
    return out
