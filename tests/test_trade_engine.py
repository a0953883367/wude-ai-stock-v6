import json
from datetime import datetime
from zoneinfo import ZoneInfo

from trade_engine import JsonTradingStateStore, PaperTradingEngine


TAIPEI = ZoneInfo("Asia/Taipei")


def candidate(symbol="2330.TW", market="TW", price=100, **updates):
    row = {
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "type": "個股",
        "price": price,
        "market_contract_valid": True,
        "trade_guard_blocked": False,
        "short_term_eligible": True,
        "overall_eligible": True,
        "next_session_direction": "📈 看漲",
        "next_session_confidence": 80,
        "outlook_direction": "📈 看漲",
        "short_term_entry_low": price * 0.98,
        "short_term_entry_high": price * 1.02,
        "short_term_stop": price * 0.95,
        "resistance1": price * 1.05,
    }
    row.update(updates)
    return row


def engine(tmp_path, rows, prices=None, initial_cash=20_000):
    report = tmp_path / "all.json"
    report.write_text(json.dumps({"data": rows}), encoding="utf-8")
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"market": {"美元台幣": {"price": 32}}}), encoding="utf-8")
    prices = prices or {row["symbol"]: row["price"] for row in rows}
    return PaperTradingEngine(
        JsonTradingStateStore(tmp_path / "state.json", initial_cash=initial_cash),
        report_path=report,
        fx_path=latest,
        quote_fetcher=lambda symbol, market: {
            "source": "test",
            "quote": {"lastPrice": prices[symbol]} if market == "TW" else {"us_live_price": prices[symbol]},
        },
        clock=lambda: datetime(2026, 8, 24, 10, 0, tzinfo=TAIPEI),
    )


def test_twenty_thousand_is_split_with_ten_percent_reserve(tmp_path):
    subject = engine(tmp_path, [candidate(), candidate("2317.TW")])
    subject.configure(selected=["2330", "2317"], cash_limit=20_000)
    preview = subject.preview()
    assert preview["reserve"] == 2_000
    assert preview["per_symbol_budget"] == 9_000
    assert [plan["quantity"] for plan in preview["plans"]] == [89, 89]


def test_ten_thousand_and_two_symbols_is_4500_each(tmp_path):
    subject = engine(tmp_path, [candidate(), candidate("2317.TW")], initial_cash=10_000)
    subject.configure(selected=["2330", "2317"], cash_limit=10_000)
    preview = subject.preview()
    assert preview["reserve"] == 1_000
    assert preview["per_symbol_budget"] == 4_500


def test_mixed_tw_us_share_one_twd_cap(tmp_path):
    subject = engine(tmp_path, [candidate(), candidate("NVDA", "US", 100)])
    subject.configure(selected=["2330", "NVDA"], cash_limit=20_000)
    preview = subject.preview()
    assert preview["per_symbol_budget"] == 9_000
    us = next(plan for plan in preview["plans"] if plan["symbol"] == "NVDA")
    assert us["currency"] == "USD"
    assert us["fx_rate"] == 32
    assert us["quantity"] == 2


def test_invalid_symbol_budget_is_not_redistributed(tmp_path):
    blocked = candidate("2317.TW", next_session_confidence=10)
    subject = engine(tmp_path, [candidate(), blocked])
    subject.configure(selected=["2330", "2317"], cash_limit=20_000, enabled=True)
    state = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert set(state["positions"]) == {"2330.TW"}
    assert state["positions"]["2330.TW"]["quantity"] == 89
    assert state["paper_cash"] > 10_000


def test_maximum_three_symbols(tmp_path):
    subject = engine(tmp_path, [candidate()])
    try:
        subject.configure(selected=["2330", "2317", "2308", "2454"], cash_limit=20_000)
    except ValueError as exc:
        assert "最多只能勾選3檔" in str(exc)
    else:
        raise AssertionError("four symbols must be rejected")


def test_position_is_sold_on_fifth_trading_session(tmp_path):
    prices = {"2330.TW": 100}
    subject = engine(tmp_path, [candidate()], prices=prices)
    subject.configure(selected=["2330"], cash_limit=20_000, enabled=True)
    for session in ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]:
        state = subject.run_cycle(session_date=session, markets={"TW"})
    assert state["positions"] == {}
    assert state["orders"][-1]["side"] == "SELL"
    assert state["orders"][-1]["reason"] == "達到最長5個交易日"


def test_emergency_stop_prevents_new_buy(tmp_path):
    subject = engine(tmp_path, [candidate()])
    subject.configure(selected=["2330"], cash_limit=20_000, enabled=True, emergency_stop=True)
    state = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert state["positions"] == {}
    assert state["enabled"] is False
    assert state["real_orders_sent"] == 0
