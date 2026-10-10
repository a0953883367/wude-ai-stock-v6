"""Offline wire-handler tests with real device validation and research code.

All keys, tokens, calendars, and bars here are synthetic test fixtures. No
pairing/issuance function runs, no real credential is read, and sockets are
disabled. The fake provider exercises the real bounded parser and projection.
"""
from datetime import date, datetime, time, timedelta, timezone
import functools
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

# Runtime modules are imported only inside a clean child process. Even an
# already-imported live_api singleton in the surrounding suite stays untouched.
live_api = private = None
CHILD_FLAG = "WUDE_SIGNED_DEVICE_OFFLINE_TEST_CHILD"


def isolated_case(test):
    """Run each normal pytest case in its own credential-free import boundary."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        harness = kwargs["harness"]
        if not hasattr(harness, "child_nodeid"):
            return test(*args, **kwargs)
        before = {name: sys.modules.get(name) for name in
                  ("live_api", "config", "web_push", "us_private_research_service")}
        # Deliberately do not copy os.environ, including provider/auth keys,
        # runtime state paths, plugin settings, HOME, or proxy credentials.
        environment = {
            "PATH": os.defpath,
            "PYTHONPATH": os.pathsep.join(sys.path),
            "HOME": str(harness.child_root),
            "TMPDIR": str(harness.child_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            CHILD_FLAG: "1",
        }
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
             "--basetemp", str(harness.child_root / "pytest"), harness.child_nodeid],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, text=True, timeout=60, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert all(sys.modules.get(name) is original for name, original in before.items())
    return run


ORIGIN = "https://wude-ai-stock-app.vercel.app"
PATH = "/api/private/us-research"
TEST_SECRET = "offline-only-synthetic-owner-secret-never-deployed"
NOW = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
AUTH_NOW = int(NOW.timestamp())
HELD_TRANSITIONS = {
    "AAOI": "2026-02-27", "HON": "2026-06-29", "POET": "2026-04-27",
    "SNPS": "2025-09-10", "UMAC": "2026-05-28", "WOLF": "2025-09-29",
}
SYMBOLS = ["AAPL", "SPY", "HNHPF", *HELD_TRANSITIONS]
SYNTHETIC_MARKER = "SYNTHETIC_PROVIDER_PRIVATE_BODY_MUST_NOT_ESCAPE"


def signed_fixture(*, expires=AUTH_NOW + 3600, nonce="offline-fixture-nonce", secret=TEST_SECRET):
    body = f"wude-device-v1.{expires}.{nonce}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz is not None
        return NOW.astimezone(tz)


class SyntheticCalendar:
    """Weekday-only artificial membership, not a real exchange calendar."""
    def __init__(self):
        self.calls = []

    def relay_us_year(self, year):
        self.calls.append(year)
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)
                if (start + timedelta(days=i)).weekday() < 5]
        return {"year": year, "status": "verified_alpaca", "sources": ["Alpaca Market Calendar"],
                "sessions": days, "session_details": {d: {"open": "09:30", "close": "16:00"} for d in days}}


def synthetic_rows(start, end, *, transition=None):
    start, end = date.fromisoformat(start), date.fromisoformat(end[:10])
    rows = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        factor = 0.4 if transition and day.isoformat() >= transition else 1.0
        value = (100 + offset * 0.1) * factor
        rows.append({"t": datetime.combine(day, time(), private.history.NY).isoformat(),
                     "o": value, "h": value + 2, "l": value - 2, "c": value + 0.5, "v": 1000})
    return rows


class FakeResponse:
    def __init__(self, payload, *, status=200, raw=None):
        self.status_code = status
        self.headers = {}
        self.body = raw if raw is not None else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 1
        for index in range(0, len(self.body), 97):
            yield self.body[index:index + 97]

    def close(self):
        self.closed = True


class FakeProvider:
    def __init__(self):
        self.calls, self.responses = [], []
        self.closed = False
        self.failure = None
        self.fail_on = 2

    def get(self, url, **kwargs):
        assert url == private.history.ENDPOINT
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["params"]["feed"] == "sip"
        assert kwargs["params"]["adjustment"] == "split"
        assert kwargs["params"]["timeframe"] == "1Day"
        assert kwargs["headers"]["APCA-API-KEY-ID"] == "offline-fake-provider-key"
        assert kwargs["headers"]["APCA-API-SECRET-KEY"] == "offline-fake-provider-secret"
        assert 0 < kwargs["timeout"][0] <= 3 and 0 < kwargs["timeout"][1] <= 7
        self.calls.append(dict(kwargs["params"]))
        assert len(self.calls) <= 2, "the provider budget must stop before a third call"
        if self.failure and len(self.calls) == self.fail_on:
            response = self.failure(kwargs["params"])
        else:
            symbol = kwargs["params"]["symbols"]
            assert "," not in symbol
            rows = synthetic_rows(kwargs["params"]["start"], kwargs["params"]["end"],
                                  transition=HELD_TRANSITIONS.get(symbol))
            if symbol == "HNHPF":
                rows = []
            response = FakeResponse({"bars": {symbol: rows}, "next_page_token": None})
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


class MemoryConnection:
    """Feeds BaseHTTPRequestHandler actual HTTP bytes without a socket."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, mode, *args):
        assert mode == "rb"
        return self.input

    def sendall(self, body):
        self.output.extend(body)


@pytest.fixture
def harness(monkeypatch, tmp_path, request):
    global live_api, private
    if os.environ.get(CHILD_FLAG) != "1":
        return SimpleNamespace(child_nodeid=request.node.nodeid, child_root=tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("network, real credentials, and new pairing are forbidden in this test")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    # This also protects an explicitly selected child case from inherited
    # credentials. Keep the environment clean for the entire real handler run.
    clean_environment = {"LIVE_ACCESS_TOKEN": TEST_SECRET, "TRADING_MODE": "paper"}
    environment_guard = patch.dict(os.environ, clean_environment, clear=True)
    environment_guard.start()
    request.addfinalizer(environment_guard.stop)
    # These constructors are unrelated to private research and normally read
    # persisted state or create a VAPID key when live_api is first imported.
    # Suppression is confined to this child process and only the import window.
    import web_push
    import large_buy_monitor
    import trade_engine
    with patch.object(web_push.WebPushService, "__init__", return_value=None), \
            patch.object(large_buy_monitor.LargeBuyAlertService, "__init__", return_value=None), \
            patch.object(trade_engine.JsonTradingStateStore, "__init__", return_value=None):
        import live_api as actual_live_api
        import us_private_research_service as actual_private
    live_api, private = actual_live_api, actual_private
    import config
    for field in ("finmind_token", "telegram_bot_token", "telegram_live_bot_token"):
        assert getattr(config.SETTINGS, field) == ""
    monkeypatch.setenv("LIVE_ALLOWED_ORIGINS", "https://a0953883367.github.io")
    monkeypatch.setenv("LIVE_PUBLIC_READ", "1")
    monkeypatch.setenv("LIVE_TRUSTED_AUTH_HEADER", "X-Trusted-User")
    monkeypatch.setenv("LIVE_SITE_TOKEN_SHA256", hashlib.sha256(b"synthetic-shared-site").hexdigest())
    monkeypatch.setenv("VERCEL_APP_TOKEN_SHA256", hashlib.sha256(b"synthetic-shared-site").hexdigest())
    for name in ("request_code", "verify_code", "_issue_device_token"):
        monkeypatch.setattr(live_api.DevicePairingService, name, forbidden)
    pairing = live_api.DevicePairingService(forbidden, clock=lambda: AUTH_NOW)
    monkeypatch.setattr(private, "datetime", FixedDatetime)
    credential_calls = []
    def synthetic_credentials():
        credential_calls.append(True)
        return ("offline-fake-provider-key", "offline-fake-provider-secret")
    monkeypatch.setattr(private.history, "_credentials", synthetic_credentials)
    path = tmp_path / "synthetic-universe.json"
    path.write_text(json.dumps({"data": [{"symbol": s, "market": "US", "type": "ETF" if s == "SPY" else "stock"}
                                          for s in SYMBOLS]}), encoding="utf-8")
    provider, calendar = FakeProvider(), SyntheticCalendar()
    service = private.PrivateResearchService(path, session_factory=lambda: provider)
    class Handler(live_api.LiveRequestHandler):
        device_pairing = pairing
        private_research_service = service
        large_buy_service = SimpleNamespace(weight_shadow=SimpleNamespace(calendar=calendar))
        rate_limiter = SimpleNamespace(allow=lambda: True)
    def post(*, token=None, symbol="AAPL", origin=ORIGIN, raw_body=None, extra_headers=()):
        body = json.dumps({"symbol": symbol}).encode() if raw_body is None else raw_body
        headers = [f"Origin: {origin}", "Content-Type: application/json", f"Content-Length: {len(body)}", "Connection: close"]
        if token is not None:
            headers.append("X-Live-Token: " + token)
        headers.extend(f"{key}: {value}" for key, value in extra_headers)
        conn = MemoryConnection((f"POST {PATH} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode() + body)
        Handler(conn, ("127.0.0.1", 12345), SimpleNamespace())
        raw = bytes(conn.output)
        head, body = raw.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        code = int(lines[0].split()[1])
        response_headers = dict(line.split(": ", 1) for line in lines[1:])
        assert response_headers["Cache-Control"] == "no-store"
        assert "Set-Cookie" not in response_headers
        assert "Access-Control-Allow-Credentials" not in response_headers
        if origin == ORIGIN:
            assert response_headers["Access-Control-Allow-Origin"] == ORIGIN
            assert response_headers["Vary"] == "Origin"
        else:
            assert "Access-Control-Allow-Origin" not in response_headers
        assert int(response_headers["Content-Length"]) == len(body)
        for secret in (TEST_SECRET, token, "offline-fake-provider-key", "offline-fake-provider-secret", SYNTHETIC_MARKER):
            if secret:
                assert secret.encode() not in raw
        return code, json.loads(body)
    return SimpleNamespace(post=post, provider=provider, calendar=calendar, service=service,
                           pairing=pairing, credential_calls=credential_calls, universe=path)


@pytest.mark.parametrize("symbol,category", [("AAPL", "stock"), ("SPY", "etf")])
@isolated_case
def test_signed_paired_device_reaches_real_parser_and_projection(harness, symbol, category, caplog):
    code, body = harness.post(token=signed_fixture(), symbol=symbol)
    assert code == 200 and body["ok"] is True
    data = body["data"]
    assert data["symbol"] == symbol and data["instrument_category"] == category
    assert data["status"] == "projected" and data["features"] and data["plan"]
    assert data["decision_eligible"] is data["affects_formal"] is data["durable_retention"] is False
    assert data["prospective_evaluation_started"] is False
    assert data["plan"]["execution_eligible"] is data["plan"]["investment_recommendation"] is False
    assert data["provenance"]["historical_point_in_time"] is False
    assert data["provenance"]["corporate_actions_independently_verified"] is False
    assert data["provenance"]["observed_at"] == NOW.isoformat()
    assert data["request_count"] == len(harness.provider.calls) == 2
    assert [row["symbols"] for row in harness.provider.calls] == ["AAPL", symbol]
    assert harness.provider.closed and all(row.closed for row in harness.provider.responses)
    assert harness.pairing.requests == {} and not caplog.records
    assert not any(isinstance(value, (dict, list)) for value in vars(harness.service).values())
    assert list(harness.universe.parent.iterdir()) == [harness.universe]
    # Repeated explicit requests remain subject to the same global cooldown.
    code, body = harness.post(token=signed_fixture(), symbol=symbol)
    assert code == 429 and body == {"ok": False, "error": "research_cooldown"}
    assert len(harness.provider.calls) == 2


@pytest.mark.parametrize("token", [
    signed_fixture(expires=AUTH_NOW - 1), signed_fixture(expires=AUTH_NOW),
    signed_fixture().replace(str(AUTH_NOW + 3600), str(AUTH_NOW + 7200)),
    signed_fixture().replace("offline-fixture-nonce", "tampered-nonce"),
    signed_fixture()[:-1] + ("0" if signed_fixture()[-1] != "0" else "1"),
    signed_fixture(secret="different-offline-owner-secret-no-valid-grant"),
    "synthetic-shared-site", None,
])
@isolated_case
def test_expired_or_tampered_signed_tokens_fail_before_json_and_provider(harness, token, caplog):
    code, body = harness.post(token=token, raw_body=b"{invalid-json", extra_headers=[
        ("X-Site-Live-Token", "synthetic-shared-site"), ("X-Trusted-User", "claimed-owner")])
    assert code == 401 and body == {"ok": False, "error": "existing private device authorization required"}
    assert harness.provider.calls == harness.calendar.calls == harness.credential_calls == []
    assert harness.pairing.requests == {} and not caplog.records


@isolated_case
def test_correct_signature_one_second_before_expiry_is_valid(harness):
    assert harness.post(token=signed_fixture(expires=AUTH_NOW + 1))[0] == 200


@isolated_case
def test_valid_device_still_cannot_bypass_origin_or_body_validation(harness):
    assert harness.post(token=signed_fixture(), origin="https://preview-wude-ai-stock-app.vercel.app")[0] == 403
    assert harness.post(token=signed_fixture(), raw_body=b'{"symbol":"AAPL","raw":true}')[0] == 400
    assert harness.post(token=signed_fixture(), raw_body=b"[]")[0] == 400
    assert harness.provider.calls == harness.calendar.calls == harness.credential_calls == []


@pytest.mark.parametrize("symbol", [*HELD_TRANSITIONS, "HNHPF"])
@isolated_case
def test_all_seven_held_symbols_keep_hold_through_signed_device_handler(harness, symbol, caplog):
    code, body = harness.post(token=signed_fixture(), symbol=symbol)
    assert code == 200 and body["ok"] is True
    data = body["data"]
    assert data["symbol"] == symbol and data["status"] == "blocked"
    assert data["features"] is data["plan"] is data["provenance"] is None
    assert data["decision_eligible"] is data["affects_formal"] is data["durable_retention"] is False
    if symbol == "HNHPF":
        assert data["reasons"] == ["insufficient_contiguous_indicator_history"]
        assert data["quality_sessions"] == ["2026-10-09"]
    else:
        assert data["reasons"] == ["adjusted_price_discontinuity_requires_review"]
        assert data["quality_sessions"] == [HELD_TRANSITIONS[symbol]]
    assert len(harness.provider.calls) == 2 and harness.provider.closed
    assert all(row.closed for row in harness.provider.responses)
    assert not caplog.records


@pytest.mark.parametrize("fail_on", [1, 2])
@pytest.mark.parametrize("status,reason", [
    (401, "provider_authentication_denied"), (403, "provider_entitlement_denied"),
    (429, "provider_rate_limited"), (500, "provider_http_failure"), (302, "provider_http_failure"),
])
@isolated_case
def test_provider_http_errors_are_fixed_private_reasons(harness, status, reason, fail_on, caplog):
    harness.provider.failure = lambda params: FakeResponse({"message": SYNTHETIC_MARKER}, status=status)
    harness.provider.fail_on = fail_on
    code, body = harness.post(token=signed_fixture())
    assert code == 503 and body == {"ok": False, "error": reason}
    assert len(harness.provider.calls) == fail_on and harness.provider.closed
    assert all(row.closed for row in harness.provider.responses)
    assert not harness.service._lock.locked() and not caplog.records


@pytest.mark.parametrize("failure,reason", [
    (lambda params: FakeResponse(None, raw=SYNTHETIC_MARKER.encode()), "invalid_provider_json"),
    (lambda params: FakeResponse({"message": SYNTHETIC_MARKER}), "invalid_provider_payload"),
    (lambda params: FakeResponse({"bars": {"UNKNOWN": []}}), "unexpected_provider_symbol"),
    (lambda params: FakeResponse({"bars": {"AAPL": []}, "next_page_token": SYNTHETIC_MARKER}), "request_time_budget"),
])
@isolated_case
def test_provider_parse_and_page_errors_do_not_escape_or_exceed_two_requests(harness, failure, reason, caplog):
    harness.provider.failure = failure
    code, body = harness.post(token=signed_fixture())
    assert code == 503 and body == {"ok": False, "error": reason}
    assert len(harness.provider.calls) == 2 and harness.provider.closed
    assert all(row.closed for row in harness.provider.responses)
    assert not harness.service._lock.locked() and not caplog.records


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError, RuntimeError])
@isolated_case
def test_provider_exceptions_are_generic_and_release_singleflight(harness, error, caplog):
    def fail(params):
        raise error(SYNTHETIC_MARKER)
    harness.provider.failure = fail
    code, body = harness.post(token=signed_fixture())
    reason = "private research unavailable" if error is RuntimeError else "provider_network_failure"
    assert code == 503 and body == {"ok": False, "error": reason}
    assert len(harness.provider.calls) == 2 and harness.provider.closed
    assert all(row.closed for row in harness.provider.responses)
    assert not harness.service._lock.locked() and not caplog.records
