from datetime import date

from portfolio import models as m
from portfolio.sources.snaptrade import (
    SnapTradeSource,
    is_retirement,
    parse_account,
    parse_activity,
    parse_balances,
    parse_positions,
)
from portfolio.sync import map_accounts


def _acct(id, inst, name, raw_type="", number="00001234"):
    return {"id": id, "institution_name": inst, "name": name, "raw_type": raw_type, "number": number,
            "balance": {"total": {"amount": 1000.0, "currency": "USD"}},
            "sync_status": {"holdings": {"initial_sync_completed": True}}}


def test_positions_handle_cash_sweep_options_and_total_cost():
    body = {
        "results": [
            {"instrument": {"kind": "stock", "symbol": "MSTR", "description": "Strategy"},
             "units": 10, "price": 300.0, "cost_basis": 250.0, "currency": "USD", "cash_equivalent": False},
            {"instrument": {"kind": "mutualfund", "symbol": "SPAXX"},
             "units": 500, "price": 1.0, "cost_basis": 1.0, "currency": "USD", "cash_equivalent": True},
            {"instrument": {"kind": "option", "symbol": "MSTR  261218C00400000"},
             "units": 2, "price": 12.5, "cost_basis": 10.0, "currency": "USD"},
            {"instrument": {"kind": "stock", "symbol": "ZERO"}, "units": 0, "price": 1.0},
        ],
        "data_freshness": {"as_of": "2026-09-26T20:00:00Z"},
    }
    positions, as_of = parse_positions(body)
    by = {p.symbol: p for p in positions}
    assert as_of.isoformat().startswith("2026-09-26T20:00")
    assert by["MSTR"].cost_basis == 2500 and by["MSTR"].asset_class == m.EQUITY
    assert by["SPAXX"].in_cash_balance and by["SPAXX"].asset_class == m.CASH
    opt = by["MSTR  261218C00400000"]
    assert opt.multiplier == 100 and opt.cost_basis == 2000 and opt.asset_class == m.OPTION
    assert "ZERO" not in by


def test_balances_and_activity_signs():
    cash = parse_balances([{"currency": {"code": "USD"}, "cash": 42.5, "buying_power": 42.5}])
    assert cash[0].amount == 42.5
    buy = parse_activity({"id": "a1", "type": "BUY", "units": 3, "price": 10, "amount": 30,
                          "trade_date": "2026-09-25T00:00:00Z", "symbol": {"symbol": "STRC"}})
    assert buy.type == m.BUY and buy.amount == -30 and buy.quantity == 3 and buy.value_usd == 30
    sell = parse_activity({"id": "a2", "type": "SELL", "units": 3, "price": 10, "amount": -30})
    assert sell.amount == 30 and sell.quantity == -3
    dep = parse_activity({"id": "a3", "type": "CONTRIBUTION", "amount": -500, "trade_date": "2026-09-24"})
    assert dep.type == m.DEPOSIT and dep.amount == 500
    wd = parse_activity({"id": "a4", "type": "WITHDRAWAL", "amount": 200})
    assert wd.type == m.WITHDRAWAL and wd.amount == -200
    assert parse_activity({"id": "a5", "type": "SOMETHING_NEW"}).type == m.OTHER


def test_retirement_detection_and_mapping_to_config():
    accts = [parse_account(a) for a in (
        _acct("f1", "Fidelity", "Individual", "INDIVIDUAL", "Z12345678"),
        _acct("r1", "Robinhood", "Robinhood Individual", "individual", "5551111"),
        _acct("r2", "Robinhood", "Robinhood Roth IRA", "ira_roth", "5552222"),
    )]
    assert [is_retirement(a) for a in accts] == [False, False, True]
    specs = {
        "fidelity_taxable": {"source": "snaptrade", "match": {"institution": "fidelity", "retirement": False}},
        "robinhood_taxable": {"source": "snaptrade", "match": {"institution": "robinhood", "retirement": False}},
        "robinhood_ira": {"source": "snaptrade", "match": {"institution": "robinhood", "retirement": True}},
        "kraken": {"source": "kraken", "match": {}},
    }
    warnings = []
    mapping = map_accounts("snaptrade", accts, specs, {}, warnings)
    assert mapping == {"f1": "fidelity_taxable", "r1": "robinhood_taxable", "r2": "robinhood_ira"}
    assert not warnings


def test_ambiguous_rule_warns_and_falls_back_to_auto_key():
    accts = [parse_account(a) for a in (
        _acct("r2", "Robinhood", "Roth IRA", "ira_roth", "5552222"),
        _acct("r3", "Robinhood", "Traditional IRA", "ira_traditional", "5553333"),
    )]
    specs = {"robinhood_ira": {"source": "snaptrade", "match": {"institution": "robinhood", "retirement": True}}}
    warnings = []
    mapping = map_accounts("snaptrade", accts, specs, {}, warnings)
    assert mapping == {"r2": "robinhood_2222", "r3": "robinhood_3333"}
    assert "number_last4" in warnings[0]
    specs["robinhood_ira"]["match"]["number_last4"] = "3333"
    assert map_accounts("snaptrade", accts, specs, {}, [])["r3"] == "robinhood_ira"


class _Resp:
    def __init__(self, body):
        self.body = body


class FakeSnapClient:
    """Mimics the SDK surface SnapTradeSource uses."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.account_information = self

    def list_user_accounts(self):
        return _Resp([_acct("f1", "Fidelity", "Individual")])

    def get_all_account_positions(self, account_id):
        return _Resp({"results": [], "data_freshness": {"as_of": None}})

    def get_user_account_balance(self, account_id):
        return _Resp([{"currency": {"code": "USD"}, "cash": 10}])

    def get_account_activities(self, **kw):
        self.calls.append(kw)
        return _Resp(self.pages[kw["offset"] // kw["limit"]])


def test_activities_paginate_and_start_from_last_known_date():
    page = lambda n, total: {"data": [{"id": f"x{i}", "type": "DIVIDEND", "amount": 1} for i in range(n)],
                             "pagination": {"offset": 0, "limit": 1000, "total": total}}
    client = FakeSnapClient([page(1000, 1500), page(500, 1500)])
    src = SnapTradeSource({"overlap_days": 10}, client=client)
    snaps = src.fetch(since=lambda ext: date(2026, 9, 20))
    assert len(snaps[0].transactions) == 1500
    assert client.calls[0]["start_date"] == date(2026, 9, 10)
    assert [c["offset"] for c in client.calls] == [0, 1000]


def test_credit_cards_are_excluded_and_robinhood_splits_cleanly():
    class Client(FakeSnapClient):
        def list_user_accounts(self):
            return _Resp([_acct("i", "Robinhood", "Robinhood Individual", "INDIVIDUAL", "1111"),
                          _acct("c", "Robinhood", "Robinhood Crypto", "DIGITALASSET", "2222"),
                          _acct("cc", "Robinhood", "Robinhood Credit Card", "CREDITCARD", "3333"),
                          _acct("r", "Robinhood", "Robinhood Roth Ira", "ROTH_IRA", "4444")])

    src = SnapTradeSource({"exclude_account_types": ["CREDITCARD"]}, client=Client([]))
    accts = src.list_accounts()
    assert [a.external_id for a in accts] == ["i", "c", "r"]
    from portfolio.config import account_specs
    warnings = []
    mapping = map_accounts("snaptrade", accts, account_specs(), {}, warnings)
    assert mapping == {"i": "robinhood_taxable", "c": "robinhood_crypto", "r": "robinhood_ira"}
    assert not warnings
