"""Pull every account into the database.

    python -m jobs.sync                 # what the schedule runs
    python -m jobs.sync --dry-run       # fetch + price + map, print it, write nothing
    python -m jobs.sync --only kraken   # one source
    python -m jobs.sync --refresh       # ask SnapTrade to re-pull the brokers first
    python -m jobs.sync --force         # ignore min gaps and the empty-holdings guard

On GitHub Actions (or with --redact) the output carries no dollar amounts, symbols, account names or
error text, because Actions logs can be read by anyone who can see the repo. The full summary of every
run is kept in the database (sync_runs); `python -m jobs.sync` locally prints the same run in full.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys

from portfolio import sync


def _gh(level: str, msg: str) -> None:
    """Annotate the GitHub Actions run; plain text locally."""
    print(f"::{level}::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"[{level}] {msg}")


def _kind(text: str | None) -> str:
    """'ApiException (401) Reason: Unauthorized ...' -> 'ApiException'. Keeps the class, drops the detail."""
    return (text or "").split(":")[0].split(" ")[0] or "error"


def report_redacted(res: dict) -> None:
    print(f"sync {res['status']}")
    for name, v in res["sources"].items():
        reason = v.get("reason") or ""
        detail = (reason if "not set" in reason else "(synced recently)") if v["status"] == "skipped" else ""
        detail = detail or (_kind(v.get("error")) if v["status"] == "error" else "")
        print(f"  source {name}: {v['status']} {detail}".rstrip())
        if v["status"] == "error":
            _gh("error", f"{name} failed ({_kind(v.get('error'))}). Run python -m jobs.sync locally for details.")
    for key, v in res["accounts"].items():
        name = "an account not in config" if v.get("unmapped") else key  # auto keys end in account digits
        print(f"  {name}: {v['status']}")
        if v["status"] in ("error", "suspect"):
            _gh("warning", f"{name}: {v['status']}. Run python -m jobs.sync locally for details.")
    notes = len(res["warnings"]) + sum(len(v.get("warnings", [])) + bool(v.get("reconcile")) for v in res["accounts"].values())
    if notes:
        _gh("notice", f"{notes} note(s) from this run. Run python -m jobs.sync locally for details.")


def report_full(res: dict, dry_run: bool) -> None:
    print(f"sync {res['status']}{' (dry run, nothing written)' if dry_run else ''}")
    for name, v in res["sources"].items():
        print(f"  source {name}: {v['status']}  {v.get('reason') or v.get('error') or ''}".rstrip())
    for key, v in res["accounts"].items():
        bits = [v["status"]]
        if "total" in v:
            bits.append(f"${v['total']:,.2f}")
        for k in ("positions", "transactions", "changes"):
            if k in v:
                bits.append(f"{v[k]} {k}")
        if v.get("unmapped"):
            bits.append("NOT IN CONFIG")
        if v.get("error"):
            bits.append(v["error"])
        print(f"  {key}: " + " · ".join(bits))
        if v.get("reconcile"):
            print(f"    note: {v['reconcile']}")
        for w in v.get("warnings", []):
            print(f"    warning: {w}")
    for w in res["warnings"]:
        print(f"  warning: {w}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["snaptrade", "kraken"])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--trigger", default="cli")
    ap.add_argument("--redact", action="store_true", help="public-log mode (automatic on GitHub Actions)")
    ap.add_argument("--json", action="store_true", help="print the full summary as JSON")
    args = ap.parse_args(argv)
    redact = args.redact or bool(os.environ.get("GITHUB_ACTIONS"))

    kwargs = dict(trigger=args.trigger, only=args.only, refresh=args.refresh, dry_run=args.dry_run, force=args.force)
    if not redact:
        res = sync.run(**kwargs)
        if args.json:
            print(json.dumps(res, indent=2, default=str))
        else:
            report_full(res, args.dry_run)
    else:
        # Libraries (yfinance, SDK retries) print symbols and URLs; swallow all of it in public logs.
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                res = sync.run(**kwargs)
        except Exception as e:  # noqa: BLE001 - a traceback would print hostnames and data
            _gh("error", f"sync crashed ({type(e).__name__}). Run `python -m jobs.sync` locally for details.")
            return 1
        report_redacted(res)
    if res["status"] == "skipped":
        unset = all("not set" in (v.get("reason") or "") for v in res["sources"].values())
        _gh("notice", "No source is configured yet (see README: secrets). Nothing to do." if unset
            else "Every source synced within its minimum gap. Nothing to do this run.")
    return 1 if res["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
