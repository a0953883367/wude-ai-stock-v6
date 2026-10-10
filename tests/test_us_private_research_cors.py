"""Offline request-handler coverage for the one-origin private research exception."""
import hashlib
import io
import json
from email.message import Message
from types import SimpleNamespace

import pytest

import live_api


ORIGIN = "https://wude-ai-stock-app.vercel.app"
GITHUB = "https://a0953883367.github.io"
PATH = "/api/private/us-research"
OWNER = "existing-owner-token-for-offline-tests"
DEVICE = "existing-paired-device-for-offline-tests"


@pytest.fixture(autouse=True)
def isolated_auth_and_no_network(monkeypatch):
    import requests
    import socket
    monkeypatch.setenv("LIVE_ALLOWED_ORIGINS", GITHUB)
    monkeypatch.setenv("LIVE_ACCESS_TOKEN", OWNER)
    monkeypatch.setenv("LIVE_PUBLIC_READ", "1")
    monkeypatch.setenv("LIVE_SITE_TOKEN_SHA256", hashlib.sha256(b"shared-site").hexdigest())
    monkeypatch.setenv("VERCEL_APP_TOKEN_SHA256", hashlib.sha256(b"shared-vercel").hexdigest())
    monkeypatch.setenv("LIVE_TRUSTED_AUTH_HEADER", "X-Trusted-User")
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network forbidden"))


def handler(*, method="POST", path=PATH, origin=ORIGIN, headers=(), body=None):
    h = object.__new__(live_api.LiveRequestHandler)
    h.command, h.path = method, path
    h.requestline = f"{method} {path} HTTP/1.1"
    h.headers = Message()
    if origin is not None:
        h.headers["Origin"] = origin
    for key, value in headers:
        h.headers[key] = value
    h.device_pairing = SimpleNamespace(token_valid=lambda value: value == DEVICE)
    h.large_buy_service = SimpleNamespace(weight_shadow=SimpleNamespace(calendar=object()))
    h.rate_limiter = SimpleNamespace(allow=lambda: True)
    calls = {"body": 0, "provider": 0}
    def read():
        calls["body"] += 1
        return {"symbol": "AAPL"} if body is None else body
    def research(*args):
        calls["provider"] += 1
        return {"symbol": "AAPL", "status": "blocked", "decision_eligible": False}
    h._read_json = read
    h.private_research_service = SimpleNamespace(research=research)
    response = {"headers": {}}
    h.send_response = lambda code: response.update(status=int(code))
    h.send_header = lambda name, value: response["headers"].update({name: value})
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    return h, response, calls


def preflight(*, path=PATH, origin=ORIGIN, request_method="POST", request_headers="content-type, x-live-token", extra=()):
    headers = list(extra)
    if request_method is not None:
        headers.append(("Access-Control-Request-Method", request_method))
    if request_headers is not None:
        headers.append(("Access-Control-Request-Headers", request_headers))
    return handler(method="OPTIONS", path=path, origin=origin, headers=headers)


def assert_private_headers(response):
    assert response["headers"]["Access-Control-Allow-Origin"] == ORIGIN
    assert response["headers"]["Cache-Control"] == "no-store"
    assert "Origin" in response["headers"]["Vary"]
    assert "Access-Control-Allow-Credentials" not in response["headers"]
    assert "Set-Cookie" not in response["headers"]
    assert "*" not in response["headers"].values()


def test_valid_preflight_is_unauthenticated_but_does_not_grant_data_access():
    h, response, calls = preflight()
    h.device_pairing.token_valid = lambda value: pytest.fail("preflight must not authenticate")
    h.do_OPTIONS()
    assert response["status"] == 204
    assert_private_headers(response)
    assert response["headers"]["Access-Control-Allow-Methods"] == "POST"
    assert response["headers"]["Access-Control-Allow-Headers"] == "Content-Type, X-Live-Token"
    assert "Access-Control-Request-Method" in response["headers"]["Vary"]
    assert "Access-Control-Request-Headers" in response["headers"]["Vary"]
    assert response["headers"]["Content-Length"] == "0"
    assert h.wfile.getvalue() == b""
    assert calls == {"body": 0, "provider": 0}
    assert h._origin() is None, "the general allowlist must remain unchanged"


@pytest.mark.parametrize("request_headers", ["X-Live-Token, Content-Type", "content-type", "x-live-token"])
def test_preflight_only_accepts_existing_explicit_token_and_json_header_names(request_headers):
    h, response, _ = preflight(request_headers=request_headers)
    h.do_OPTIONS()
    assert response["status"] == 204
    assert_private_headers(response)


@pytest.mark.parametrize("request_method", [None, "", "GET", "PUT", "DELETE", "HEAD", "OPTIONS", "post", "POST, GET"])
def test_other_preflight_methods_are_denied(request_method):
    h, response, calls = preflight(request_method=request_method)
    h.do_OPTIONS()
    assert response["status"] == 403
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert response["headers"]["Cache-Control"] == "no-store"
    assert calls == {"body": 0, "provider": 0}


@pytest.mark.parametrize("request_headers", [None, "", " ", ",", "x-live-token,", "Authorization", "Cookie",
    "content-type, x-site-live-token", "x-live-token, x-arbitrary", "*", "x-live-token\ncontent-type"])
def test_other_preflight_headers_are_denied(request_headers):
    h, response, calls = preflight(request_headers=request_headers)
    h.do_OPTIONS()
    assert response["status"] == 403
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert "Access-Control-Allow-Credentials" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


@pytest.mark.parametrize("duplicate", [
    ("Access-Control-Request-Method", "POST"),
    ("Access-Control-Request-Headers", "x-live-token"),
])
def test_duplicate_preflight_fields_are_denied(duplicate):
    h, response, _ = preflight(extra=[duplicate])
    h.do_OPTIONS()
    assert response["status"] == 403
    assert "Access-Control-Allow-Origin" not in response["headers"]


@pytest.mark.parametrize("headers", [
    [("X-Live-Token", DEVICE)], [("X-Live-Token", OWNER)], [("Authorization", "Bearer " + OWNER)],
])
def test_existing_owner_or_paired_device_can_post_without_cookie_credentials(headers):
    h, response, calls = handler(headers=headers)
    h.do_POST()
    assert response["status"] == 200
    assert_private_headers(response)
    assert calls == {"body": 1, "provider": 1}
    assert json.loads(h.wfile.getvalue())["data"]["decision_eligible"] is False


@pytest.mark.parametrize("headers", [
    [], [("X-Live-Token", "wrong")], [("Authorization", "Bearer wrong")],
    [("X-Site-Live-Token", "shared-site")], [("X-Site-Live-Token", "shared-vercel")],
    [("X-Trusted-User", "asserted-owner")], [("Cookie", "X-Live-Token=" + OWNER)],
    [("Cookie", "live_token=" + DEVICE)], [("X-Live-Token", "非ASCII")],
])
def test_public_site_proxy_cookie_and_invalid_tokens_never_authorize(headers):
    h, response, calls = handler(headers=headers)
    h.do_POST()
    assert response["status"] == 401
    assert_private_headers(response)
    assert calls == {"body": 0, "provider": 0}
    assert "data" not in json.loads(h.wfile.getvalue())


def test_missing_owner_secret_does_not_enable_proxy_or_public_access(monkeypatch):
    monkeypatch.delenv("LIVE_ACCESS_TOKEN", raising=False)
    h, response, calls = handler(headers=[("X-Trusted-User", "asserted-owner")])
    h.do_POST()
    assert response["status"] == 401
    assert calls == {"body": 0, "provider": 0}


def test_cookie_present_with_valid_explicit_token_still_has_no_credentials_mode_grant():
    h, response, _ = handler(headers=[("X-Live-Token", DEVICE), ("Cookie", "unrelated=1")])
    h.do_POST()
    assert response["status"] == 200
    assert_private_headers(response)


@pytest.mark.parametrize("origin", [None, "", "null", ORIGIN + "/", ORIGIN + ":443", " " + ORIGIN,
    ORIGIN + " ", ORIGIN.replace("https:", "http:"), ORIGIN.upper(), ORIGIN + ".evil.test",
    ORIGIN + "@evil.test", ORIGIN + "?q=x", ORIGIN + "#fragment", ORIGIN + ", https://evil.test",
    "https://preview-wude-ai-stock-app.vercel.app", "https://wude-ai-stock-app-git-preview.vercel.app",
    "https://alias.example.test"])
def test_only_literal_production_origin_is_added(origin):
    h, response, calls = handler(origin=origin, headers=[("X-Live-Token", DEVICE)])
    h.do_POST()
    assert response["status"] == 403
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}
    h, response, calls = preflight(origin=origin)
    h.do_OPTIONS()
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert "Access-Control-Allow-Credentials" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


def test_duplicate_origin_is_not_accepted():
    h, response, calls = handler(headers=[("X-Live-Token", DEVICE), ("Origin", ORIGIN)])
    h.do_POST()
    assert response["status"] == 403
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


@pytest.mark.parametrize("path", [PATH + "?", PATH + "?symbol=AAPL", PATH + "#", PATH + "#fragment",
    PATH + "/", PATH + "/extra", PATH + ";param", PATH.upper(), "/api/private/%75s-research",
    "/api//private/us-research", "/api/private/../private/us-research", "/api/private/us-research%2F",
    "https://wude-ai-stock-v6-production.up.railway.app" + PATH])
def test_private_path_variants_do_not_receive_exception_or_start_research(path):
    h, response, calls = handler(path=path, headers=[("X-Live-Token", DEVICE)])
    h.do_POST()
    assert response["status"] in {400, 403, 404}
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}
    h, response, calls = preflight(path=path)
    h.do_OPTIONS()
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


@pytest.mark.parametrize("method", ["POST", "OPTIONS"])
def test_raw_double_slash_target_cannot_use_http_server_normalization(method):
    h, response, calls = (preflight() if method == "OPTIONS" else handler(headers=[("X-Live-Token", DEVICE)]))
    h.requestline = f"{method} /{PATH} HTTP/1.1"
    getattr(h, "do_" + method)()
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


@pytest.mark.parametrize("path", ["/health", "/api/live", "/api/large-buy-alerts", "/api/push/config",
    "/api/device-auth/request", "/api/device-auth/verify", "/api/trading/config", "/api/internal/market-data"])
def test_no_other_route_gets_vercel_response_or_preflight_permission(path):
    for method in ("POST", "GET", "OPTIONS"):
        h, response, calls = handler(method=method, path=path)
        if method == "OPTIONS":
            h.do_OPTIONS()
        else:
            h._send(401, {"ok": False})
        assert h._origin() is None
        assert "Access-Control-Allow-Origin" not in response["headers"]
        assert "Access-Control-Allow-Credentials" not in response["headers"]
        assert calls == {"body": 0, "provider": 0}


def test_vercel_cannot_start_a_new_pairing_flow():
    h, response, calls = handler(path="/api/device-auth/request", headers=[("X-Live-Token", DEVICE)])
    h.device_pairing.request_code = lambda: pytest.fail("must not pair")
    h.do_POST()
    assert response["status"] == 403
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


def test_get_on_exact_private_path_does_not_receive_exception():
    h, response, calls = handler(method="GET", headers=[("X-Live-Token", DEVICE)])
    h.do_GET()
    assert response["status"] == 404
    assert "Access-Control-Allow-Origin" not in response["headers"]
    assert calls == {"body": 0, "provider": 0}


@pytest.mark.parametrize("status", [200, 400, 401, 403, 429, 503])
def test_all_exact_private_post_responses_are_no_store_and_omit_cookie_credentials(status):
    h, response, _ = handler()
    h._send(status, {"ok": status == 200})
    assert_private_headers(response)


def test_provider_exception_is_generic_and_private_requests_never_log_values(caplog):
    h, response, calls = handler(headers=[("X-Live-Token", DEVICE)])
    def fail(*args):
        raise RuntimeError("SECRET_MARKET_VALUE_FOR_TEST")
    h.private_research_service.research = fail
    h.do_POST()
    h.log_message("%s", "SECRET_MARKET_VALUE_FOR_TEST")
    assert response["status"] == 503
    assert_private_headers(response)
    assert "SECRET_MARKET_VALUE" not in h.wfile.getvalue().decode() + caplog.text
    assert not caplog.records


def test_github_origin_keeps_its_existing_behavior():
    h, response, calls = handler(origin=GITHUB, headers=[("X-Live-Token", DEVICE)])
    h.do_POST()
    assert response["status"] == 200
    assert response["headers"]["Access-Control-Allow-Origin"] == GITHUB
    assert response["headers"]["Access-Control-Allow-Credentials"] == "true"
    assert calls == {"body": 1, "provider": 1}
    h, response, _ = preflight(origin=GITHUB, path="/api/live")
    h.do_OPTIONS()
    assert response["status"] == 204
    assert response["headers"]["Access-Control-Allow-Origin"] == GITHUB
    assert response["headers"]["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert response["headers"]["Access-Control-Allow-Headers"] == "Authorization, X-Live-Token, Content-Type"
    assert response["headers"]["Access-Control-Allow-Credentials"] == "true"
