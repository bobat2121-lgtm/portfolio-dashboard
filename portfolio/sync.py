"""One sync: pull every source, map what it finds onto your accounts, price it, and record it.

Called by the scheduled GitHub Action (`python -m jobs.sync`) and by the dashboard's Sync button.
Nothing is ever deleted. A failed or suspicious fetch leaves the stored holdings alone instead of
treating them as sold.
"""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import func, select

from portfolio import db, models as m, prices
from portfolio.config import account_specs, source_settings
from portfolio.costbasis import average_cost, basis_for
from portfolio.sources import Source, all_sources
from portfolio.sources.snaptrade import is_retirement
from portfolio.timeutil import aware, today_ny, trade_day, utcnow

EPS = 1e-9
CASH_PREFIX = "CASH:"  # holding_snapshots symbol for a cash balance, e.g. CASH:USD


# ---------------------------------------------------------------- account mapping

def matches(rule: dict, acct: m.SourceAccount) -> bool:
    """rule keys (all optional): id, institution, retirement, number_last4, name_contains"""
    if rule.get("id"):
        return acct.external_id == str(rule["id"])
    if rule.get("institution") and str(rule["institution"]).lower() not in acct.institution.lower():
        return False
    if rule.get("number_last4") and not acct.number.endswith(str(rule["number_last4"])):
        return False
    if "retirement" in rule and bool(rule["retirement"]) != is_retirement(acct):
        return False
    if rule.get("name_contains") and str(rule["name_contains"]).lower() not in f"{acct.name} {acct.raw_type}".lower():
        return False
    return True


def auto_key(source: str, acct: m.SourceAccount) -> str:
    inst = re.sub(r"[^a-z0-9]+", "_", acct.institution.lower()).strip("_") or source
    tail = acct.number[-4:] if len(acct.number) >= 4 else acct.external_id[:8]
    return f"{inst}_{tail}".lower()


def map_accounts(source: str, accounts: list[m.SourceAccount], specs: dict[str, dict],
                 known: dict[str, str], warnings: list[str]) -> dict[str, str]:
    """external_id -> account key. Config rules first, then whatever key the account had before,
    then an auto key (e.g. fidelity_1234) so an account you didn't list is still tracked."""
    here = {k: v for k, v in specs.items() if v.get("source") == source}
    out: dict[str, str] = {}
    for key, spec in here.items():  # pinned ids win
        pinned = (spec.get("match") or {}).get("id")
        for a in accounts:
            if pinned and a.external_id == str(pinned):
                out[a.external_id] = key
    for key, spec in here.items():
        if key in out.values():
            continue
        cands = [a for a in accounts if a.external_id not in out and matches(spec.get("match") or {}, a)]
        if len(cands) == 1:
            out[cands[0].external_id] = key
        elif len(cands) > 1:
            names = ", ".join(f"{a.institution} {a.name} …{a.number[-4:]}" for a in cands)
            warnings.append(f"{key}: {len(cands)} accounts match ({names}). Pin one with match.number_last4.")
    for a in accounts:
        if a.external_id not in out:
            prior = known.get(a.external_id)
            out[a.external_id] = prior if prior and prior not in out.values() else auto_key(source, a)
    return out


# ---------------------------------------------------------------- helpers

def _merge_positions(positions: list[m.Position]) -> list[m.Position]:
    merged: dict[str, m.Position] = {}
    for p in positions:
        if p.symbol not in merged:
            merged[p.symbol] = p
            continue
        cur = merged[p.symbol]
        cur.cost_basis = None if cur.cost_basis is None or p.cost_basis is None else cur.cost_basis + p.cost_basis
        cur.quantity += p.quantity
        cur.price = cur.price if cur.price is not None else p.price
    return [p for p in merged.values() if abs(p.quantity) > EPS]


def _txn_rows(key: str, txns: list[m.Txn], now) -> list[dict]:
    rows: dict[str, dict] = {}
    for t in txns:
        rows[t.external_id] = {
            "account_key": key, "external_id": t.external_id[:160], "type": t.type,
            "raw_type": (t.raw_type or "")[:60], "occurred_at": t.occurred_at, "trade_date": trade_day(t.occurred_at),
            "settle_date": t.settle_date, "symbol": (t.symbol or "")[:64], "quantity": t.quantity,
            "price": t.price, "amount": t.amount, "value_usd": t.value_usd, "fee": t.fee,
            "currency": (t.currency or "USD")[:10], "description": t.description or "", "raw": t.raw or {},
            "first_seen_at": now, "updated_at": now,
        }
    return list(rows.values())


def _since_fn(s, source: str):
    rows = s.execute(
        select(db.Account.external_id, func.max(db.Transaction.trade_date))
        .join(db.Transaction, db.Transaction.account_key == db.Account.key)
        .where(db.Account.source == source)
        .group_by(db.Account.external_id)
    ).all()
    last = {ext: d for ext, d in rows}
    return lambda external_id: last.get(external_id)


def _too_soon(s, src: Source, force: bool) -> str | None:
    minutes = float(source_settings(src.name).get("min_minutes_between_syncs", 0) or 0)
    if force or minutes <= 0:
        return None
    last = s.scalar(select(func.max(db.Account.last_synced_at)).where(db.Account.source == src.name))
    last = aware(last)
    if last and utcnow() - last < timedelta(minutes=minutes):
        ago = (utcnow() - last).total_seconds() / 60
        return f"synced {ago:.0f} min ago (minimum gap {minutes:.0f} min)"
    return None


# ---------------------------------------------------------------- writing one account

def write_account(s, key: str, spec: dict, source: Source, snap: m.AccountSnapshot, quotes: dict,
                  run_id: int | None, now, force: bool = False) -> dict:
    a = snap.account
    row = s.get(db.Account, key) or db.Account(key=key, first_seen_at=now)
    prev_synced = row.last_synced_at
    row.label = spec.get("label") or row.label or f"{a.institution} {a.name}".strip()
    row.institution = spec.get("institution") or a.institution
    row.tax = spec.get("tax") or row.tax or ("ira" if is_retirement(a) else "unknown")
    row.source, row.external_id, row.mapped = source.name, a.external_id, bool(spec)
    row.number_mask = a.number[-4:] if a.number else ""
    row.raw_type, row.broker_total, row.meta = a.raw_type, a.broker_total, a.meta
    s.add(row)
    if snap.error:
        row.last_error = snap.error
        return {"status": "error", "error": snap.error}
    row.last_error = "; ".join(snap.warnings)

    txn_rows = _txn_rows(key, snap.transactions, now)
    db.upsert(s, db.Transaction, txn_rows, keys=["account_key", "external_id"], keep=("first_seen_at",))
    s.flush()
    if not snap.holdings_ready:
        return {"status": "pending", "note": "broker still on its first sync", "transactions": len(txn_rows)}

    positions = _merge_positions(snap.positions)
    if source.derive_cost_basis:
        bases = average_cost(s.scalars(select(db.Transaction).where(db.Transaction.account_key == key)).all())
        overrides = {str(k).upper(): float(v) for k, v in (spec.get("cost_basis_overrides") or {}).items()}
        for p in positions:
            p.cost_basis = overrides.get(p.symbol.upper(), basis_for(p.quantity, bases.get(p.symbol)))

    existing = {p.symbol: p for p in s.scalars(select(db.Position).where(db.Position.account_key == key)).all()}
    prev_open = {sym: p for sym, p in existing.items() if p.closed_at is None and abs(p.quantity) > EPS}
    usd_cash = sum(c.amount for c in snap.cash if c.currency == "USD")
    if not positions and prev_open and not force:
        broker_says_invested = a.broker_total is not None and a.broker_total > usd_cash + 1
        nothing_at_all = a.broker_total is None and not any(abs(c.amount) > EPS for c in snap.cash)
        if broker_says_invested or nothing_at_all:
            msg = "returned no positions while you held some; kept the previous holdings (run with --force if real)"
            row.last_error = msg
            return {"status": "suspect", "error": msg, "transactions": len(txn_rows)}

    # price everything
    valued = []
    for p in positions:
        venue = prices.venue_for(p.asset_class, p.symbol, source.name)
        price, prev, psrc, as_of, mv = prices.valuation(p, venue, quotes.get((venue, p.symbol)))
        valued.append((p, price, prev, psrc, as_of, mv))
    counted = [v for v in valued if not v[0].in_cash_balance]
    cash_like = usd_cash + sum(v[5] or 0.0 for v in counted if v[0].asset_class == m.CASH)
    invested = sum(v[5] or 0.0 for v in counted if v[0].asset_class != m.CASH)
    day_change = sum(p.quantity * p.multiplier * (price - prev) for p, price, prev, *_ in counted
                     if price is not None and prev is not None)
    unpriced = sum(1 for v in valued if v[5] is None)
    broker_view = usd_cash + sum(p.quantity * p.multiplier * (p.price if p.price is not None else (price or 0.0))
                                 for p, price, *_ in counted)

    # what changed since the last sync (skipped on an account's very first sync: no baseline yet)
    changes = 0
    if prev_synced is not None:
        new_qty = {p.symbol: p.quantity for p, *_ in valued if p.asset_class != m.CASH}
        prices_now = {p.symbol: price for p, price, *_ in valued}
        for sym in sorted(set(new_qty) | {k for k, v in prev_open.items() if v.asset_class != m.CASH}):
            before = prev_open[sym].quantity if sym in prev_open else 0.0
            after = new_qty.get(sym, 0.0)
            if abs(after - before) <= EPS * max(1.0, abs(before)):
                continue
            kind = "opened" if abs(before) <= EPS else "closed" if abs(after) <= EPS else "added" if after > before else "trimmed"
            px = prices_now.get(sym) or (prev_open[sym].price if sym in prev_open else None)
            mult = prev_open[sym].multiplier if sym in prev_open else next((p.multiplier for p, *_ in valued if p.symbol == sym), 1.0)
            s.add(db.Change(account_key=key, detected_at=now, sync_run_id=run_id, symbol=sym, kind=kind,
                            qty_before=before, qty_after=after, price=px,
                            value_delta=(after - before) * mult * px if px is not None else None))
            changes += 1
        prev_cash = sum(c.amount for c in s.scalars(select(db.CashBalance).where(
            db.CashBalance.account_key == key, db.CashBalance.currency == "USD")).all())
        prev_cash += sum((p.market_value or 0.0) for p in prev_open.values() if p.asset_class == m.CASH and not p.in_cash_balance)
        if abs(cash_like - prev_cash) >= 0.01:
            s.add(db.Change(account_key=key, detected_at=now, sync_run_id=run_id, symbol="USD",
                            kind="cash_in" if cash_like > prev_cash else "cash_out",
                            qty_before=prev_cash, qty_after=cash_like, price=1.0, value_delta=cash_like - prev_cash))
            changes += 1

    # current positions (exits stay as quantity-0 rows)
    for p, price, prev, psrc, as_of, mv in valued:
        r = existing.get(p.symbol) or db.Position(account_key=key, symbol=p.symbol, opened_at=now)
        if r.closed_at is not None or abs(r.quantity or 0.0) <= EPS:
            r.opened_at, r.closed_at = now, None
        r.name, r.asset_class, r.quantity, r.multiplier = p.name, p.asset_class, p.quantity, p.multiplier
        r.broker_price, r.price, r.prev_close, r.price_source, r.price_as_of = p.price, price, prev, psrc, as_of
        r.market_value, r.cost_basis, r.currency, r.in_cash_balance = mv, p.cost_basis, p.currency, p.in_cash_balance
        r.updated_at, r.meta = now, p.meta
        s.add(r)
    held = {p.symbol for p in positions}
    closed_now = [r for sym, r in prev_open.items() if sym not in held]
    for r in closed_now:
        r.quantity, r.market_value, r.closed_at, r.updated_at = 0.0, 0.0, now, now

    cash_rows = {c.currency: c for c in s.scalars(select(db.CashBalance).where(db.CashBalance.account_key == key)).all()}
    new_cash = {c.currency: c for c in snap.cash}
    for cur, c in new_cash.items():
        r = cash_rows.get(cur) or db.CashBalance(account_key=key, currency=cur)
        r.amount, r.buying_power, r.updated_at = c.amount, c.buying_power, now
        s.add(r)
    for cur, r in cash_rows.items():
        if cur not in new_cash and r.amount:
            r.amount, r.updated_at = 0.0, now

    # today's end-of-day row per holding (later syncs today overwrite; exits today are written as 0)
    day = today_ny()
    snap_rows = [{
        "as_of": day, "account_key": key, "symbol": p.symbol, "asset_class": p.asset_class, "quantity": p.quantity,
        "price": price, "market_value": mv, "cost_basis": p.cost_basis, "in_cash_balance": p.in_cash_balance,
        "taken_at": now,
    } for p, price, prev, psrc, as_of, mv in valued]
    snap_rows += [{
        "as_of": day, "account_key": key, "symbol": r.symbol, "asset_class": r.asset_class, "quantity": 0.0,
        "price": r.price, "market_value": 0.0, "cost_basis": None, "in_cash_balance": r.in_cash_balance, "taken_at": now,
    } for r in closed_now]
    all_cash = set(new_cash) | set(cash_rows)
    snap_rows += [{
        "as_of": day, "account_key": key, "symbol": f"{CASH_PREFIX}{cur}", "asset_class": m.CASH,
        "quantity": new_cash[cur].amount if cur in new_cash else 0.0, "price": 1.0,
        "market_value": (new_cash[cur].amount if cur in new_cash else 0.0) if cur == "USD" else None,
        "cost_basis": None, "in_cash_balance": False, "taken_at": now,
    } for cur in sorted(all_cash)]
    db.upsert(s, db.HoldingSnapshot, snap_rows, keys=["as_of", "account_key", "symbol"])

    s.add(db.ValueTick(account_key=key, taken_at=now, invested=invested, cash=cash_like, total=invested + cash_like,
                       day_change=day_change, broker_total=a.broker_total, unpriced=unpriced, sync_run_id=run_id))
    row.last_synced_at, row.data_as_of = now, snap.data_as_of

    out = {"status": "ok", "total": round(invested + cash_like, 2), "positions": len(positions),
           "transactions": len(txn_rows), "changes": changes, "unpriced": unpriced,
           "data_as_of": snap.data_as_of.isoformat() if snap.data_as_of else None}
    if a.broker_total and abs(broker_view - a.broker_total) > max(0.01 * abs(a.broker_total), 5.0):
        out["reconcile"] = f"broker says ${a.broker_total:,.2f}, holdings add up to ${broker_view:,.2f}"
    if snap.warnings:
        out["warnings"] = snap.warnings
    return out


# ---------------------------------------------------------------- the run

def run(trigger: str = "cli", only: str | None = None, refresh: bool = False, dry_run: bool = False,
        force: bool = False, sources: list[Source] | None = None, quote_fn=None) -> dict:
    """refresh: ask SnapTrade to re-pull brokers first. force: ignore min gaps and the empty-holdings guard.
    dry_run: do everything, then roll back."""
    started = utcnow()
    summary: dict = {"trigger": trigger, "sources": {}, "accounts": {}, "warnings": []}
    specs = account_specs()
    sources = sources if sources is not None else all_sources(only)
    quote_fn = quote_fn or prices.get_quotes
    s = db.session()
    run_id = None
    try:
        if not dry_run:
            r = db.SyncRun(started_at=started, trigger=trigger)
            s.add(r)
            s.commit()
            run_id = r.id

        fetched: list[tuple[Source, m.AccountSnapshot]] = []
        for src in sources:
            reason = src.missing_config() or _too_soon(s, src, force)
            if reason:
                summary["sources"][src.name] = {"status": "skipped", "reason": reason}
                continue
            mode = source_settings(src.name).get("refresh", "manual")
            try:
                snaps = src.fetch(since=_since_fn(s, src.name),
                                  refresh=mode == "always" or (refresh and mode != "never"))
            except Exception as e:  # noqa: BLE001
                summary["sources"][src.name] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
                continue
            summary["sources"][src.name] = {"status": "ok", "accounts": len(snaps)}
            fetched.extend((src, sn) for sn in snaps)

        # map provider accounts onto config keys
        keyed: list[tuple[str, dict, Source, m.AccountSnapshot]] = []
        for src in {id(x): x for x, _ in fetched}.values():
            snaps = [sn for x, sn in fetched if x is src]
            known = dict(s.execute(select(db.Account.external_id, db.Account.key)  # mapped rows sort last and win
                                   .where(db.Account.source == src.name).order_by(db.Account.mapped)).all())
            mapping = map_accounts(src.name, [sn.account for sn in snaps], specs, known, summary["warnings"])
            for sn in snaps:
                key = mapping[sn.account.external_id]
                keyed.append((key, specs.get(key) or {}, src, sn))

        # one batched quote call per venue for everything we hold
        wanted = set()
        for _, _, src, sn in keyed:
            for p in sn.positions:
                venue = prices.venue_for(p.asset_class, p.symbol, src.name)
                if venue in (prices.YAHOO, prices.KRAKEN):
                    wanted.add((venue, p.symbol))
        quotes, quote_errors = quote_fn(wanted) if wanted else ({}, [])
        summary["warnings"].extend(quote_errors)
        now = utcnow()
        if quotes:
            db.upsert(s, db.Quote, [{"venue": q.venue, "symbol": q.symbol, "price": q.price, "prev_close": q.prev_close,
                                     "as_of": q.as_of, "fetched_at": now} for q in quotes.values()],
                      keys=["venue", "symbol"])
            if not dry_run:
                s.commit()

        for key, spec, src, sn in keyed:
            try:
                res = write_account(s, key, spec, src, sn, quotes, run_id, now, force=force)
                if dry_run:
                    s.rollback()
                else:
                    s.commit()
            except Exception as e:  # noqa: BLE001
                s.rollback()
                res = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            if not spec:
                res["unmapped"] = True
            summary["accounts"][key] = res

        if not dry_run and any(v["status"] == "ok" for v in summary["sources"].values()):
            try:  # daily closes + splits for the history, taxes and what-if views
                from portfolio import pricehist

                summary["prices"] = pricehist.update(s)
            except Exception as e:  # noqa: BLE001 - prices are a nice-to-have; the sync itself succeeded
                s.rollback()
                summary["warnings"].append(f"price history: {type(e).__name__}: {e}")

        for name, v in summary["sources"].items():
            if v["status"] == "ok" and not v["accounts"]:
                summary["warnings"].append(f"{name} returned no accounts. Connect a broker first (python -m jobs.link).")
        attempted = [v for v in summary["sources"].values() if v["status"] != "skipped"]
        bad_sources = [v for v in attempted if v["status"] == "error"]
        results = list(summary["accounts"].values())
        bad_accounts = [v for v in results if v["status"] in ("error", "suspect")]
        if not attempted:
            status = "skipped"
        elif (bad_sources and not results) or (results and all(v["status"] == "error" for v in results)):
            status = "error"
        elif bad_sources or bad_accounts:
            status = "partial"
        else:
            status = "ok"
        summary["status"] = status
        if not dry_run:
            r = s.get(db.SyncRun, run_id)
            r.finished_at, r.status, r.summary = utcnow(), status, summary
            s.commit()
        return summary
    finally:
        s.close()
