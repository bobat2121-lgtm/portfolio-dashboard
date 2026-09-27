"""Make a read-only database login for the dashboard (run once, locally, after DATABASE_URL is in .env).

    python -m jobs.readonly_login

Creates the tables if needed, then a Postgres role `portfolio_reader` that can only SELECT, with a fresh
random password. Its connection string is written into .env as DATABASE_URL_READONLY and never printed.
Paste that value into the Streamlit app's secrets as DATABASE_URL. Rerun to rotate the password.
"""
from __future__ import annotations

import re
import secrets
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from portfolio import db
from portfolio.config import ROOT, env

ROLE = "portfolio_reader"


def _write_env(name: str, value: str) -> None:
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [ln for ln in lines if not re.match(rf"\s*{name}\s*=", ln)] + [f"{name}={value}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not env("DATABASE_URL") or not db.is_postgres():
        print("Put your Neon owner connection string in .env as DATABASE_URL first.")
        return 1
    owner = db.engine()  # creates any missing tables as the owner
    url = make_url(db.db_url())
    password = secrets.token_urlsafe(32)
    with owner.begin() as c:
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}).scalar()
        verb = "ALTER" if exists else "CREATE"
        c.execute(text(f"{verb} ROLE {ROLE} WITH LOGIN PASSWORD '{password}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{url.database}" TO {ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))
        c.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}"))
        c.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE}"))
        c.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {ROLE}"))

    reader = url.set(username=ROLE, password=password)
    check = create_engine(reader)
    with check.connect() as c:
        c.execute(text("SELECT count(*) FROM accounts")).scalar()
        try:
            c.execute(text("UPDATE accounts SET label = label"))
            print("STOP: the reader login could write. Don't use it; tell Claude.")
            return 1
        except Exception:  # noqa: BLE001 - expected: permission denied
            pass
    check.dispose()

    plain = reader.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://", 1)
    _write_env("DATABASE_URL_READONLY", plain)
    print(f"Done. Role {ROLE} can read and cannot write (checked). Its connection string is in .env as")
    print("DATABASE_URL_READONLY. Paste that value into Streamlit Cloud -> app -> Settings -> Secrets as DATABASE_URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
