"""Owner-only near-real-time quote API for the mobile dashboard.

The public GitHub Pages site must never contain broker or market-data keys.
This service is designed to run behind an authenticated cloud endpoint.  It
polls authoritative snapshots every few seconds for the symbol the owner is
currently viewing, while the normal briefing job continues to scan the full
universe in the background.
"""

from __future__ import annotations

import base64
from collections import deque
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from fubon_runner import _login_fubon, parse_fubon_quote
from us_market_data import fetch_us_opra_signals, fetch_us_sip_snapshots

LOG = logging.getLogger("live_api")
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^-]{1,20}(?:\.(?:TW|TWO))?$")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def normalize_symbol(symbol: str, market: str) -> tuple[str, str]:
    normalized_market = str(market or "").strip().upper()
    normalized_symbol = str(symbol or "").strip().upper()
    if normalized_market not in {"TW", "US"}:
        raise ValueError("market must be TW or US")
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("invalid symbol")
    if normalized_market == "TW" and not normalized_symbol.endswith((".TW", ".TWO")):
        normalized_symbol += ".TW"
    if normalized_market == "US":
        normalized_symbol = normalized_symbol.removesuffix(".TW").removesuffix(".TWO")
    return normalized_symbol, normalized_market


def configure_fubon_certificate(environ: dict[str, str] | os._Environ[str] | None = None) -> Path | None:
    """Materialize a base64 certificate into a mode-0600 temporary file.

    Cloud secret stores usually expose binary values as base64 environment
    variables.  The temporary file is never committed and is removed when the
    container is replaced.
    """

    env = os.environ if environ is None else environ
    configured = str(env.get("FUBON_CERT_PATH", "")).strip()
    if configured:
        return Path(configured)
    encoded = str(env.get("FUBON_CERT_BASE64", "")).strip()
    if not encoded:
        return None
    try:
        certificate = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("FUBON_CERT_BASE64 is invalid") from exc
    if not certificate:
        raise RuntimeError("FUBON_CERT_BASE64 is empty")
    fd, filename = tempfile.mkstemp(prefix="wude-fubon-", suffix=".p12")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(certificate)
    except Exception:
        Path(filename).unlink(missing_ok=True)
        raise
    env["FUBON_CERT_PATH"] = filename
    return Path(filename)


class LiveDataService:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        us_fetcher: Callable[..., dict[str, dict[str, Any]]] = fetch_us_sip_snapshots,
        option_fetcher: Callable[..., dict[str, dict[str, Any]]] = fetch_us_opra_signals,
        fubon_login: Callable[[], Any] = _login_fubon,
        quote_ttl: float | None = None,
        option_ttl: float | None = None,
    ) -> None:
        self.clock = clock
        self.us_fetcher = us_fetcher
        self.option_fetcher = option_fetcher
        self.fubon_login = fubon_login
        self.quote_ttl = quote_ttl if quote_ttl is not None else float(os.getenv("LIVE_QUOTE_TTL_SECONDS", "2"))
        self.option_ttl = option_ttl if option_ttl is not None else float(os.getenv("LIVE_OPTION_TTL_SECONDS", "60"))
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._option_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._fubon_sdk: Any = None
        self._lock = threading.RLock()

    def _cached(self, key: tuple[str, str]) -> dict[str, Any] | None:
        row = self._cache.get(key)
        if row and self.clock() - row[0] <= self.quote_ttl:
            result = dict(row[1])
            result["cached"] = True
            return result
        return None

    def _tw_quote(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            if self._fubon_sdk is None:
                configure_fubon_certificate()
                self._fubon_sdk = self.fubon_login()
            sdk = self._fubon_sdk
        stock_id = symbol.split(".", 1)[0]
        try:
            payload = sdk.marketdata.rest_client.stock.intraday.quote(symbol=stock_id)
            quote = parse_fubon_quote(payload)
        except Exception:
            with self._lock:
                self._fubon_sdk = None
            raise
        if not quote:
            raise RuntimeError("Fubon returned no quote")
        return {
            "ok": True,
            "symbol": symbol,
            "market": "TW",
            "source": "Fubon Neo",
            "fetched_at": quote.get("fetchedAt") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "quote": quote,
            "options": {},
            "cached": False,
        }

    def _us_options(self, symbol: str, quote: dict[str, Any]) -> dict[str, Any]:
        cached = self._option_cache.get(symbol)
        if cached and self.clock() - cached[0] <= self.option_ttl:
            return dict(cached[1])
        candidate = {
            "symbol": symbol,
            "price": _number(quote.get("us_live_price")),
            **quote,
        }
        options = self.option_fetcher([candidate]).get(symbol, {})
        self._option_cache[symbol] = (self.clock(), dict(options))
        return options

    def _us_quote(self, symbol: str, include_options: bool) -> dict[str, Any]:
        quote = self.us_fetcher({symbol}).get(symbol, {})
        if not quote:
            raise RuntimeError("SIP quote is unavailable")
        options = self._us_options(symbol, quote) if include_options else {}
        return {
            "ok": True,
            "symbol": symbol,
            "market": "US",
            "source": quote.get("us_live_source") or "Alpaca",
            "fetched_at": quote.get("us_live_fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "quote": quote,
            "options": options,
            "cached": False,
        }

    def fetch(self, symbol: str, market: str, *, include_options: bool = True) -> dict[str, Any]:
        symbol, market = normalize_symbol(symbol, market)
        key = (market, symbol)
        with self._lock:
            cached = self._cached(key)
        if cached is not None:
            return cached
        result = self._tw_quote(symbol) if market == "TW" else self._us_quote(symbol, include_options)
        with self._lock:
            self._cache[key] = (self.clock(), dict(result))
        return result

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "wude-live-api",
            "us_sip_configured": bool(os.getenv("ALPACA_API_KEY_ID") and os.getenv("ALPACA_API_SECRET_KEY")),
            "us_opra_configured": bool(os.getenv("ALPACA_OPTION_FEED")),
            "tw_fubon_configured": bool(
                os.getenv("FUBON_ID") and os.getenv("FUBON_API_KEY")
                and (os.getenv("FUBON_CERT_PATH") or os.getenv("FUBON_CERT_BASE64"))
            ),
        }


class MinuteRateLimiter:
    """Small in-process guard against a leaked link exhausting paid feeds."""

    def __init__(self, limit: int, *, clock: Callable[[], float] = time.time) -> None:
        self.limit = max(1, int(limit))
        self.clock = clock
        self.requests: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        now = self.clock()
        with self.lock:
            while self.requests and now - self.requests[0] >= 60:
                self.requests.popleft()
            if len(self.requests) >= self.limit:
                return False
            self.requests.append(now)
            return True


class LiveRequestHandler(BaseHTTPRequestHandler):
    service = LiveDataService()
    rate_limiter = MinuteRateLimiter(int(os.getenv("LIVE_MAX_REQUESTS_PER_MINUTE", "120")))

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin", "").rstrip("/")
        allowed = {value.strip().rstrip("/") for value in os.getenv(
            "LIVE_ALLOWED_ORIGINS", "https://a0953883367.github.io"
        ).split(",") if value.strip()}
        return origin if origin and origin in allowed else None

    def _authorized(self) -> bool:
        token = os.getenv("LIVE_ACCESS_TOKEN", "")
        if token:
            return self.headers.get("Authorization", "") == f"Bearer {token}"
        if _truthy(os.getenv("LIVE_PUBLIC_READ")):
            return True
        proxy_header = os.getenv("LIVE_TRUSTED_AUTH_HEADER", "").strip()
        return bool(proxy_header and self.headers.get(proxy_header))

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(HTTPStatus.OK, self.service.health())
            return
        if parsed.path != "/api/live":
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "owner authentication required"})
            return
        if not self.rate_limiter.allow():
            self._send(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "live request limit reached"})
            return
        query = parse_qs(parsed.query)
        try:
            result = self.service.fetch(
                query.get("symbol", [""])[0],
                query.get("market", [""])[0],
                include_options=query.get("options", ["1"])[0] != "0",
            )
        except ValueError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            LOG.warning("live quote failed: %s", exc)
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "live source temporarily unavailable"})
        else:
            self._send(HTTPStatus.OK, result)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), LiveRequestHandler)
    LOG.info("Wude live API listening on %s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
