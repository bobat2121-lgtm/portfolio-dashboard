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
