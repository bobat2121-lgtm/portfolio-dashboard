"""The Simulation switch's made-up portfolio (portfolio/simulation.py)."""
from datetime import date

import pytest

from portfolio import db, history, lenses, queries, simulation, taxes
from portfolio.config import performance_start

TODAY = date(2026, 9, 27)


@pytest.fixture
def sim(tmp_path):
    path = tmp_path / "sim.db"
    simulation.build(path, TODAY)
    url = f"sqlite:///{path.as_posix()}"
    with db.using(url):
        yield
    db.reset_engine()


def test_the_made_up_portfolio_is_worth_32571_in_six_holdings(sim):
    h, c = queries.holdings(), queries.cash()
    priced = queries.reprice(h, simulation.quotes(TODAY))
    t = queries.totals(priced, c)
    assert t["total"] == pytest.approx(32_571.00, abs=0.005)
    assets = lenses.combine_assets(priced, c)
    assert sorted(assets["asset"]) == sorted(["SPCX", "MSTR", "BTC", "QQQ", "Cash", "AAPL $250 call Jan '27"])
    unreal = dict(zip(assets["asset"], assets["unrealized"]))
    assert unreal["SPCX"] > 0 and unreal["QQQ"] > 0 and unreal["MSTR"] < 0 and unreal["AAPL $250 call Jan '27"] < 0


def test_it_has_tranches_sales_and_a_clean_history(sim):
    h, c = queries.holdings(), queries.cash()
    priced = queries.reprice(h, simulation.quotes(TODAY))
    tx = queries.all_transactions()
    assert (tx["type"] == "sell").sum() == 3 and (tx["type"] == "dividend").sum() >= 4
    rep = taxes.build(tx, priced, queries.account_rows(), queries.splits(), TODAY)
    gains = rep.realized.groupby("asset")["gain"].sum()
    assert gains["MSTR"] < 0 < gains["SPCX"]                                 # one sold at a loss, one at a gain
    assert rep.lots.groupby("asset").size()["MSTR"] >= 4
    hist = history.rebase(history.build(tx, priced, c, queries.account_rows(), queries.price_history(), queries.splits(),
                                        TODAY), performance_start())
    assert not hist.notes and history.summary(hist)["value"] == pytest.approx(32_571, abs=1)
    assert not queries.value_history().empty


def test_it_is_static_and_never_touches_the_real_database(tmp_path, tmp_db):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    simulation.build(a, TODAY)
    simulation.build(b, TODAY)
    with db.using(f"sqlite:///{a.as_posix()}"):
        first = queries.all_transactions().drop(columns=["id"])
    with db.using(f"sqlite:///{b.as_posix()}"):
        second = queries.all_transactions().drop(columns=["id"])
    assert first[["trade_date", "type", "symbol", "quantity", "price", "amount"]].equals(
        second[["trade_date", "type", "symbol", "quantity", "price", "amount"]])
    assert queries.holdings().empty                                           # the real (test) database is untouched
