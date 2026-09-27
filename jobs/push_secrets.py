"""Copy the sync job's secrets from your .env into this repo's GitHub Actions secrets.

    python -m jobs.push_secrets --dry-run   # which names would be sent (values never shown)
    python -m jobs.push_secrets             # send them (uses your `gh` login)

Only the names the Action needs are sent. APP_PASSWORD and DATABASE_URL_READONLY stay local: those go to
Streamlit, not GitHub. Values are piped straight to `gh` and never printed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from dotenv import dotenv_values

from portfolio.config import ROOT

REPO = "bobat2121-lgtm/portfolio-dashboard"
NAMES = ["DATABASE_URL", "SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "KRAKEN_API_KEY", "KRAKEN_API_SECRET",
         "PORTFOLIO_CONFIG"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    values = dotenv_values(ROOT / ".env")
    url = values.get("DATABASE_URL") or ""
    if url and "-pooler" in url:
        print("  note: DATABASE_URL is Neon's pooled address. Use the one with Connection pooling OFF.")
    if url and not url.startswith(("postgres://", "postgresql://")):
        print("  stop: DATABASE_URL doesn't look like a Postgres connection string.")
        return 1
    sent = 0
    for name in NAMES:
        value = (values.get(name) or "").strip()
        if not value:
            print(f"  skip {name} (empty in .env)")
            continue
        if args.dry_run:
            print(f"  would set {name}")
            continue
        r = subprocess.run(["gh", "secret", "set", name, "--repo", REPO], input=value, text=True, capture_output=True)
        if r.returncode:
            print(f"  FAILED {name}: gh exited {r.returncode}")
            return 1
        print(f"  set {name}")
        sent += 1
    if not args.dry_run:
        print(f"{sent} secret(s) set on {REPO}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
