import base64
import hashlib
from datetime import datetime, timezone
from email.message import Message
from types import SimpleNamespace

import pytest

from live_api import (
    LiveDataService,
    LiveRequestHandler,
    MinuteRateLimiter,
    configure_fubon_certificate,
    normalize_symbol,
    _open_market_sessions,
)


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
