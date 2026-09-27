"""The holding drawer's numbers (portfolio/detail.py): tranches, trades, summary, taxes, the option contract."""
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from portfolio import detail

SINCE, TODAY = date(2025, 6, 30), date(2026, 9, 27)
RATES = {"short_term_rate": 0.24, "long_term_rate": 0.15, "state_rate": 0.0}


def lot(acq, qty, cost, value, tax="taxable", account="Robinhood Taxable", asset="ASST", term="short", lt=None):
    return {"asset": asset, "symbol": asset, "account": account, "tax": tax, "acquired": acq, "qty": qty, "cost": cost,
            "value": value, "unrealized": value - cost, "days": (TODAY - acq).days if acq else None, "term": term,
            "long_term_on": lt}


def row(**kw):
    base = dict(asset="ASST", symbol="ASST", asset_class="equity", quantity=100.0, price=30.0, market_value=3000.0,
                cost_basis=2000.0, underlying="ASST", name="Strive")
    return SimpleNamespace(**{**base, **kw})


def test_tranches_keep_only_this_holdings_lots_after_the_start():
    lots = pd.DataFrame([lot(date(2025, 5, 1), 10, 100, 300), lot(date(2026, 3, 12), 105, 956.55, 3150),
                         lot(date(2026, 1, 30), 6.35, 99.82, 190.5), lot(date(2026, 2, 2), 5, 50, 150, asset="MSTR")])
    t = detail.tranches(lots, row(), SINCE)
    assert list(t["acquired"]) == [date(2026, 1, 30), date(2026, 3, 12)]       # oldest first; 2025-05 and MSTR dropped
    assert t["paid"].iloc[1] == pytest.approx(9.11) and t["gain_pct"].iloc[0] == pytest.approx(190.5 / 99.82 - 1)


def test_trades_are_split_adjusted_and_start_after_the_start():
    txns = pd.DataFrame([
        {"symbol": "ASST", "type": "buy", "trade_date": date(2025, 6, 1), "quantity": 10, "price": 1.0, "value_usd": 10.0,
         "amount": -10.0, "account": "RH"},
        {"symbol": "ASST", "type": "buy", "trade_date": date(2026, 1, 30), "quantity": 127, "price": 0.786, "value_usd": 99.82,
         "amount": -99.82, "account": "RH"},
        {"symbol": "ASST", "type": "sell", "trade_date": date(2026, 8, 25), "quantity": -69, "price": 21.34, "value_usd": 1472.43,
         "amount": 1472.43, "account": "RH"},
        {"symbol": "ASST", "type": "dividend", "trade_date": date(2026, 8, 1), "quantity": None, "price": None, "value_usd": 3.0,
         "amount": 3.0, "account": "RH"},
    ])
    splits = pd.DataFrame([{"symbol": "ASST", "date": date(2026, 2, 6), "ratio": 0.05}])   # 1-for-20
    tr = detail.trades(txns, {"ASST"}, SINCE, splits)
    assert list(tr["side"]) == ["buy", "sell"]
    assert tr["qty"].iloc[0] == pytest.approx(6.35) and tr["price"].iloc[0] == pytest.approx(15.72)   # today's shares
    assert tr["qty"].iloc[1] == pytest.approx(69) and tr["amount"].iloc[1] == pytest.approx(1472.43)
    marks = detail.by_day(pd.concat([tr, tr.iloc[:1]]))
    assert len(marks) == 2 and marks["qty"].iloc[0] == pytest.approx(12.7)


def test_summary_break_even_counts_what_sales_and_income_brought_in():
    lots = pd.DataFrame([lot(date(2026, 1, 30), 100, 2000, 3000)])
    realized = pd.DataFrame([{"asset": "ASST", "sold": date(2026, 8, 25), "gain": 400.0},
                             {"asset": "ASST", "sold": date(2025, 1, 5), "gain": 999.0}])      # before the start: ignored
    txns = pd.DataFrame([{"symbol": "ASST", "type": "dividend", "trade_date": date(2026, 8, 1), "amount": 100.0}])
    s = detail.summary(row(), lots, realized, txns, {"ASST"}, SINCE, TODAY)
    assert s["avg_cost"] == pytest.approx(20) and s["breakeven"] == pytest.approx((2000 - 400 - 100) / 100)
    assert s["unrealized"] == pytest.approx(1000) and s["total"] == pytest.approx(1500)
    assert s["first"] == date(2026, 1, 30) and s["held_days"] == (TODAY - date(2026, 1, 30)).days


def test_taxes_leave_the_ira_out_net_the_terms_and_flag_wash_sales_only_against_other_shares():
    lots = pd.DataFrame([
        lot(date(2025, 8, 1), 10, 1000, 1500, term="long"),                                   # +500 long
        lot(date(2026, 9, 10), 5, 800, 600, lt=date(2027, 9, 11)),                           # -200 short
        lot(date(2026, 1, 5), 4, 100, 900, lt=date(2027, 1, 6)),                             # +800 short
        lot(date(2026, 2, 1), 121, 1000, 3562, tax="ira", account="Robinhood IRA"),
    ])
    tr = pd.DataFrame([{"date": date(2026, 9, 10), "side": "buy", "qty": 5, "price": 160, "amount": 800, "account": "RH"}])
    t = detail.taxes(lots, tr, RATES, TODAY)
    assert t["short"] == pytest.approx(600) and t["long"] == pytest.approx(500)
    assert t["bill"] == pytest.approx(600 * 0.24 + 500 * 0.15)
    assert t["next"]["on"] == date(2027, 1, 6) and t["loss_lots"] == 1 and t["loss"] == pytest.approx(-200)
    assert t["wash_until"] == date(2026, 10, 10) and t["ira_qty"] == 121
    loss_offsets = detail.taxes(pd.DataFrame([lot(date(2025, 8, 1), 1, 100, 400, term="long"),
                                              lot(date(2026, 5, 1), 1, 500, 300)]), tr.iloc[:0], RATES, TODAY)
    assert loss_offsets["bill"] == pytest.approx((300 - 200) * 0.15)                      # short loss offsets long gain
    alone = detail.taxes(pd.DataFrame([lot(date(2026, 9, 10), 5, 800, 600)]), tr, RATES, TODAY)
    assert alone["wash_until"] is None                                                   # its own buy can't wash it


def test_option_contract_numbers():
    r = row(asset="ASST $35 call Jan '28", symbol="ASST  280121C00035000", asset_class="option", quantity=1.0,
            price=11.65, market_value=1165.0, cost_basis=1210.66)
    o = detail.option(r, 29.44, TODAY)
    assert (o["underlying"], o["call"], o["strike"], o["expiry"]) == ("ASST", True, 35.0, date(2028, 1, 21))
    assert o["days"] == (date(2028, 1, 21) - TODAY).days and o["shares"] == 100
    assert o["premium"] == pytest.approx(12.1066) and o["breakeven"] == pytest.approx(47.1066)
    assert o["move"] == pytest.approx(47.1066 / 29.44 - 1) and o["money"] == pytest.approx(29.44 / 35 - 1)
    assert o["intrinsic"] == 0 and o["time_value"] == pytest.approx(11.65)
    assert detail.option(row(), 29.44, TODAY) is None                                  # not an option
