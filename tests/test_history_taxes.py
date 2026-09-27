from datetime import date, timedelta

import pandas as pd
import pytest

from portfolio import achievements, history, lenses, taxes, whatif

TODAY = date(2026, 9, 27)
D0 = TODAY - timedelta(days=10)


def accounts(rows=(("rh", "Robinhood Taxable", "snaptrade", "INDIVIDUAL", "taxable"),)):
    return pd.DataFrame(rows, columns=["key", "label", "source", "raw_type", "tax"])


def txn(i, key, d, type_, symbol="", qty=None, price=None, amount=None, value=None):
    return {"id": i, "account_key": key, "trade_date": d, "occurred_at": None, "type": type_, "symbol": symbol,
            "quantity": qty, "price": price, "amount": amount, "value_usd": value, "fee": 0.0}


def closes(sym, start, values, adj=None):
    return [{"symbol": sym, "date": start + timedelta(days=i), "close": v, "adj_close": (adj or values)[i]}
            for i, v in enumerate(values)]


def positions(rows):
    return pd.DataFrame(rows, columns=["account_key", "symbol", "quantity", "price", "multiplier", "asset_class",
                                       "in_cash_balance", "market_value"])


def test_history_rebuilds_value_money_in_and_benchmarks():
    t = pd.DataFrame([txn(1, "rh", D0, "deposit", amount=1000.0),
                      txn(2, "rh", D0 + timedelta(days=1), "buy", "XYZ", 10, 50.0, -500.0, 500.0)])
    pos = positions([["rh", "XYZ", 10, 60.0, 1.0, "equity", False, 600.0]])
    cash = pd.DataFrame([["rh", "USD", 500.0]], columns=["account_key", "currency", "amount"])
    prices = pd.DataFrame(closes("XYZ", D0, [50.0] * 5 + [55.0] * 5 + [60.0])
                          + closes("BTC-USD", D0, [100.0] * 10 + [200.0])
                          + closes("SPY", D0, [100.0] * 10 + [110.0]))
    h = history.build(t, pos, cash, accounts(), prices, pd.DataFrame(columns=["symbol", "date", "ratio"]), TODAY)
    s = history.summary(h)
    assert s["value"] == pytest.approx(1100) and s["net_in"] == pytest.approx(1000) and s["gain"] == pytest.approx(100)
    assert s["btc"] == pytest.approx(2000) and s["spy"] == pytest.approx(1100)
    assert h.daily.loc[D0, "value"] == pytest.approx(1000)                     # deposit day: all cash
    assert h.daily.loc[D0 + timedelta(days=6), "value"] == pytest.approx(500 + 550)


def test_reverse_split_the_broker_never_reported_is_applied():
    # 100 shares at $1, then a 1-for-20 reverse split: you now hold 5 shares at $20 (Yahoo prices are split-adjusted)
    t = pd.DataFrame([txn(1, "rh", D0, "deposit", amount=100.0),
                      txn(2, "rh", D0, "buy", "ASST", 100, 1.0, -100.0, 100.0)])
    pos = positions([["rh", "ASST", 5, 20.0, 1.0, "equity", False, 100.0]])
    cash = pd.DataFrame([["rh", "USD", 0.0]], columns=["account_key", "currency", "amount"])
    prices = pd.DataFrame(closes("ASST", D0, [20.0] * 11) + closes("BTC-USD", D0, [1.0] * 11) + closes("SPY", D0, [1.0] * 11))
    splits = pd.DataFrame([{"symbol": "ASST", "date": D0 + timedelta(days=5), "ratio": 0.05}])
    h = history.build(t, pos, cash, accounts(), prices, splits, TODAY)
    assert (h.daily["value"].round(6) == 100).all() and not h.notes           # no phantom 20x jump, no gaps
    rep = taxes.build(t, pos, accounts(), splits, TODAY)
    assert rep.lots["qty"].sum() == pytest.approx(5) and rep.lots["cost"].sum() == pytest.approx(100)


def test_fifo_lots_terms_and_wash_sales():
    d1, d2, d3 = date(2025, 1, 2), date(2026, 2, 6), date(2026, 5, 17)
    t = pd.DataFrame([
        txn(1, "rh", d1, "buy", "XYZ", 10, 50.0, -500.0, 500.0),
        txn(2, "rh", d2, "buy", "XYZ", 10, 60.0, -600.0, 600.0),
        txn(3, "rh", d3, "sell", "XYZ", -15, 70.0, 1050.0, 1050.0),
        txn(4, "rh", date(2026, 6, 1), "buy", "ABC", 10, 10.0, -100.0, 100.0),
        txn(5, "rh", date(2026, 6, 10), "sell", "ABC", -10, 8.0, 80.0, 80.0),        # a loss...
        txn(6, "rh", date(2026, 6, 20), "buy", "ABC", 5, 8.5, -42.5, 42.5),          # ...bought back 10 days later
    ])
    pos = positions([["rh", "XYZ", 5, 75.0, 1.0, "equity", False, 375.0], ["rh", "ABC", 5, 9.0, 1.0, "equity", False, 45.0]])
    rep = taxes.build(t, pos, accounts(), pd.DataFrame(columns=["symbol", "date", "ratio"]), TODAY)
    R = rep.realized.set_index(["symbol", "term"])
    assert R.loc[("XYZ", "long"), "gain"] == pytest.approx(10 * 70 - 500)          # oldest lot first, held > 1 year
    assert R.loc[("XYZ", "short"), "gain"] == pytest.approx(5 * 70 - 300)
    assert bool(R.loc[("ABC", "short"), "wash_sale"]) and R.loc[("ABC", "short"), "gain"] == pytest.approx(-20)
    lots = rep.lots.set_index("symbol")
    assert lots.loc["XYZ", "qty"] == pytest.approx(5) and lots.loc["XYZ", "cost"] == pytest.approx(300)
    ys = taxes.year_summary(rep, 2026)
    assert ys["long"] == pytest.approx(200) and ys["short"] == pytest.approx(30) and ys["wash"] == 1


def test_whatif_scales_by_beta_and_reprices_options():
    assets = pd.DataFrame([
        {"asset": "MSTR", "symbol": "MSTR", "underlying": "MSTR", "asset_class": "equity", "market_value": 1000.0, "price": 100.0, "quantity": 10},
        {"asset": "Cash", "symbol": "Cash", "underlying": "Cash", "asset_class": "cash", "market_value": 50.0, "price": 1.0, "quantity": 50},
        {"asset": "MSTR $150 call", "symbol": "MSTR  280121C00150000", "underlying": "MSTR", "asset_class": "option",
         "market_value": 300.0, "price": 3.0, "quantity": 1},
    ])
    b = {"MSTR": {"beta": 2.0}, "Cash": {"beta": 0.0}, "MSTR $150 call": {"beta": 2.0}}
    p = whatif.project(assets, b, 50_000, 100_000).set_index("asset")
    assert p.loc["MSTR", "then"] == pytest.approx(4000)                             # 2x BTC, beta 2 -> 4x
    assert p.loc["Cash", "then"] == pytest.approx(50)
    assert p.loc["MSTR $150 call", "then"] == pytest.approx((400 - 150) * 100 + 300)  # intrinsic at $400 + time value
    assert whatif.price_choices(84_000)[0] >= 21_000


def test_achievements_read_the_history():
    assets = lenses.with_themes(pd.DataFrame([
        {"asset": "ASST", "symbol": "ASST", "underlying": "ASST", "asset_class": "equity", "name": "", "quantity": 10.0,
         "price": 30.0, "market_value": 300.0, "cost_basis": 100.0, "unrealized": 200.0, "unrealized_pct": 2.0,
         "day_change": 0.0, "accounts": ["Robinhood Taxable"], "n_accounts": 1, "weight": 1.0}]), lenses.DEFAULT_THEMES)
    badges = {b["code"]: b for b in achievements.evaluate(
        assets, {"total": 300.0, "cash": 0.0}, {"value": 300.0, "btc": 250.0, "spy": 400.0},
        pd.DataFrame([txn(1, "rh", D0, "buy", "ASST", 10, 10.0, -100.0, 100.0)]), pd.DataFrame(columns=["days"]),
        pd.DataFrame(columns=["institution", "total"]), pd.DataFrame(columns=["symbol", "date", "close"]), TODAY)}
    assert badges["2X"]["earned"] and badges["BB"]["earned"] and not badges["SP"]["earned"]
    assert badges["MX"]["earned"] and not badges["10"]["earned"] and badges["10"]["progress"] == pytest.approx(2 / 9)
    assert len(badges) == 16


def test_rebase_starts_money_in_at_that_days_value():
    t = pd.DataFrame([txn(1, "rh", D0, "deposit", amount=1000.0),
                      txn(2, "rh", D0 + timedelta(days=1), "buy", "XYZ", 10, 50.0, -500.0, 500.0),
                      txn(3, "rh", D0 + timedelta(days=7), "deposit", amount=200.0)])
    pos = positions([["rh", "XYZ", 10, 60.0, 1.0, "equity", False, 600.0]])
    cash = pd.DataFrame([["rh", "USD", 700.0]], columns=["account_key", "currency", "amount"])
    prices = pd.DataFrame(closes("XYZ", D0, [50.0] * 5 + [55.0] * 5 + [60.0])
                          + closes("BTC-USD", D0, [100.0] * 5 + [150.0] * 5 + [300.0])
                          + closes("SPY", D0, [100.0] * 11))
    full = history.build(t, pos, cash, accounts(), prices, pd.DataFrame(columns=["symbol", "date", "ratio"]), TODAY)
    start = D0 + timedelta(days=5)
    r = history.rebase(full, start)
    s = history.summary(r)
    assert r.daily.index[0] == start and s["since"] == start
    assert s["net_in"] == pytest.approx(1000 + 200)            # worth $1,000 the day before (500 cash + 10 x $50), then $200
    assert s["btc"] == pytest.approx(1000 / 150 * 300 + 200 / 150 * 300)
    assert history.rebase(full, None) is full


def test_ytd_is_money_weighted_and_compares_the_same_money():
    days = pd.date_range("2025-12-30", "2026-01-10", freq="D").date
    d = pd.DataFrame(index=pd.Index(days, name="date"))
    d["value"] = [900.0, 1000.0] + [2000.0] * 9 + [2200.0]       # Dec 31: 1000; Jan 1: +1000 deposited; ends 2200
    d["flow"] = [0.0, 0.0, 1000.0] + [0.0] * 9
    d["net_in"] = d["flow"].cumsum()
    d["btc_px"] = 50.0                                           # bitcoin flat
    d["spy_px"] = [100.0] * 11 + [200.0]                         # the S&P doubles on the last day
    h = history.History(daily=d)
    y = history.ytd(h, date(2026, 1, 10))
    n = 10                                                       # Jan 1 .. Jan 10
    capital = 1000 + 1000 * (n - 1) / n                          # the deposit was in 9 of 10 days
    assert y["gain"] == pytest.approx(200) and y["capital"] == pytest.approx(capital)
    assert y["pct"] == pytest.approx(200 / capital) and y["twr"] == pytest.approx(0.10)
    assert y["btc"] == pytest.approx(0.0) and y["spy"] == pytest.approx(2000 / capital)
    assert history.ytd(history.History(daily=d.iloc[:0]), date(2026, 1, 10)) == {}
