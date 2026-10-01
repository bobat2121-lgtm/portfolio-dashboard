import httpx
import pytest

from portfolio import models as m
from portfolio.costbasis import average_cost, basis_for
from portfolio.sources.kraken import (
    KrakenClient, KrakenError, balances_to_holdings, ledger_to_txns, normalize_asset, sign,
)

STABLES = {"USDC", "USDT"}


def test_signature_matches_krakens_published_example():
    secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    body = "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25"
    assert sign("/0/private/AddOrder", body, "1616492376594", secret) == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


class _Http:
    """Records private calls and answers each with an empty result."""

    def __init__(self):
        self.urls = []

    def post(self, url, content, headers):
        self.urls.append(url)
        return httpx.Response(200, json={"error": [], "result": {}}, request=httpx.Request("POST", url))


def _client(http):
    return KrakenClient(key="k", secret="c2VjcmV0", http=http, sleep=lambda s: None)


def test_private_calls_that_trade_or_move_money_never_leave_the_app():
    http = _Http()
    client = _client(http)
    for method in ("AddOrder", "AmendOrder", "CancelAll", "Withdraw", "WalletTransfer", "Earn/Allocate",
                   "DepositAddresses", "Balance/../Withdraw", "balance"):
        with pytest.raises(KrakenError, match="refused"):
            client.private(method, asset="XBT")
    assert http.urls == []


def test_balance_and_ledger_still_go_through():
    http = _Http()
    client = _client(http)
    client.balances()
    client.ledger()
    assert http.urls == ["https://api.kraken.com/0/private/Balance", "https://api.kraken.com/0/private/Ledgers"]


def test_asset_codes_fold_to_one_symbol():
    for code in ("XXBT", "XBT", "XBT.F", "XBT.M", "XBT.B"):
        assert normalize_asset(code) == "BTC"
    assert normalize_asset("ETH2.S") == "ETH"
    assert normalize_asset("XETH") == "ETH"
    assert normalize_asset("ZUSD") == "USD"
    assert normalize_asset("USD.HOLD") == "USD"
    assert normalize_asset("SOL.S") == "SOL"
    assert normalize_asset("USDC") == "USDC"


def test_balances_fold_wallets_and_split_out_usd_cash():
    positions, cash = balances_to_holdings(
        {"XXBT": "0.5", "XBT.F": "0.25", "ZUSD": "100.5", "USD.M": "10", "USDC": "50", "SOL": "0", "ZEUR": "5"},
        STABLES,
    )
    by = {p.symbol: p for p in positions}
    assert by["BTC"].quantity == 0.75
    assert by["BTC"].asset_class == m.CRYPTO
    assert by["USDC"].asset_class == m.STABLECOIN
    assert by["EUR"].asset_class == m.CASH
    assert "SOL" not in by
    assert cash[0].currency == "USD" and cash[0].amount == 110.5


def _e(id, refid, type, asset, amount, fee="0", time=1_700_000_000, subtype=""):
    return {"id": id, "refid": refid, "type": type, "subtype": subtype, "asset": asset,
            "amount": amount, "fee": fee, "time": time}


def test_ledger_usd_buy_deposit_withdrawal_reward_internal():
    entries = [
        _e("L1", "D1", "deposit", "ZUSD", "1000"),
        _e("L2", "T1", "trade", "ZUSD", "-600", fee="2.4", time=1_700_000_100),
        _e("L3", "T1", "trade", "XXBT", "0.01", time=1_700_000_100),
        _e("L4", "S1", "staking", "XBT.S", "0.0001", time=1_700_000_200),
        _e("L5", "X1", "transfer", "XXBT", "-0.005", subtype="spottostaking", time=1_700_000_300),
        _e("L6", "X2", "transfer", "XBT.S", "0.005", subtype="stakingfromspot", time=1_700_000_300),
        _e("L7", "W1", "withdrawal", "ZUSD", "-100", fee="1", time=1_700_000_400),
        _e("L8", "B1", "spend", "ZUSD", "-50", fee="0.75", time=1_700_000_500),
        _e("L9", "B1", "receive", "XETH", "0.02", time=1_700_000_500),
    ]
    txns = {t.external_id: t for t in ledger_to_txns(entries, STABLES)}
    assert txns["kraken:L1"].type == m.DEPOSIT and txns["kraken:L1"].amount == 1000
    buy = txns["kraken:T1"]
    assert (buy.type, buy.symbol, buy.quantity) == (m.BUY, "BTC", 0.01)
    assert buy.amount == -602.4 and buy.value_usd == 600 and buy.price == 60000
    assert txns["kraken:L4"].type == m.REWARD and txns["kraken:L4"].symbol == "BTC"
    assert txns["kraken:L5"].type == m.INTERNAL and txns["kraken:L6"].type == m.INTERNAL
    assert txns["kraken:L7"].type == m.WITHDRAWAL and txns["kraken:L7"].amount == -101
    eth = txns["kraken:B1"]
    assert (eth.type, eth.symbol, eth.value_usd) == (m.BUY, "ETH", 50)


def test_coin_for_coin_trade_priced_off_the_stablecoin_leg():
    entries = [
        _e("L1", "T9", "trade", "USDC", "-300"),
        _e("L2", "T9", "trade", "SOL", "2", fee="0.01"),
    ]
    txns = {t.symbol: t for t in ledger_to_txns(entries, STABLES)}
    assert txns["SOL"].type == m.BUY and abs(txns["SOL"].quantity - 1.99) < 1e-12
    assert txns["SOL"].value_usd == 300
    assert txns["USDC"].type == m.SELL and txns["USDC"].quantity == -300


def test_average_cost_from_ledger():
    entries = [
        _e("L1", "T1", "trade", "ZUSD", "-1000", time=1),
        _e("L2", "T1", "trade", "XXBT", "0.02", time=1),
        _e("L3", "T2", "trade", "ZUSD", "-3000", time=2),
        _e("L4", "T2", "trade", "XXBT", "0.04", time=2),
        _e("L5", "T3", "trade", "ZUSD", "2000", time=3),
        _e("L6", "T3", "trade", "XXBT", "-0.03", time=3),
        _e("L7", "S1", "staking", "XBT.S", "0.001", time=4),
    ]
    b = average_cost(ledger_to_txns(entries, STABLES))["BTC"]
    # bought 0.06 for $4000, sold half at average cost -> 0.03 for $2000, plus 0.001 reward at $0
    assert abs(b.units - 0.031) < 1e-12 and abs(b.cost - 2000) < 1e-9 and b.complete
    assert basis_for(0.031, b) == b.cost
    assert basis_for(0.5, b) is None  # history doesn't explain what's held


def test_deposited_coins_make_basis_unknown():
    entries = [_e("L1", "D1", "deposit", "XXBT", "0.1")]
    b = average_cost(ledger_to_txns(entries, STABLES))["BTC"]
    assert not b.complete and basis_for(0.1, b) is None
