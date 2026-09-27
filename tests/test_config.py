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
