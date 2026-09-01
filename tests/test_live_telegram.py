from pathlib import Path
from types import SimpleNamespace

from live_telegram import LiveTelegramBatcher, fanout_alert, format_live_telegram_batch
import notifier
from notifier import (
    _live_friend_telegram_credentials,
    _live_telegram_credentials,
    live_telegram_configured,
    send_live_alert_telegram,
)


def alert(sequence: int, symbol: str, market: str = "TW") -> dict:
    return {
        "sequence": sequence,
        "market": market,
        "symbol": symbol,
        "name": "台積電" if market == "TW" else "NVIDIA",
        "alert_side": "buy",
        "trigger_label": "單筆大買",
        "buy_value": 3_000_000 if market == "TW" else 250_000,
        "aggressive_buy_ratio_pct": 91,
        "price": 1000 if market == "TW" else 200,
    }


class ManualTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        pass


def test_batcher_groups_same_window_into_one_message():
    sent = []
    timers = []

    def timer_factory(delay, callback):
        timer = ManualTimer(delay, callback)
        timers.append(timer)
        return timer

    batcher = LiveTelegramBatcher(lambda text: sent.append(text) or True, timer_factory=timer_factory)
    batcher.enqueue(alert(1, "2330.TW"))
    batcher.enqueue(alert(2, "NVDA", "US"))

    assert len(timers) == 1
    assert timers[0].delay == 10
    assert sent == []
    assert batcher.flush() is True
    assert len(sent) == 1
    assert "台股 1檔・美股 1檔" in sent[0]
    assert "2330.TW" in sent[0]
    assert "NVDA" in sent[0]
    assert "武得" not in sent[0]
    assert "不改排名、不自動下單" in sent[0]
    status = batcher.status()
    assert status["last_attempt_at"]
    assert status["last_success_at"] == status["last_attempt_at"]
    assert status["last_error"] is None
    assert status["pending_count"] == 0


def test_batcher_records_delivery_failure_without_raising():
    batcher = LiveTelegramBatcher(lambda _text: False, timer_factory=ManualTimer)
    batcher.enqueue(alert(1, "2330.TW"))

    assert batcher.flush() is False
    status = batcher.status()
    assert status["last_attempt_at"]
    assert status["last_success_at"] is None
    assert status["last_error"] == "send_returned_false"


def test_live_destination_never_falls_back_to_report_chat():
    settings = SimpleNamespace(
        telegram_bot_token="report-token",
        telegram_chat_id="report-chat",
        telegram_live_bot_token="",
        telegram_live_chat_id="",
    )
    assert _live_telegram_credentials(settings) == ("", "")
    assert live_telegram_configured(settings) is False
    settings.telegram_live_bot_token = "separate-live-token"
    assert live_telegram_configured(settings) is True
    settings.telegram_live_chat_id = "separate-live-chat"
    assert live_telegram_configured(settings) is True


def test_live_chat_can_be_discovered_from_first_private_start(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [
                {"message": {"text": "hello", "chat": {"id": 1, "type": "private"}}},
                {"message": {"text": "/start", "chat": {"id": 24680, "type": "private"}}},
            ]}

    settings = SimpleNamespace(
        telegram_live_bot_token="separate-live-token",
        telegram_live_chat_id="",
    )
    monkeypatch.setattr(notifier.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(notifier, "_LIVE_DISCOVERED_CHAT_ID", "")
    assert _live_telegram_credentials(settings, discover_chat=True) == (
        "separate-live-token",
        "24680",
    )


def test_friend_channel_discovery_requires_exact_channel_and_admin(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [
                {"channel_post": {"chat": {
                    "id": -1, "type": "channel", "title": "別的頻道",
                }}},
                {"my_chat_member": {
                    "chat": {"id": -2, "type": "channel", "title": "AI 大量買賣朋友版"},
                    "new_chat_member": {"status": "member"},
                }},
                {"my_chat_member": {
                    "chat": {"id": -10024680, "type": "channel", "title": "AI 大量買賣朋友版"},
                    "new_chat_member": {"status": "administrator"},
                }},
            ]}

    state_path = tmp_path / "friend-chat.json"
    settings = SimpleNamespace(
        telegram_live_bot_token="separate-live-token",
        telegram_friend_alert_chat_id="",
        telegram_friend_alert_channel_title="AI 大量買賣朋友版",
    )
    monkeypatch.setattr(notifier.requests, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(notifier, "_LIVE_DISCOVERED_FRIEND_CHAT_ID", "")

    assert _live_friend_telegram_credentials(
        settings, discover_chat=True, state_path=state_path
    ) == ("separate-live-token", "-10024680")
    assert state_path.exists()
    monkeypatch.setattr(notifier, "_LIVE_DISCOVERED_FRIEND_CHAT_ID", "")
    assert _live_friend_telegram_credentials(settings, state_path=state_path) == (
        "separate-live-token", "-10024680",
    )


def test_large_trade_delivery_fans_out_but_friend_failure_does_not_break_owner(monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, "send_live_telegram", lambda text: sent.append(("owner", text)) or True)

    def broken_friend(_text, *, state_path=None):
        sent.append(("friend", str(state_path)))
        raise RuntimeError("friend unavailable")

    monkeypatch.setattr(notifier, "send_live_friend_telegram", broken_friend)

    state_path = Path("/data/friend.json")
    assert send_live_alert_telegram("large trade", friend_state_path=state_path) is True
    assert sent == [("owner", "large trade"), ("friend", str(state_path))]


def test_owner_failure_does_not_prevent_friend_attempt(monkeypatch):
    sent = []

    def broken_owner(_text):
        raise RuntimeError("owner unavailable")

    monkeypatch.setattr(notifier, "send_live_telegram", broken_owner)
    monkeypatch.setattr(
        notifier,
        "send_live_friend_telegram",
        lambda text, *, state_path=None: sent.append(text) or True,
    )

    try:
        send_live_alert_telegram("large trade")
    except RuntimeError as exc:
        assert str(exc) == "owner unavailable"
    else:
        raise AssertionError("owner failure must remain visible to delivery health")
    assert sent == ["large trade"]


def test_fanout_keeps_other_sink_when_one_fails():
    received = []

    def broken(_alert):
        raise RuntimeError("web push failed")

    fanout_alert(broken, received.append)(alert(1, "2330.TW"))
    assert received[0]["symbol"] == "2330.TW"


def test_batch_formatter_keeps_sell_direction():
    row = alert(3, "NVDA", "US")
    row.update({
        "alert_side": "sell",
        "trigger_label": "3–5筆大賣",
        "sell_value": 900_000,
        "aggressive_sell_ratio_pct": 88,
    })
    text = format_live_telegram_batch([row])
    assert "🔻" in text
    assert "3–5筆大賣" in text
    assert "US$900K" in text
    assert "賣88%" in text


def test_batch_formatter_marks_single_block_trade_without_duplicate_message():
    row = alert(4, "NVDA", "US")
    row.update({
        "is_block_trade": True,
        "block_trade_label": "單筆巨額大買",
        "buy_value": 250_000,
    })
    text = format_live_telegram_batch([row])
    assert text.count("NVDA") == 1
    assert "單筆巨額大買" in text
    assert "單筆大買｜" not in text
