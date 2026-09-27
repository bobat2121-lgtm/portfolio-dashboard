"""Connect brokers and check what each source can see, before the first real sync.

    python -m jobs.link             # list SnapTrade connections + accounts and how they map to config
    python -m jobs.link --portal    # print a one-time SnapTrade link to connect a broker (read-only)
    python -m jobs.link --portal --broker ROBINHOOD
"""
from __future__ import annotations

import argparse
import sys

from portfolio.config import account_specs
from portfolio.sources.kraken import KrakenSource, balances_to_holdings, stablecoins
from portfolio.sources.snaptrade import SnapTradeSource, is_retirement
from portfolio.sync import map_accounts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--portal", action="store_true")
    ap.add_argument("--broker", help="SnapTrade broker slug to open straight to, e.g. FIDELITY or ROBINHOOD")
    args = ap.parse_args(argv)

    st = SnapTradeSource()
    if args.portal:
        if st.missing_config():
            print(st.missing_config())
            return 1
        print("Open this link in your browser to connect (read-only). It expires in a few minutes:\n")
        print(st.portal_url(broker=args.broker))
        print("\nOr connect from the SnapTrade dashboard directly. Then rerun `python -m jobs.link`.")
        return 0

    print("SnapTrade")
    if st.missing_config():
        print(f"  not configured: {st.missing_config()}")
    else:
        conns = st.connections()
        if not conns:
            print("  no brokers connected yet. Run `python -m jobs.link --portal`.")
        for c in conns:
            b = c.get("brokerage") or {}
            state = "DISABLED, reconnect it" if c.get("disabled") else "ok"
            print(f"  connection: {b.get('name') or c.get('name')}  [{state}]  id={c.get('id')}")
        accounts = st.list_accounts()
        warnings: list[str] = []
        mapping = map_accounts("snaptrade", accounts, account_specs(), {}, warnings)
        for a in accounts:
            key = mapping[a.external_id]
            tag = key if key in account_specs() else f"{key} (NOT IN CONFIG)"
            ret = "retirement" if is_retirement(a) else "taxable?"
            ready = "" if a.meta.get("holdings_ready") is not False else "  (first sync still running)"
            print(f"  account: {a.institution} | {a.name} | {a.raw_type} | …{a.number[-4:]} | {ret} -> {tag}{ready}")
            print(f"           id={a.external_id}")
        for w in warnings:
            print(f"  ! {w}")

    print("Kraken")
    kr = KrakenSource()
    if kr.missing_config():
        print(f"  not configured: {kr.missing_config()}")
    else:
        positions, cash = balances_to_holdings(kr.client.balances(), stablecoins())
        print(f"  key works: {len(positions)} coins, ${cash[0].amount:,.2f} USD cash")
        for p in positions:
            print(f"    {p.symbol}: {p.quantity:g}  (from {', '.join(p.meta['kraken_codes'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
