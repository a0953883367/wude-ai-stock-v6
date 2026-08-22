import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from live_trade_engine import LIVE_ARM_PHRASE, LiveTradingEngine
from trade_engine import JsonTradingStateStore


TAIPEI = ZoneInfo("Asia/Taipei")


def candidate(price=100, **updates):
    row = {
        "symbol": "2330.TW",
        "name": "台積電",
        "market": "TW",
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


class FakeBroker:
    def __init__(self, cash=20_000):
        self.cash = cash
        self.orders = []
        self.inventory = {}
        self.next_id = 1

    def available_cash(self):
        return self.cash

    def inventories(self):
        return [{"symbol": symbol, "quantity": qty} for symbol, qty in self.inventory.items()]

    def place_limit_order(self, *, symbol, side, quantity, price):
        broker_id = f"B{self.next_id}"
        self.next_id += 1
        row = {
            "broker_id": broker_id,
            "order_no": broker_id,
            "symbol": symbol.split(".", 1)[0],
            "side": side,
            "price": price,
            "after_price": price,
            "quantity": quantity,
            "after_qty": quantity,
            "filled_qty": 0,
            "filled_money": 0,
            "status_code": 10,
            "terminal": False,
            "failed": False,
        }
        self.orders.append(row)
        return [dict(row)]

    def order_results(self):
        return [dict(row) for row in self.orders]

    def cancel_order(self, order_no):
        row = next(item for item in self.orders if item["order_no"] == order_no)
        row["status_code"] = 40 if row["filled_qty"] else 30
        row["terminal"] = True
        return [dict(row)]

    def fill(self, order_no, quantity=None):
        row = next(item for item in self.orders if item["order_no"] == order_no)
        quantity = row["quantity"] if quantity is None else quantity
        previous = row["filled_qty"]
        row["filled_qty"] = quantity
        row["filled_money"] = quantity * row["price"]
        row["status_code"] = 50 if quantity == row["quantity"] else 10
        row["terminal"] = row["status_code"] == 50
        delta = quantity - previous
        symbol = row["symbol"]
        if row["side"] == "BUY":
            self.inventory[symbol] = self.inventory.get(symbol, 0) + delta
            self.cash -= delta * row["price"]
        else:
            self.inventory[symbol] = self.inventory.get(symbol, 0) - delta
            self.cash += delta * row["price"]


def make_engine(tmp_path, broker, row=None, prices=None):
    report = tmp_path / "all.json"
    report.write_text(json.dumps({"data": [row or candidate()]}), encoding="utf-8")
    prices = prices or {"2330.TW": 100}
    subject = LiveTradingEngine(
        JsonTradingStateStore(tmp_path / "live.json", mode="live"),
        report_path=str(report),
        quote_fetcher=lambda symbol, market: {"source": "test", "quote": {"lastPrice": prices[symbol]}},
        broker_factory=lambda: broker,
        clock=lambda: datetime(2026, 8, 24, 10, 0, tzinfo=TAIPEI),
    )
    return subject, report


def unlock(monkeypatch):
    monkeypatch.setattr("live_trade_engine.live_order_unlock_reason", lambda: None)


def test_live_arm_requires_owner_confirmation(tmp_path, monkeypatch):
    unlock(monkeypatch)
    subject, _ = make_engine(tmp_path, FakeBroker())
    with pytest.raises(PermissionError):
        subject.configure(selected=[], cash_limit=20_000, enabled=True)
    state = subject.configure(
        selected=[], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    assert state["enabled"] is True


def test_buy_is_only_a_position_after_broker_fill(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    subject, _ = make_engine(tmp_path, broker)
    subject.configure(
        selected=["2330"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )

    submitted = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert submitted["positions"] == {}
    assert len(broker.orders) == 1
    assert submitted["orders"][-1]["status"] == "SUBMITTED"

    broker.fill("B1")
    filled = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert filled["positions"]["2330.TW"]["quantity"] == broker.orders[0]["quantity"]
    assert len(broker.orders) == 1


def test_partial_fill_is_accounted_once_and_never_duplicates_buy(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    subject, _ = make_engine(tmp_path, broker)
    subject.configure(
        selected=["2330"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    broker.fill("B1", 40)
    once = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    twice = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert once["positions"]["2330.TW"]["quantity"] == 40
    assert twice["positions"]["2330.TW"]["quantity"] == 40
    assert len(broker.orders) == 1


def test_restart_recovers_pending_order_before_creating_position(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    first, _ = make_engine(tmp_path, broker)
    first.configure(
        selected=["2330"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    first.run_cycle(session_date="2026-08-24", markets={"TW"})
    broker.fill("B1")

    restarted, _ = make_engine(tmp_path, broker)
    state = restarted.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert state["positions"]["2330.TW"]["quantity"] == broker.orders[0]["quantity"]
    assert len(broker.orders) == 1


def test_weak_forecast_places_sell_and_fill_closes_position(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    prices = {"2330.TW": 103}
    subject, report = make_engine(tmp_path, broker, prices=prices)
    subject.configure(
        selected=["2330"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    broker.fill("B1")
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})

    prices["2330.TW"] = 104
    weak = candidate(
        price=104,
        next_session_direction="📉 看跌",
        outlook_direction="震盪",
        short_term_entry_low=100,
        short_term_entry_high=106,
    )
    report.write_text(json.dumps({"data": [weak]}), encoding="utf-8")
    submitted_sell = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert broker.orders[-1]["side"] == "SELL"
    assert submitted_sell["positions"]

    broker.fill("B2")
    closed = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert closed["positions"] == {}
    assert closed["realized_pnl"] > 0


def test_us_symbol_is_kept_but_blocked_until_us_broker_exists(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    subject, report = make_engine(tmp_path, broker)
    report.write_text(json.dumps({"data": [candidate(), {
        **candidate(), "symbol": "NVDA", "name": "NVIDIA", "market": "US",
    }]}), encoding="utf-8")
    subject.configure(
        selected=["NVDA"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    preview = subject.preview()
    assert preview["plans"][0]["eligible"] is False
    assert "美股尚未接上真單券商" in preview["plans"][0]["reasons"]
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert broker.orders == []


def test_unselect_requests_cancel_for_pending_buy(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    subject, _ = make_engine(tmp_path, broker)
    subject.configure(
        selected=["2330"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    subject.configure(selected=[], cash_limit=20_000)
    state = subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert state["orders"][-1]["cancel_requested_at"]
    assert broker.orders[0]["status_code"] == 30


def test_emergency_stop_cancels_pending_and_submits_nothing_else(tmp_path, monkeypatch):
    unlock(monkeypatch)
    broker = FakeBroker()
    subject, _ = make_engine(tmp_path, broker)
    subject.configure(
        selected=["2330"], cash_limit=20_000, enabled=True,
        live_confirmation=LIVE_ARM_PHRASE,
    )
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    subject.configure(selected=["2330"], cash_limit=20_000, enabled=False, emergency_stop=True)
    subject.run_cycle(session_date="2026-08-24", markets={"TW"})
    assert broker.orders[0]["status_code"] == 30
    assert len(broker.orders) == 1
