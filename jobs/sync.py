"""Pull every account into the database.

    python -m jobs.sync                 # what the schedule runs
    python -m jobs.sync --dry-run       # fetch + price + map, print it, write nothing
    python -m jobs.sync --only kraken   # one source
    python -m jobs.sync --refresh       # ask SnapTrade to re-pull the brokers first
    python -m jobs.sync --force         # ignore min gaps and the empty-holdings guard
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from portfolio import sync


def _gh(level: str, msg: str) -> None:
    """Annotate the GitHub Actions run; plain text locally."""
    print(f"::{level}::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"[{level}] {msg}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["snaptrade", "kraken"])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--trigger", default="cli")
    ap.add_argument("--json", action="store_true", help="print the full summary as JSON")
    args = ap.parse_args(argv)

    res = sync.run(trigger=args.trigger, only=args.only, refresh=args.refresh, dry_run=args.dry_run, force=args.force)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"sync {res['status']}{' (dry run, nothing written)' if args.dry_run else ''}")
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
            print(f"  {key}: " + " · ".join(bits))
    for name, v in res["sources"].items():
        if v["status"] == "error":
            _gh("error", f"{name}: {v['error']}")
    for key, v in res["accounts"].items():
        if v["status"] in ("error", "suspect"):
            _gh("warning", f"{key}: {v.get('error')}")
        if v.get("reconcile"):
            _gh("notice", f"{key}: {v['reconcile']}")
        for w in v.get("warnings", []):
            _gh("warning", f"{key}: {w}")
    for w in res["warnings"]:
        _gh("warning", w)
    if res["status"] == "skipped":
        _gh("notice", "No source is configured yet (see README: secrets). Nothing to do.")
    return 1 if res["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
