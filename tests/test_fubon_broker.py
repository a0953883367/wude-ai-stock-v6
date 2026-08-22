import pytest

from fubon_broker import HARD_CAP_TWD, FubonTradingSession, live_order_unlock_reason, split_market_quantities


def test_share_quantities_are_split_for_tw_exchange():
    assert split_market_quantities(0) == []
    assert split_market_quantities(25) == [("IntradayOdd", 25)]
    assert split_market_quantities(1000) == [("Common", 1000)]
    assert split_market_quantities(1234) == [("Common", 1000), ("IntradayOdd", 234)]


def test_live_orders_fail_closed():
    assert live_order_unlock_reason({}) == "TRADING_MODE 尚未設為 live"
    assert "LIVE_TRADING_ENABLED" in live_order_unlock_reason({"TRADING_MODE": "live"})
    env = {"TRADING_MODE": "live", "LIVE_TRADING_ENABLED": "true"}
    assert "永久交易狀態" in live_order_unlock_reason(env)
    env["TRADE_STATE_PATH"] = "/data/trading.json"
    env["LIVE_TRADING_CONFIRMATION"] = "A"
    assert "確認碼" in live_order_unlock_reason(env)


def test_available_cash_never_exceeds_twenty_thousand():
    class Accounting:
        @staticmethod
        def bank_remain(account):
            return {"is_success": True, "data": [{"available_balance": 999_999}]}

    subject = FubonTradingSession(sdk=type("SDK", (), {"accounting": Accounting()})(), account="A")
    assert subject.available_cash() == HARD_CAP_TWD == 20_000


def test_place_order_is_blocked_before_importing_order_sdk(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    subject = FubonTradingSession(sdk=object(), account="A")
    with pytest.raises(PermissionError):
        subject.place_limit_order(symbol="2330", side="BUY", quantity=1, price=100)
