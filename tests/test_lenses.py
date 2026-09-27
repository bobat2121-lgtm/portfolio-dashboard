from datetime import date

import pandas as pd
import pytest

from portfolio import lenses


def holdings(rows):
    cols = ["account", "symbol", "name", "asset_class", "quantity", "price", "market_value", "cost_basis",
            "day_change", "in_cash_balance"]
    return pd.DataFrame(rows, columns=cols)


H = holdings([
    ["Robinhood Taxable", "MSTR", "Strategy", "equity", 100, 160.0, 16000.0, 12000.0, -300.0, False],
    ["Robinhood IRA", "MSTR", "Strategy", "equity", 20, 160.0, 3200.0, 2800.0, -60.0, False],
    ["Kraken", "BTC", "BTC", "crypto", 0.05, 80000.0, 4000.0, None, 50.0, False],
    ["Robinhood Crypto", "BTC", "BTC", "crypto", 0.01, 80000.0, 800.0, 700.0, 10.0, False],
    ["Fidelity Taxable", "ASST  280121C00035000", "", "option", 1, 11.65, 1165.0, 1210.0, 0.0, False],
    ["Fidelity Taxable", "SPAXX", "", "cash", 500, 1.0, 500.0, 500.0, 0.0, True],   # already inside cash
    ["Robinhood Taxable", "STRC", "", "equity", 1, 98.0, 98.0, 100.0, 0.0, False],
])
C = pd.DataFrame([["Fidelity Taxable", "USD", 500.0], ["Robinhood IRA", "USD", 30.0]], columns=["account", "currency", "amount"])


def test_option_symbols_read_as_people_say_them():
    assert lenses.option_label("ASST  280121C00035000") == ("ASST", "ASST $35 call Jan '28")
    assert lenses.option_label("MSTR  261218P00150500") == ("MSTR", "MSTR $150.5 put Dec '26")
    assert lenses.option_label("MSTR") == (None, "MSTR")


def test_combine_merges_accounts_and_folds_cash():
    a = lenses.combine_assets(H, C).set_index("asset")
    assert a.loc["MSTR", "market_value"] == 19200 and a.loc["MSTR", "cost_basis"] == 14800
    assert a.loc["MSTR", "accounts"] == ["Robinhood IRA", "Robinhood Taxable"] and a.loc["MSTR", "n_accounts"] == 2
    assert a.loc["BTC", "market_value"] == 4800 and pd.isna(a.loc["BTC", "cost_basis"])  # Kraken basis unknown
    assert a.loc["Cash", "market_value"] == 530                                            # SPAXX not counted twice
    assert "ASST $35 call Jan '28" in a.index
    assert a["weight"].sum() == pytest.approx(1.0)
    assert list(a.index[:2]) == ["MSTR", "BTC"]                                            # biggest first


def test_themes_follow_symbols_then_classes_and_options_follow_underlying():
    a = lenses.with_themes(lenses.combine_assets(H, C), lenses.DEFAULT_THEMES).set_index("asset")
    assert a.loc["MSTR", "theme"] == "Bitcoin treasuries"
    assert a.loc["ASST $35 call Jan '28", "theme"] == "Bitcoin treasuries"
    assert a.loc["BTC", "theme"] == "Bitcoin" and a.loc["STRC", "theme"] == "Digital credit"
    assert a.loc["Cash", "theme"] == "Cash & stablecoins"
    assert lenses.theme_of("ETH", "crypto", rules=lenses.DEFAULT_THEMES)["name"] == "Crypto"
    assert lenses.theme_of("AAPL  280121C00200000", "option", "AAPL", lenses.DEFAULT_THEMES)["name"] == "Stocks & funds"
    t = lenses.by_theme(a.reset_index())
    assert t.iloc[0]["theme"] == "Bitcoin treasuries" and t["weight"].sum() == pytest.approx(1.0)
    share = lenses.bitcoin_share(a.reset_index())
    assert share == pytest.approx((19200 + 1165 + 4800 + 98) / (19200 + 4800 + 1165 + 98 + 530))


def test_configured_themes_are_valid():
    names = [t["name"] for t in lenses.theme_rules()]
    assert len(names) == len(set(names)) and "Bitcoin" in names
    assert all(t.get("color", "").startswith("#") for t in lenses.theme_rules())


def test_crawl_is_written_from_the_numbers():
    a = lenses.with_themes(lenses.combine_assets(H, C), lenses.DEFAULT_THEMES)
    totals = {"total": 25793.0, "day_change": -300.0, "cash": 530.0, "cost_basis": 18810.0, "unrealized": 6453.0}
    c = lenses.crawl(totals, a, ["Fidelity Taxable", "Robinhood Taxable", "Kraken"], date(2026, 9, 27), date(2026, 9, 30))
    assert c["episode"] == "Episode IV" and c["title"] == "The Bears Strike Back"
    text = " ".join(c["paragraphs"])
    assert "$25,793" in text and "Fidelity Taxable, Robinhood Taxable and Kraken" in text
    assert "MSTR leads the fleet" in text and "bound to the power of Bitcoin" in text
    assert lenses.episode_title(0.03) == "The Rally Awakens" and lenses.episode_title(0.0) == "The Phantom Drift"
    assert lenses.roman(270) == "CCLXX"
