# BTC Supernova

A portfolio dashboard: one view of Fidelity (taxable), Robinhood (taxable + IRA) and Kraken. A scheduled job pulls every
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
| APP_PASSWORD | Streamlit secrets | view the dashboard (changing it signs out every remembered browser) |
| AUTH_SECRET (optional) | Streamlit secrets | nothing on its own: extra key mixed into the 30-day browser pass. Changing it signs out every remembered browser |

Nothing in this project can log in to Fidelity, Robinhood or Kraken, trade, or move money. Those
abilities stay behind your own logins and 2FA at each company, which this project never sees.

**Guards in the code:**
- **Your real accounts never load without the password.** Visitors get an Enter button, then the made-up
  Simulation. Switching it off asks for `APP_PASSWORD`, and the check is on the server, in front of every
  data read ([panel/auth.py](panel/auth.py)), so nothing a browser sends gets around it. A missing or short
  (<16 characters) `APP_PASSWORD` leaves only the Simulation. Wrong guesses are slowed, limited to 5 per
  address per 5 minutes and 20 app-wide per 15 minutes, and visitors never see error details.
- **"Remember this browser" stores a signed pass, not the password.** It is good for 30 days, only in that
  browser. **Lock** in the header forgets the browser; changing `APP_PASSWORD` (or `AUTH_SECRET`) voids every
  pass at once. Anyone using a remembered browser can switch the Simulation off, so press Lock before
  handing yours to someone.
- **The cloud app holds no broker keys.** It reads the database through a login that is not allowed to write.
- **Actions logs are redacted.** They show no amounts, symbols, account names or error text. Details
  stay in the database, on the Sync tab.
- **The workflow never runs on pull requests.** Its token is read-only, actions are pinned to exact
  commits, and packages are installed from a hash-checked lockfile.
- **Nothing personal is committed.** Pins and cost-basis overrides go in the `PORTFOLIO_CONFIG` secret.

**Settings to keep:** 2FA on GitHub, Streamlit (it signs in through GitHub), Neon, SnapTrade and Kraken.
No collaborators on the repo. Streamlit's own sharing set to what you want (public is fine: visitors only
ever see the Simulation).

**Rotating a key** (if you ever suspect a leak): delete it at the provider (SnapTrade API key page, Kraken
API page, Neon role password), create a new one, and update `.env` and the GitHub/Streamlit secrets.
`python -m jobs.readonly_login` rotates the read-only DB password.

## Ways to view it

**Simulation** (the third button beside Sync now and Refresh prices, lit while on) swaps your accounts for a
made-up, static portfolio worth $32,571: SPCX, MSTR, BTC, QQQ, TSLA, AUR, an AAPL call and cash across four accounts, with a
year of tranches, a few sales, dividends and invented prices ([portfolio/simulation.py](portfolio/simulation.py)).
Every page, the ticker and the Taxes page read it instead of your data, so the dashboard can be shown to anyone.
Every visit starts with an Enter button and opens on the Simulation; press Simulation and give the password
to see your real portfolio, live (tick "Remember this browser" to skip the password for 30 days; **Lock** undoes
it). (The Simulation is built into a throwaway SQLite file per day; your real database is never touched.)

Under the title, two hero tags: **Total value** (and what it is in bitcoin) and **YTD return** since Dec 31,
money-weighted so deposits never count as gains, next to the same money held in bitcoin or the S&P 500 (hover it
for the time-weighted figure). Beside them, six readouts: Bitcoin, Today, Unrealized, Invested, Cash, Cost basis.

Two pages (top left): **Dashboard** and **Taxes**. A ticker scrolls across the top: BTC, your holdings over
$100, then a watchlist (SPCX, QQQ, SPY, TSLA, ETH, BMNR, STRC, SATA, ZEC, RUT) set in
[config/portfolio.yaml](config/portfolio.yaml) under `ticker.watch`.

| Dashboard tab | What it shows |
|---|---|
| **Assets** (opens first) | every holding worth over $100, all accounts combined (BTC on Kraken + Robinhood = one BTC, MSTR in taxable + IRA = one MSTR, all cash = one Cash), as cards: the value up top, then price ($84,400 / $158.6 / $29.44), today's move, gain on cost and share of the portfolio. Cards glow lightsaber blue on hover; **click one** and a drawer opens under its row with the position on one line (shares, avg cost, paid, value, unrealized, total return), the price (opens on YTD; 3M, 6M, 1Y, All) with your buys and sells as green and red circles sized by amount, every purchase tranche (FIFO, folded until you open it) and what selling would mean for taxes (short vs long-term, a rough bill, the next lot to go long-term, loss lots, wash-sale window, and the whole portfolio's realized gain or loss; IRA shares left out). Cash shows each account's share. Options add the contract: strike, expiry, break-even and the move needed, intrinsic vs time value. Only what happened after the start (`performance.start`) counts. Below, two panels that open on demand: **Performance** (you vs the same deposits in BTC and in the S&P 500, and value vs money in; its title carries the headline) and **What if bitcoin hits…** (slide BTC's price; each holding moves by its measured beta) |
| **Explore** | sub-tabs: **Accounts** (by account, asset class, taxable/IRA), **Themes** (Bitcoin-linked share, price in BTC), **Star map**, **Achievements** (16 badges from your history), **Positions**, **Activity** |
| **Briefing** | a Star Wars opening crawl written from today's numbers |

**Taxes page:** realized short- and long-term gains by year (taxable accounts), dividends and interest, a rough tax
estimate at your rates, lots turning long-term soon, loss-harvest candidates with wash-sale windows, every sale with a
wash-sale flag, and all open lots. First-in-first-out lots rebuilt from your transactions, stock splits applied. Set
your rates in the `PORTFOLIO_CONFIG` secret: `taxes: {short_term_rate: 0.24, long_term_rate: 0.15, state_rate: 0.05}`.
An estimate; your 1099 is the record.

**Performance starts June 30, 2025** (`performance.start` in `config/portfolio.yaml`): the portfolio's value that
day counts as money in, and the BTC / S&P comparisons are replayed from there. Money in isn't the same as cost basis:
cost basis is only what you paid for what you still hold; money in also covers your cash, money lost (or made) on
things you've sold, fees and dividends. Shares and coins held before SnapTrade's history aren't counted as money in
(or as value); when one was sold, the cash it brought in counts as money in that day.

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
the right, drawn side-on and flying at it (X-wings, a Y-wing, A-wings, the Falcon and a Mon Calamari cruiser).
Every 12 to 72 s something happens, each long enough to watch: a shooting star (~3 s), meteor shower (~12 s),
supernova (swells and flares for 8 s, remnant fades over 40 s), comet, tumbling asteroid, pulsar (15 s), a ship
jumping to hyperspace and back, a rebel attack run (~12 s), or the Death Star charging and firing its superlaser
(a 10 s beam). About once in 128
events (roughly every 1.5 hours of viewing, at random) a black hole opens, pulls everything in, swallows the dashboard and leaves
the tab black until you reload.

- **Panel style:** `mix` (floating header and numbers, see-through data panels). The other two are still in the
  code: add `?style=distinct` (solid panels) or `?style=melded` (no panels) to the URL to see them.
- **Trial: floating islands.** Add `?style=islands`: no holdings panel, every info box (holding cards, the
  headline tags and readouts) floats on its own and bobs gently; bigger panels become still, rounded islands.
  The choice sticks while you move between pages; open the plain URL again for mix.
- **Try an event now:** add `?space=supernova` to the URL (or `meteor_shower`, `comet`, `asteroid`, `pulsar`,
  `hyperspace`, `superlaser`, `rebel_attack`, `shooting_star`, `blackhole`). From the browser console:
  `__space.trigger("comet")`.
- **Star density:** 0.4 of the full starfield (`STAR_DENSITY` in [panel/theme.py](panel/theme.py)); add
  `?stars=0.7` (0.2 to 2) to the URL to try another.
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
panel/           Streamlit plumbing (secrets, caches), auth.py (Enter screen, password, 30-day pass) + theme.py and
                 assets/ (the space scene, CSS, font)
streamlit_app.py the dashboard
config/portfolio.yaml   account map + knobs
.github/workflows/sync.yml
```
