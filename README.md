# Portfolio Dashboard

One view of Fidelity (taxable), Robinhood (taxable + IRA) and Kraken. A scheduled job pulls every
account into Postgres, so buys, sells and deposits show up without you doing anything. It keeps a
permanent history of holdings, values and transactions.

```
 Fidelity ─┐                                              GitHub Actions (holds the broker keys)
 Robinhood ┼─ SnapTrade Personal (read-only) ─┐           every 30 min in market hours, 4 h otherwise
 RH IRA ───┘                                  ├─ jobs.sync ──write──→ Neon Postgres ──read-only──→ Streamlit app
 Kraken ─────── Kraken API (read-only key) ───┘                                            (password; no broker keys)
 Yahoo / Kraken public ticker ── live prices, no keys
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

The cloud dashboard's **Sync now** opens the workflow on GitHub; press *Run workflow* there (tick refresh to also
ask SnapTrade to re-pull the brokers). Run locally with keys in `.env`, the button syncs in place.

## Setup (one time)

Keys and passwords go only into `.env` (on your PC), GitHub secrets and Streamlit secrets. Never into chat,
code or config files.

**1. Database.** Neon → your project → **Connect** (top right of the project dashboard). Leave branch `main`,
set **Connection pooling off** and copy the `postgresql://…` string into `.env` as `DATABASE_URL`.

**2. SnapTrade (Fidelity + Robinhood).** At <https://dashboard.snaptrade.com> turn on two-factor login,
then create your Personal key at <https://dashboard.snaptrade.com/api-key>. Put the client ID and consumer
key in `.env`. Connect Fidelity and Robinhood from the dashboard. SnapTrade uses each broker's own
read-only login page, and its Fidelity and Robinhood integrations cannot place trades at all.

**3. Kraken.** Settings → API → Create key. Tick **only** *Query Funds* and *Query Ledger Entries*.
Leave Deposit, Withdraw, Trade/Orders, Earn, Export and WebSockets off. Put the key and private key in `.env`.

**4. First run (locally).**
```powershell
.venv\Scripts\python -m jobs.link             # each linked account and the config key it maps to
.venv\Scripts\python -m jobs.sync --dry-run   # fetch + price everything, write nothing
.venv\Scripts\python -m jobs.sync             # first real sync (reads your whole Kraken ledger: a few minutes)
.venv\Scripts\python -m jobs.readonly_login   # read-only DB login for the dashboard -> .env DATABASE_URL_READONLY
```
If `jobs.link` says an account is ambiguous, pin it with `number_last4`, but in the `PORTFOLIO_CONFIG`
secret, not the committed YAML (see [config/portfolio.yaml](config/portfolio.yaml)).

**5. GitHub Actions secrets.** Run `python -m jobs.push_secrets`, or add them by hand (repo → Settings → Secrets and variables → Actions): `DATABASE_URL` (the
owner string), `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`, `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, and
optionally `PORTFOLIO_CONFIG`.

**6. Streamlit Cloud secrets**: only `DATABASE_URL` (the **read-only** string from step 4) and
`APP_PASSWORD` (make one with `python -c "import secrets; print(secrets.token_urlsafe(24))"`).
Main file `streamlit_app.py`, Python 3.12.

## Security model

**What each key can do, if someone stole it:**

| Secret | Lives in | Worst case if stolen |
|---|---|---|
| SnapTrade consumer key | GitHub secrets, your `.env` | read your holdings/transactions. Can't trade (the Fidelity and Robinhood integrations don't support it; connections are read-only), can't log in to your brokers, can't move money |
| Kraken API key | GitHub secrets, your `.env` | read balances and ledger. No trade, deposit or withdraw permission exists on the key |
| DB owner URL | GitHub secrets, your `.env` | read or alter the dashboard's copy of your data. It holds no broker credentials |
| DB read-only URL | Streamlit secrets | read the dashboard's copy of your data |
| APP_PASSWORD | Streamlit secrets | view the dashboard |

Nothing in this project can log in to Fidelity, Robinhood or Kraken, trade, or move money. Those
abilities stay behind your own logins and 2FA at each company, which this project never sees.

**Guards in the code:**
- **Nothing on the dashboard loads before the password.** Against the real database, a missing or short
  (<16 characters) `APP_PASSWORD` locks the app instead of opening it. Wrong guesses are slowed down and
  locked out, and visitors never see error details.
- **The cloud app holds no broker keys.** It reads the database through a login that is not allowed to write.
- **Actions logs are redacted.** They show no amounts, symbols, account names or error text. Details
  stay in the database, on the Sync tab.
- **The workflow never runs on pull requests.** Its token is read-only, actions are pinned to exact
  commits, and packages are installed from a hash-checked lockfile.
- **Nothing personal is committed.** Pins and cost-basis overrides go in the `PORTFOLIO_CONFIG` secret.

**Settings to keep:** 2FA on GitHub, Streamlit (it signs in through GitHub), Neon, SnapTrade and Kraken.
No collaborators on the repo. Streamlit's own sharing set to what you want (public is fine: the password gate
covers it).

**Rotating a key** (if you ever suspect a leak): delete it at the provider (SnapTrade API key page, Kraken
API page, Neon role password), create a new one, and update `.env` and the GitHub/Streamlit secrets.
`python -m jobs.readonly_login` rotates the read-only DB password.

## Ways to view it

Two pages (top left): **Dashboard** and **Taxes**. A ticker of your holdings scrolls across the top.

| Dashboard tab | What it shows |
|---|---|
| **Assets** (opens first) | every holding with all accounts combined (BTC on Kraken + Robinhood = one BTC, MSTR in taxable + IRA = one MSTR, all cash = one Cash) as cards, then **Performance**: you vs the same deposits in BTC and in the S&P 500 (SPY, dividends reinvested), and value vs money in |
| **Accounts** | value by account, allocation by asset class and by taxable/IRA |
| **Themes** | holdings by theme (edit in `config/portfolio.yaml`), Bitcoin-linked share, optional pricing in BTC |
| **Star map** | each theme an orbit, each holding a planet sized by value, gain/loss halo, a moon per extra account |
| **Briefing** | a Star Wars opening crawl written from today's numbers |
| **What if** | slide bitcoin's price; each holding moves by its measured beta to BTC (options re-priced from their stock) |
| **Achievements** | 16 badges earned from your real history, with progress on the locked ones |
| **Positions / Activity / Sync** | raw positions, detected changes + transactions + contributions, sync health |

**Taxes page:** realized short- and long-term gains by year (taxable accounts), dividends and interest, a rough tax
estimate at your rates, lots turning long-term soon, loss-harvest candidates with wash-sale windows, every sale with a
wash-sale flag, and all open lots. First-in-first-out lots rebuilt from your transactions, stock splits applied. Set
your rates in the `PORTFOLIO_CONFIG` secret: `taxes: {short_term_rate: 0.24, long_term_rate: 0.15, state_rate: 0.05}`.
An estimate; your 1099 is the record.

**How history is rebuilt:** each account's holdings and cash are walked backwards from today through its transactions,
and each day is valued with daily closes the sync job stores in `price_history` (split-adjusted; old quantities are
adjusted with `splits`, since brokers don't always report splits; ASST's 1-for-20 in Feb 2026 is one). Options and
delisted tickers use their own trade prices. `python -m jobs.prices` backfills by hand.

**The scene reacts to your day:** on up days the rebels attack the Death Star more often and fighters jump to
hyperspace; on down days it charges its superlaser.

## The look

The dashboard sits over a pixel-art galaxy drawn live in the browser ([panel/assets/space.js](panel/assets/space.js)):
twinkling stars and nebula, the nine planets orbiting the sun (with real phases, Saturn's rings and Earth's moon),
Tatooine, Hoth, Mustafar and Endor far off, the Death Star with its TIE patrol on the left and the rebel fleet on
the right. Every 20 s to 2 min something happens: a shooting star, meteor shower, supernova, comet, tumbling
asteroid, pulsar, a fighter jumping to hyperspace, or the Death Star charging its superlaser. About once in 300
events (roughly every 6 hours of viewing) a black hole opens, pulls everything in, swallows the dashboard and leaves
the tab black until you reload.

- **Three panel styles** from the switcher at the top right (remembered in the URL as `?style=`):
  `distinct` (solid panels, space in the gaps), `melded` (no panels, space behind everything) and `mix`
  (floating header and numbers, solid data panels).
- **Try an event now:** add `?space=supernova` to the URL (or `meteor_shower`, `comet`, `asteroid`, `pulsar`,
  `hyperspace`, `superlaser`, `rebel_attack`, `shooting_star`, `blackhole`). From the browser console:
  `__space.trigger("comet")`.
- People who set their OS to *reduce motion* get the scene without the random events.
- Type: **Star Jedi** by Boba Fonts (freeware) for titles, kept as its original, intact zip in
  `panel/assets/fonts/` as its license asks, plus Orbitron and Share Tech Mono from Google Fonts.
- Colors are Star Wars: crawl yellow `#FFE81F`, lightsaber blue `#4BD5EE`, Sith red `#FF3B30`, saber green
  `#39FF14`, rebel orange `#F26B1D`. Theme settings are in [.streamlit/config.toml](.streamlit/config.toml).

## Commands

| | |
|---|---|
| `python -m jobs.sync` | pull everything (what the schedule runs) |
| `python -m jobs.sync --dry-run` | do it all, print it, roll it back |
| `python -m jobs.sync --only kraken` | one source |
| `python -m jobs.sync --refresh` | ask SnapTrade to re-pull brokers first |
| `python -m jobs.sync --force` | skip the min-gap and empty-holdings guard |
| `python -m jobs.link [--portal]` | check connections and account mapping / get a connect link |
| `python -m jobs.readonly_login` | create or rotate the dashboard's read-only DB login |
| `python -m jobs.prices` | backfill daily price and split history (the sync does this too) |
| `python -m jobs.push_secrets [--dry-run]` | copy the sync job's secrets from `.env` to GitHub Actions (values never printed) |
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
jobs/            sync, link, readonly_login, demo (CLI entry points)
panel/           Streamlit plumbing (secrets, password gate, caches) + theme.py and assets/ (the space scene, CSS, font)
streamlit_app.py the dashboard
config/portfolio.yaml   account map + knobs
.github/workflows/sync.yml
```
