# Portfolio Dashboard

One view of Fidelity (taxable), Robinhood (taxable + IRA) and Kraken. A scheduled job pulls every
account into Postgres, so buys, sells and deposits show up without you doing anything. It keeps a
permanent history of holdings, values and transactions.

```
 Fidelity ─┐                                             ┌─ GitHub Actions (every 30 min in market hours,
 Robinhood ┼─ SnapTrade Personal (free, read-only) ─┐    │  every 4 h otherwise): python -m jobs.sync
 RH IRA ───┘                                        ├─ portfolio/sync.py ─→ Neon Postgres ─→ Streamlit app
 Kraken ─────── Kraken API (read-only key) ─────────┘    │                                    (+ "Sync now")
 Yahoo / Kraken ticker ── live prices between syncs ─────┘
```

## How fresh is the data?

| | Holdings & cash | Transactions |
|---|---|---|
| **Kraken** | live on every sync | live (full ledger) |
| **Fidelity / Robinhood via SnapTrade** | live or cached daily, depending on SnapTrade's plan and the broker; each sync records SnapTrade's `as of` time | once a day, a day late (SnapTrade's rule for every plan) |

Two things cover the gap:
- **Change detection.** Every sync compares holdings and cash with the previous sync and records what
  moved in the `changes` table ("opened STRC +30", "cash_in +$5,000"). A buy, sell or deposit shows up as
  soon as a sync sees the new holdings, before the broker posts the transaction.
- **Live prices.** Values are re-priced from Yahoo (stocks, ETFs, funds) and Kraken's public ticker
  (crypto) on every sync and every time the dashboard opens. Broker prices are the fallback.

The dashboard's **Sync now** button also asks SnapTrade to re-pull the brokers (`refresh: manual` in config).

## Setup (one time)

**1. Database.** In Neon, create a database named `portfolio`. A new database inside your existing Neon
project is fine. Copy its connection string: that's `DATABASE_URL`.

**2. SnapTrade (Fidelity + Robinhood).** Sign up at <https://dashboard.snaptrade.com/signup>. The
Personal plan is free and needs no card. Create a Personal API key and copy the client ID and consumer
key. Connect Fidelity and Robinhood with **read-only** access, either from the SnapTrade dashboard or
with `python -m jobs.link --portal`. Your broker passwords go to SnapTrade's portal, never into this app.

**3. Kraken.** Go to Settings → API → Create key. Tick **only** *Query Funds* and *Query Ledger Entries*.
No trading, no withdrawals. Copy the key and the private key.

**4. Try it locally.**
```powershell
copy .env.example .env        # fill in the values from steps 1-3 (DATABASE_URL optional locally)
.venv\Scripts\python -m jobs.link             # shows each linked account and which config key it maps to
.venv\Scripts\python -m jobs.sync --dry-run   # fetch + price everything, write nothing
.venv\Scripts\python -m jobs.sync             # first real sync (reads your whole Kraken ledger: a few minutes)
.venv\Scripts\streamlit run streamlit_app.py
```
If `jobs.link` says an account is `NOT IN CONFIG` or ambiguous, edit `match:` in
[config/portfolio.yaml](config/portfolio.yaml). The easiest fix is `number_last4`.

**5. GitHub.** Push to a **private** repo. Add Actions secrets `DATABASE_URL`, `SNAPTRADE_CLIENT_ID`,
`SNAPTRADE_CONSUMER_KEY`, `KRAKEN_API_KEY` and `KRAKEN_API_SECRET`. The `sync` workflow then runs on
its schedule. You can also run it by hand from the Actions tab, with an optional SnapTrade refresh.

**6. Streamlit Cloud.** Deploy from the private repo with main file `streamlit_app.py` and Python 3.12.
Paste the secrets from [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) and include
`APP_PASSWORD`. Under Sharing, pick "Only specific people can view this app".

## Commands

| | |
|---|---|
| `python -m jobs.sync` | pull everything (what the schedule runs) |
| `python -m jobs.sync --dry-run` | do it all, print it, roll it back |
| `python -m jobs.sync --only kraken` | one source |
| `python -m jobs.sync --refresh` | ask SnapTrade to re-pull brokers first |
| `python -m jobs.sync --force` | skip the min-gap and empty-holdings guard |
| `python -m jobs.link [--portal]` | check connections and account mapping / get a connect link |
| `python -m jobs.demo` | made-up data in `data/demo.db` for UI work, then `streamlit run streamlit_app.py -- --demo` |
| `python -m pytest` | tests (no network) |

## What's stored

Nothing is ever deleted. When you exit a position, its row stays at quantity 0 with a `closed_at` date.

| Table | One row per | Used for |
|---|---|---|
| `accounts` | account | name, tax type, last sync, SnapTrade's as-of time, last error |
| `positions` | account + symbol | current holdings, price source, cost basis |
| `cash_balances` | account + currency | cash |
| `holding_snapshots` | day + account + symbol | daily history. The day's last sync wins; exits are written as 0 |
| `value_ticks` | account + sync | intraday value line, day change, and the broker's own total for reconciling |
| `transactions` | account + provider id | buys, sells, dividends, deposits and withdrawals (your contributions), fees, rewards |
| `changes` | detected move | instant buy/sell/deposit feed |
| `quotes` | venue + symbol | last live price |
| `sync_runs` | run | status and full summary of every run |

## Safety rails

- **A fetch that fails, or comes back empty while the broker still reports a balance, never counts
  as "sold everything".** The previous holdings are kept and the run is flagged.
- **Accounts the source finds that aren't in config are still tracked,** under keys like
  `fidelity_1234`.
- **Cash sweep funds aren't counted twice.** When SnapTrade marks a fund like SPAXX as already inside
  cash, it isn't added again.
- **Kraken balances are merged per coin.** Wallets like `XXBT`, `XBT.F` and `XBT.S` all count as BTC.
  Moves between spot and Earn net to zero.
- **Kraken cost basis is average cost from your ledger.** Staking and Earn rewards come in at $0. Coins
  you deposited from another wallet have no known cost, so basis shows blank until you add
  `cost_basis_overrides` in config.
- **Every sync checks its own totals.** Holdings valued at broker prices are compared with SnapTrade's
  own account total. A gap over 1% is noted in the run summary.

## Layout

```
portfolio/       config, db (schema), models, sources/{snaptrade,kraken}, prices, costbasis, sync, queries
jobs/            sync, link, demo (CLI entry points)
panel/           Streamlit plumbing (secrets, password gate, caches)
streamlit_app.py the dashboard (plain; styling comes next)
config/portfolio.yaml   account map + knobs
.github/workflows/sync.yml
```
