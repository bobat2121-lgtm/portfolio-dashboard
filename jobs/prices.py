"""Fill in daily price and split history (the sync job also does this at the end of every run).

    python -m jobs.prices
"""
from __future__ import annotations

import sys

from portfolio import db, pricehist


def main() -> int:
    with db.session() as s:
        res = pricehist.update(s)
    print(f"price history: {res['symbols']} symbols tracked, {res['rows']} rows stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
