import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest
import live_api

from live_api import (
    DevicePairingService,
    LiveDataService,
    LiveRequestHandler,
    MinuteRateLimiter,
    configure_fubon_certificate,
    _login_fubon_stream,
    _live_telegram_delivery_health,
    market_closed_label,
    normalize_symbol,
    _open_market_sessions,
)


def test_live_telegram_health_uses_confirmed_delivery_marker(tmp_path, monkeypatch):
    marker = tmp_path / "telegram-ready.json"
    token = "dedicated-live-bot-token"
    marker.write_text(json.dumps({
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "sent_at": "2026-08-24T08:01:00+00:00",
    }), encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_LIVE_BOT_TOKEN", token)
    monkeypatch.setattr(live_api, "_live_telegram_ready_path", lambda: marker)
    monkeypatch.setattr(live_api, "live_telegram_configured", lambda: True)
    handler = SimpleNamespace(live_telegram=SimpleNamespace(status=lambda: {
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
        "pending_count": 0,
    }))

    health = _live_telegram_delivery_health(handler)

    assert health["configured"] is True
    assert health["state"] == "delivered"
    assert health["last_success_at"] == "2026-08-24T08:01:00+00:00"
    assert "token" not in health


def test_live_telegram_health_marks_a_newer_failed_attempt(tmp_path, monkeypatch):
    marker = tmp_path / "telegram-ready.json"
    token = "dedicated-live-bot-token"
    marker.write_text(json.dumps({
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "sent_at": "2026-08-24T08:01:00+00:00",
    }), encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_LIVE_BOT_TOKEN", token)
    monkeypatch.setattr(live_api, "_live_telegram_ready_path", lambda: marker)
    monkeypatch.setattr(live_api, "live_telegram_configured", lambda: True)
    handler = SimpleNamespace(live_telegram=SimpleNamespace(status=lambda: {
        "last_attempt_at": "2026-08-24T09:01:00+00:00",
        "last_success_at": None,
        "last_error": "HTTPError",
        "pending_count": 0,
    }))

    health = _live_telegram_delivery_health(handler)

    assert health["state"] == "delivery_failed"
    assert health["last_success_at"] == "2026-08-24T08:01:00+00:00"
    assert health["last_error"] == "HTTPError"


def test_runtime_storage_requires_an_existing_writable_volume(tmp_path, monkeypatch):
    volume = tmp_path / "railway-volume"
    monkeypatch.setenv("LIVE_PERSISTENT_DATA_DIR", str(volume))

    missing = live_api._runtime_storage_health()
    assert missing["state"] == "volume_missing"
    assert missing["validation_eligible"] is False
    assert live_api._flow_weight_shadow_state_path() == Path("/tmp/wude-flow_weight_shadow.json")

    volume.mkdir()
    healthy = live_api._runtime_storage_health()
    assert healthy["state"] == "persistent"
    assert healthy["validation_eligible"] is True
    assert live_api._flow_weight_shadow_state_path() == volume / "flow_weight_shadow.json"


def test_telegram_ready_notice_waits_for_persistent_storage(tmp_path, monkeypatch):
    missing = tmp_path / "missing-volume"
    sent = []
    monkeypatch.setenv("LIVE_PERSISTENT_DATA_DIR", str(missing))
    monkeypatch.setenv("TELEGRAM_LIVE_BOT_TOKEN", "dedicated-live-bot-token")
    monkeypatch.setattr(live_api, "send_live_telegram", lambda message: sent.append(message) or True)

    assert live_api._send_live_telegram_ready_once(SimpleNamespace()) is False
    assert sent == []


def _handler_with_headers(**headers):
    handler = object.__new__(LiveRequestHandler)
    handler.headers = Message()
    for name, value in headers.items():
        handler.headers[name.replace("_", "-")] = value
    return handler


def test_normalize_symbol_by_market():
    assert normalize_symbol("2330", "tw") == ("2330.TW", "TW")
    assert normalize_symbol("6488.two", "TW") == ("6488.TWO", "TW")
    assert normalize_symbol("nvda", "us") == ("NVDA", "US")
    with pytest.raises(ValueError):
        normalize_symbol("NVDA/../../", "US")


def test_closed_market_message_uses_premarket_start_times():
    assert market_closed_label("TW") == "台股尚未到 09:00 開盤時間"
    us = market_closed_label("US")
    assert "夏令16:00" in us
    assert "冬令17:00" in us
    assert "21:30" not in us


def test_cloud_certificate_is_written_with_private_permissions(tmp_path, monkeypatch):
    target = tmp_path / "cert.p12"
    fd = target.open("wb")
    monkeypatch.setattr("live_api.tempfile.mkstemp", lambda **_: (fd.fileno(), str(target)))
    monkeypatch.setattr("live_api.os.fdopen", lambda _fd, _mode: fd)
    env = {"FUBON_CERT_BASE64": base64.b64encode(b"certificate").decode()}
    path = configure_fubon_certificate(env)
    assert path == target
    assert target.read_bytes() == b"certificate"
    assert target.stat().st_mode & 0o777 == 0o600
    assert env["FUBON_CERT_PATH"] == str(target)


def test_us_quote_is_cached_and_options_are_merged():
    now = [100.0]
    calls = {"quote": 0, "option": 0}

    def us_fetcher(symbols):
        calls["quote"] += 1
        return {"NVDA": {"us_live_price": 200, "us_live_source": "Alpaca SIP", "us_live_fetched_at": "now"}}

    def option_fetcher(candidates):
        calls["option"] += 1
        assert candidates[0]["symbol"] == "NVDA"
        return {"NVDA": {"us_option_safety_score": 63}}

    service = LiveDataService(
        clock=lambda: now[0], us_fetcher=us_fetcher, option_fetcher=option_fetcher,
        quote_ttl=2, option_ttl=60,
    )
    first = service.fetch("nvda", "US")
    second = service.fetch("NVDA", "US")
    assert first["quote"]["us_live_price"] == 200
    assert first["options"]["us_option_safety_score"] == 63
    assert second["cached"] is True
    assert calls == {"quote": 1, "option": 1}

    now[0] += 3
    service.fetch("NVDA", "US")
    assert calls == {"quote": 2, "option": 1}


def test_taiwan_quote_uses_fubon_once_with_cache():
    calls = {"login": 0, "quote": 0}

    class QuoteClient:
        def quote(self, symbol):
            calls["quote"] += 1
            assert symbol == "2330"
            return {"lastPrice": 1000, "bids": [{"size": 10}], "asks": [{"size": 8}]}

    sdk = SimpleNamespace(
        marketdata=SimpleNamespace(
            rest_client=SimpleNamespace(stock=SimpleNamespace(intraday=QuoteClient()))
        )
    )

    def login():
        calls["login"] += 1
        return sdk

    service = LiveDataService(clock=lambda: 100, fubon_login=login, quote_ttl=2)
    first = service.fetch("2330", "TW")
    second = service.fetch("2330.TW", "TW")
    assert first["quote"]["lastPrice"] == 1000
    assert second["cached"] is True
    assert calls == {"login": 1, "quote": 1}


def test_taiwan_quote_falls_back_to_oddlot_book_when_regular_depth_is_missing():
    calls = []

    class QuoteClient:
        def quote(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("type") == "oddlot":
                return {
                    "lastPrice": 100.5,
                    "bids": [{"price": 100.0, "size": 12}],
                    "asks": [{"price": 100.5, "size": 9}],
                }
            return {"lastPrice": 100, "bids": [], "asks": []}

    sdk = SimpleNamespace(
        marketdata=SimpleNamespace(
            rest_client=SimpleNamespace(stock=SimpleNamespace(intraday=QuoteClient()))
        )
    )
    service = LiveDataService(clock=lambda: 100, fubon_login=lambda: sdk, quote_ttl=2)

    result = service.fetch("1590", "TW")

    assert calls == [{"symbol": "1590"}, {"symbol": "1590", "type": "oddlot"}]
    assert result["quote_status"] == "complete"
    assert result["quote"]["orderBookType"] == "oddlot"
    assert result["quote"]["bidTotal"] == 12
    assert result["quote"]["askTotal"] == 9


def test_taiwan_quote_reports_partial_book_without_inventing_depth():
    class QuoteClient:
        def quote(self, **_kwargs):
            return {"lastPrice": 100, "bids": [], "asks": []}

    sdk = SimpleNamespace(
        marketdata=SimpleNamespace(
            rest_client=SimpleNamespace(stock=SimpleNamespace(intraday=QuoteClient()))
        )
    )
    service = LiveDataService(clock=lambda: 100, fubon_login=lambda: sdk, quote_ttl=2)

    result = service.fetch("1590", "TW")

    assert result["quote_status"] == "order_book_missing"
    assert result["quote"]["orderBookComplete"] is False
    assert result["quote"]["bidTotal"] is None
    assert result["quote"]["askTotal"] is None


def test_taiwan_quote_keeps_regular_price_when_oddlot_fallback_fails():
    class QuoteClient:
        def quote(self, **kwargs):
            if kwargs.get("type") == "oddlot":
                raise RuntimeError("odd-lot feed unavailable")
            return {"lastPrice": 100, "bids": [], "asks": []}

    sdk = SimpleNamespace(
        marketdata=SimpleNamespace(
            rest_client=SimpleNamespace(stock=SimpleNamespace(intraday=QuoteClient()))
        )
    )
    service = LiveDataService(clock=lambda: 100, fubon_login=lambda: sdk, quote_ttl=2)

    result = service.fetch("1590", "TW")

    assert result["quote"]["lastPrice"] == 100
    assert result["quote_status"] == "order_book_missing"


def test_rate_limiter_resets_after_one_minute():
    now = [100.0]
    limiter = MinuteRateLimiter(2, clock=lambda: now[0])
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False
    now[0] += 60
    assert limiter.allow() is True


def test_device_pairing_sends_telegram_code_and_issues_read_only_token(monkeypatch):
    now = [1_000.0]
    messages = []
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "owner-secret-that-is-longer-than-32-bytes")
    service = DevicePairingService(
        lambda message: messages.append(message) or True,
        clock=lambda: now[0],
        request_cooldown_seconds=5,
    )

    request = service.request_code()
    code = re.search(r"一次性驗證碼：(\d{6})", messages[0]).group(1)
    result = service.verify_code(request["request_id"], code)

    assert service.token_valid(result["device_token"]) is True
    assert result["expires_at"] == int(now[0] + 180 * 24 * 60 * 60)
    with pytest.raises(ValueError, match="已失效"):
        service.verify_code(request["request_id"], code)


def test_device_pairing_rejects_wrong_expired_and_forged_tokens(monkeypatch):
    now = [2_000.0]
    messages = []
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "owner-secret-that-is-longer-than-32-bytes")
    service = DevicePairingService(
        lambda message: messages.append(message) or True,
        clock=lambda: now[0],
        code_ttl_seconds=60,
        device_ttl_seconds=3_600,
        request_cooldown_seconds=5,
    )

    request = service.request_code()
    code = re.search(r"一次性驗證碼：(\d{6})", messages[0]).group(1)
    wrong_code = "000000" if code != "000000" else "111111"
    with pytest.raises(ValueError, match="不正確"):
        service.verify_code(request["request_id"], wrong_code)
    result = service.verify_code(request["request_id"], code)
    assert service.token_valid(result["device_token"] + "tampered") is False
    now[0] += 3_601
    assert service.token_valid(result["device_token"]) is False

    now[0] += 5
    expired_request = service.request_code()
    expired_code = re.search(r"一次性驗證碼：(\d{6})", messages[-1]).group(1)
    now[0] += 61
    with pytest.raises(ValueError, match="已失效"):
        service.verify_code(expired_request["request_id"], expired_code)


def test_device_pairing_can_retry_immediately_when_telegram_was_not_ready(monkeypatch):
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "owner-secret-that-is-longer-than-32-bytes")
    deliveries = [False, True]
    service = DevicePairingService(
        lambda _message: deliveries.pop(0),
        clock=lambda: 0,
        request_cooldown_seconds=30,
    )

    with pytest.raises(RuntimeError, match="Telegram 尚未連線"):
        service.request_code()
    assert service.request_code()["request_id"]


def test_device_token_can_read_but_never_counts_as_owner(monkeypatch):
    now = [3_000.0]
    messages = []
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "owner-secret-that-is-longer-than-32-bytes")
    service = DevicePairingService(
        lambda message: messages.append(message) or True,
        clock=lambda: now[0],
        request_cooldown_seconds=5,
    )
    request = service.request_code()
    code = re.search(r"一次性驗證碼：(\d{6})", messages[0]).group(1)
    device_token = service.verify_code(request["request_id"], code)["device_token"]
    handler = _handler_with_headers(X_Live_Token=device_token)
    handler.device_pairing = service

    assert handler._authorized() is True
    assert handler._owner_authorized() is False


def test_market_sessions_are_separated():
    # 01:00 UTC is 09:00 Taipei and the previous evening in New York.
    assert _open_market_sessions(datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)) == [("TW", "2026-08-24")]
    # 14:00 UTC is 10:00 New York during daylight-saving time.
    assert _open_market_sessions(datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)) == [("US", "2026-08-24")]


def test_owner_auth_accepts_trimmed_bearer_token(monkeypatch):
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "  owner-token\n")
    handler = _handler_with_headers(Authorization="  Bearer owner-token  ")
    assert handler._authorized() is True


def test_owner_auth_accepts_live_token_header(monkeypatch):
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "owner-token")
    handler = _handler_with_headers(X_Live_Token=" owner-token ")
    assert handler._authorized() is True


def test_owner_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", "owner-token")
    handler = _handler_with_headers(X_Live_Token="wrong-token")
    assert handler._authorized() is False


def test_site_read_token_allows_quotes_but_not_owner_actions(monkeypatch):
    token = "site-read-only-token"
    monkeypatch.setenv("LIVE_SITE_TOKEN_SHA256", hashlib.sha256(token.encode()).hexdigest())
    handler = _handler_with_headers(X_Site_Live_Token=token)
    assert handler._authorized() is True
    assert handler._owner_authorized() is False


def test_site_read_token_rejects_wrong_value(monkeypatch):
    monkeypatch.setenv("LIVE_SITE_TOKEN_SHA256", hashlib.sha256(b"correct").hexdigest())
    handler = _handler_with_headers(X_Site_Live_Token="wrong")
    assert handler._authorized() is False


def test_vercel_app_site_token_override_is_accepted(monkeypatch):
    token = "vercel-app-test-token"
    monkeypatch.setenv("LIVE_SITE_TOKEN_SHA256", hashlib.sha256(b"old-site-token").hexdigest())
    monkeypatch.setenv("VERCEL_APP_TOKEN_SHA256", hashlib.sha256(token.encode()).hexdigest())
    handler = _handler_with_headers(X_Site_Live_Token=token)
    assert handler._authorized() is True
    assert handler._owner_authorized() is False


def test_public_read_never_counts_as_owner_auth(monkeypatch):
    monkeypatch.delenv("LIVE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LIVE_TRUSTED_AUTH_HEADER", raising=False)
    monkeypatch.setenv("LIVE_PUBLIC_READ", "1")
    handler = _handler_with_headers()
    assert handler._authorized() is True
    assert handler._owner_authorized() is False


def test_fubon_stream_login_prepares_cloud_certificate_first(monkeypatch):
    calls = []
    monkeypatch.setattr("live_api.configure_fubon_certificate", lambda: calls.append("certificate"))
    monkeypatch.setattr("live_api._login_fubon", lambda: calls.append("login") or "sdk")
    assert _login_fubon_stream() == "sdk"
    assert calls == ["certificate", "login"]
