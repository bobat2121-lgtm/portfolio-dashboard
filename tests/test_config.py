from portfolio import config


def test_private_overrides_merge_over_committed_yaml(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_CONFIG",
                       "accounts: {robinhood_ira: {match: {number_last4: '1234'}}, kraken: {cost_basis_overrides: {BTC: 25000}}}")
    config.settings.cache_clear()
    try:
        accts = config.account_specs()
        assert accts["robinhood_ira"]["match"] == {"institution": "robinhood", "retirement": True, "number_last4": "1234"}
        assert accts["kraken"]["cost_basis_overrides"] == {"BTC": 25000}
        assert accts["fidelity_taxable"]["label"] == "Fidelity Taxable"  # untouched
    finally:
        monkeypatch.delenv("PORTFOLIO_CONFIG")
        config.settings.cache_clear()


def test_engine_follows_a_changed_database_url(tmp_path, monkeypatch):
    from portfolio import db

    db.reset_engine()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    first = db.engine()
    assert db.engine() is first  # cached while the URL is unchanged
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'b.db').as_posix()}")
    second = db.engine()  # e.g. Streamlit secrets edited while the app runs
    assert second is not first and str(second.url).endswith("b.db")
    db.reset_engine()


def test_ticker_watchlist_keeps_order_and_reads_venues():
    config.settings.cache_clear()
    watch = config.ticker_watch()
    assert [w[0] for w in watch] == ["SPCX", "QQQ", "SPY", "TSLA", "ETH", "BMNR", "STRC", "SATA", "ZEC", "RUT"]
    assert ("ETH", "kraken", "ETH") in watch and ("ZEC", "kraken", "ZEC") in watch
    assert ("RUT", "yahoo", "^RUT") in watch and ("SPCX", "yahoo", "SPCX") in watch


def test_ticker_adds_the_watchlist_once_after_holdings(monkeypatch):
    import pandas as pd
    from panel import common, views
    from portfolio.prices import QuoteData

    now = pd.Timestamp("2026-09-27", tz="UTC")
    quotes = {("yahoo", "QQQ"): QuoteData("yahoo", "QQQ", 744.5, 741.1, now),
              ("kraken", "ETH"): QuoteData("kraken", "ETH", 2696.27, 2695.38, now),
              ("yahoo", "^RUT"): QuoteData("yahoo", "^RUT", 2837.55, None, now)}
    rows = common.watch_rows([("QQQ", "yahoo", "QQQ"), ("ETH", "kraken", "ETH"), ("RUT", "yahoo", "^RUT"),
                              ("TSLA", "yahoo", "TSLA")], quotes)
    assert [r[0] for r in rows] == ["QQQ", "ETH", "RUT"]          # no quote, no tile
    assert rows[2][2] is None and abs(rows[0][2] - (744.5 / 741.1 - 1)) < 1e-12

    html = []
    monkeypatch.setattr(views.st, "html", html.append)
    assets = pd.DataFrame([{"asset": "BTC", "market_value": 8000.0, "price": 84700.0, "day_change": 30.0},
                           {"asset": "QQQ", "market_value": 5000.0, "price": 744.5, "day_change": 20.0}])
    views.ticker(assets, 84724.7, 84426.8, rows)
    bar = html[0]                                                    # every cell appears twice: the track loops
    assert bar.count("<b>BTC</b>") == 2 and "$84,725" in bar       # the live tile, not the holding's price
    assert bar.count("<b>QQQ</b>") == 2 and bar.count("<b>ETH</b>") == 2 and bar.count("<b>RUT</b>") == 2
    assert bar.index("<b>BTC</b>") < bar.index("<b>QQQ</b>") < bar.index("<b>ETH</b>") < bar.index("<b>RUT</b>")
