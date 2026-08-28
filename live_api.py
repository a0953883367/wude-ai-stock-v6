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
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, time as wall_time, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fubon_runner import _login_fubon, parse_fubon_quote
from fubon_broker import FubonTradingSession
from large_buy_monitor import LargeBuyAlertService
from large_buy_streams import LargeBuyStreams
from live_trade_engine import LiveTradingEngine
from notifier import send_telegram
from trade_engine import JsonTradingStateStore, PaperTradingEngine, TAIPEI
from us_market_data import fetch_us_opra_signals, fetch_us_sip_snapshots
from web_push import WebPushService

LOG = logging.getLogger("live_api")
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^-]{1,20}(?:\.(?:TW|TWO))?$")
NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_SITE_LIVE_TOKEN_SHA256 = "0cf3e46b11bb22461985200095067592e354335fa026c4c33c58c9555544f06f"\nDEFAULT_VERCEL_APP_TOKEN_SHA256 = "80beb3c0100e5a4365a767019ac3e4dcb0f7d162915cf1efdf5570b4b577e638"


def _trading_state_path() -> Path:
    configured = os.getenv("TRADE_STATE_PATH", "").strip()
    if configured:
        return Path(configured)
    live = os.getenv("TRADING_MODE", "paper").strip().lower() == "live"
    filename = "live_trading_state.json" if live else "paper_trading_state.json"
    return Path("/data") / filename if Path("/data").is_dir() else Path("/tmp") / f"wude-{filename}"


def _large_buy_state_path() -> Path:
    configured = os.getenv("LARGE_BUY_STATE_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path("/data/large_buy_alerts.json") if Path("/data").is_dir() else Path("/tmp/wude-large-buy-alerts.json")


def _web_push_paths() -> tuple[Path, Path]:
    root = Path(os.getenv("WEB_PUSH_STATE_DIR", "/data" if Path("/data").is_dir() else "/tmp"))
    return root / "web_push_subscriptions.json", root / "web_push_vapid_private.pem"


def _paper_engine(service: "LiveDataService") -> PaperTradingEngine:
    root = Path(__file__).resolve().parent
    return PaperTradingEngine(
        JsonTradingStateStore(_trading_state_path(), initial_cash=20_000),
        report_path=root / "reports" / "all_analysis.json",
        quote_fetcher=lambda symbol, market: service.fetch(symbol, market, include_options=False),
    )


def _trading_engine(service: "LiveDataService") -> PaperTradingEngine:
    if os.getenv("TRADING_MODE", "paper").strip().lower() != "live":
        return _paper_engine(service)

    def broker_factory() -> FubonTradingSession:
        configure_fubon_certificate()
        return FubonTradingSession.login()

    root = Path(__file__).resolve().parent
    return LiveTradingEngine(
        JsonTradingStateStore(_trading_state_path(), initial_cash=20_000, mode="live"),
        report_path=str(root / "reports" / "all_analysis.json"),
        quote_fetcher=lambda symbol, market: service.fetch(symbol, market, include_options=False),
        broker_factory=broker_factory,
        stale_order_seconds=int(os.getenv("LIVE_ORDER_TIMEOUT_SECONDS", "180")),
    )


def _tw_market_open(now: datetime | None = None) -> bool:
    current = now or datetime.now(TAIPEI)
    return current.weekday() < 5 and wall_time(9, 0) <= current.time().replace(tzinfo=None) <= wall_time(13, 30)


def _us_market_open(now: datetime | None = None) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    return current.weekday() < 5 and wall_time(9, 30) <= current.time().replace(tzinfo=None) <= wall_time(16, 0)


def _open_market_sessions(now: datetime | None = None) -> list[tuple[str, str]]:
    current = now or datetime.now(timezone.utc)
    result: list[tuple[str, str]] = []
    if _tw_market_open(current.astimezone(TAIPEI)):
        result.append(("TW", current.astimezone(TAIPEI).date().isoformat()))
    if _us_market_open(current):
        result.append(("US", current.astimezone(NEW_YORK).date().isoformat()))
    return result


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
            intraday = sdk.marketdata.rest_client.stock.intraday
            payload = intraday.quote(symbol=stock_id)
            quote = parse_fubon_quote(payload)
            if not quote:
                raise RuntimeError("Fubon returned no quote")
            quote["orderBookType"] = "regular"
        except Exception:
            with self._lock:
                self._fubon_sdk = None
            raise

        # The owner trades a small TWD account, so actual orders will often be
        # intraday odd lots. Some symbols can have no regular-lot depth even
        # while the market is open. Fubon exposes a separate odd-lot five-level
        # book; use it only when the regular book is incomplete. A failed
        # fallback must not discard an otherwise valid regular-lot price.
        if quote.get("bidTotal") is None or quote.get("askTotal") is None:
            try:
                odd_payload = intraday.quote(symbol=stock_id, type="oddlot")
                odd_quote = parse_fubon_quote(odd_payload)
                if odd_quote.get("bidTotal") is not None and odd_quote.get("askTotal") is not None:
                    quote = odd_quote
                    quote["orderBookType"] = "oddlot"
            except Exception as exc:
                LOG.warning("Fubon odd-lot book fallback failed for %s: %s", stock_id, exc)
        quote["orderBookComplete"] = (
            quote.get("bidTotal") is not None and quote.get("askTotal") is not None
        )
        return {
            "ok": True,
            "symbol": symbol,
            "market": "TW",
            "source": "Fubon Neo",
            "fetched_at": quote.get("fetchedAt") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "quote": quote,
            "quote_status": "complete" if quote["orderBookComplete"] else "order_book_missing",
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
            "auth_version": 2,
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
    trading_engine = _trading_engine(service)
    _push_state_path, _push_key_path = _web_push_paths()
    web_push = WebPushService(
        _push_state_path,
        _push_key_path,
        subject=os.getenv("WEB_PUSH_SUBJECT", "mailto:a0953883367@gmail.com"),
    )
    large_buy_service = LargeBuyAlertService(
        Path(__file__).resolve().parent / "reports" / "all_analysis.json",
        _large_buy_state_path(),
        notifier=send_telegram,
        alert_notifier=web_push.send_alert,
    )
    rate_limiter = MinuteRateLimiter(int(os.getenv("LIVE_MAX_REQUESTS_PER_MINUTE", "120")))

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin", "").rstrip("/")
        allowed = {value.strip().rstrip("/") for value in os.getenv(
            "LIVE_ALLOWED_ORIGINS", "https://a0953883367.github.io"
        ).split(",") if value.strip()}
        return origin if origin and origin in allowed else None

    def _owner_authorized(self) -> bool:
        token = os.getenv("LIVE_ACCESS_TOKEN", "").strip()
        if token:
            authorization = self.headers.get("Authorization", "").strip()
            supplied = ""
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()
            if not supplied:
                supplied = self.headers.get("X-Live-Token", "").strip()
            return bool(supplied and hmac.compare_digest(supplied, token))
        proxy_header = os.getenv("LIVE_TRUSTED_AUTH_HEADER", "").strip()
        return bool(proxy_header and self.headers.get(proxy_header))

    def _site_read_authorized(self) -> bool:
        supplied = self.headers.get("X-Site-Live-Token", "").strip()
        if not supplied:
            return False
        expected_hashes = {
            os.getenv("LIVE_SITE_TOKEN_SHA256", DEFAULT_SITE_LIVE_TOKEN_SHA256).strip().lower(),
            os.getenv("VERCEL_APP_TOKEN_SHA256", DEFAULT_VERCEL_APP_TOKEN_SHA256).strip().lower(),
        }
        expected_hashes.discard("")
        digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        return any(hmac.compare_digest(digest, expected) for expected in expected_hashes)

    def _authorized(self) -> bool:
        return self._owner_authorized() or self._site_read_authorized() or _truthy(os.getenv("LIVE_PUBLIC_READ"))

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
            self.send_header("Access-Control-Allow-Headers", "Authorization, X-Live-Token, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            health = self.service.health()
            monitor = self.large_buy_service.snapshot(after=self.large_buy_service.store.latest_sequence)
            health["large_buy_monitor"] = {
                "enabled": _truthy(os.getenv("LARGE_BUY_MONITOR_ENABLED", "1")),
                "universe": monitor["universe"],
                "streams": monitor["streams"],
                "telegram_configured": bool(
                    os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")
                ),
                "web_push_subscriptions": self.web_push.subscription_count,
            }
            self._send(HTTPStatus.OK, health)
            return
        if parsed.path not in {
            "/api/live", "/api/large-buy-alerts", "/api/push/config",
            "/api/trading/status", "/api/trading/preview"
        }:
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        requires_owner = parsed.path.startswith("/api/trading/")
        if not (self._owner_authorized() if requires_owner else self._authorized()):
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "owner authentication required"})
            return
        if not self.rate_limiter.allow():
            self._send(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "live request limit reached"})
            return
        if parsed.path == "/api/trading/status":
            self._send(HTTPStatus.OK, {"ok": True, **self.trading_engine.status()})
            return
        if parsed.path == "/api/trading/preview":
            self._send(HTTPStatus.OK, {"ok": True, **self.trading_engine.preview()})
            return
        if parsed.path == "/api/push/config":
            self._send(HTTPStatus.OK, {
                "ok": True,
                "public_key": self.web_push.public_key,
                "subscriptions": self.web_push.subscription_count,
            })
            return

        query = parse_qs(parsed.query)
        if parsed.path == "/api/large-buy-alerts":
            try:
                after = max(0, int(query.get("after", ["0"])[0]))
                limit = max(1, min(100, int(query.get("limit", ["50"])[0])))
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid alert cursor"})
                return
            self._send(HTTPStatus.OK, {
                "ok": True,
                **self.large_buy_service.snapshot(after=after, limit=limit),
                "notifications": {
                    "website": True,
                    "telegram_configured": bool(
                        os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")
                    ),
                    "web_push_subscriptions": self.web_push.subscription_count,
                },
            })
            return
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

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0 or length > 65_536:
            raise ValueError("JSON body must be between 1 and 65536 bytes")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/push/subscribe", "/api/trading/config", "/api/trading/run"}:
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        requires_owner = parsed.path.startswith("/api/trading/")
        if not (self._owner_authorized() if requires_owner else self._authorized()):
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "owner authentication required"})
            return
        if not self.rate_limiter.allow():
            self._send(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "request limit reached"})
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/push/subscribe":
                count = self.web_push.subscribe(payload.get("subscription"))
                result = {"ok": True, "subscribed": True, "subscriptions": count}
            elif parsed.path == "/api/trading/config":
                config_args: dict[str, Any] = {
                    "selected": list(payload.get("selected") or []),
                    "cash_limit": int(payload.get("cash_limit", 20_000)),
                    "enabled": payload.get("enabled"),
                    "emergency_stop": payload.get("emergency_stop"),
                }
                if isinstance(self.trading_engine, LiveTradingEngine):
                    config_args["live_confirmation"] = payload.get("live_confirmation")
                state = self.trading_engine.configure(
                    **config_args,
                )
                result = {"ok": True, "state": state, "preview": self.trading_engine.preview()}
            else:
                sessions = _open_market_sessions()
                if not sessions:
                    mode_label = "真實下單" if isinstance(self.trading_engine, LiveTradingEngine) else "模擬撮合"
                    result = {
                        "ok": True,
                        "state": self.trading_engine.status(),
                        "skipped": True,
                        "message": f"目前非台股或美股正常交易時段；設定已保留，下一個市場開盤後才會執行{mode_label}",
                    }
                else:
                    state = None
                    for market, session_date in sessions:
                        state = self.trading_engine.run_cycle(session_date=session_date, markets={market})
                    result = {"ok": True, "state": state, "skipped": False}
        except PermissionError as exc:
            self._send(HTTPStatus.FORBIDDEN, {"ok": False, "error": str(exc)})
        except (TypeError, ValueError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            LOG.exception("live request failed: %s", exc)
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "live service temporarily unavailable"})
        else:
            self._send(HTTPStatus.OK, result)


def _trading_monitor(handler: type[LiveRequestHandler], stop: threading.Event) -> None:
    interval = max(15.0, float(os.getenv(
        "TRADING_INTERVAL_SECONDS",
        os.getenv("PAPER_TRADING_INTERVAL_SECONDS", "60"),
    )))
    while not stop.wait(interval):
        for market, session_date in _open_market_sessions():
            try:
                handler.trading_engine.run_cycle(session_date=session_date, markets={market})
            except Exception:
                LOG.exception("%s trading monitor cycle failed", market)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), LiveRequestHandler)
    monitor_stop = threading.Event()
    monitor = threading.Thread(
        target=_trading_monitor,
        args=(LiveRequestHandler, monitor_stop),
        name="trading-monitor",
        daemon=True,
    )
    monitor.start()
    large_buy_streams: LargeBuyStreams | None = None
    if _truthy(os.getenv("LARGE_BUY_MONITOR_ENABLED", "1")):
        large_buy_streams = LargeBuyStreams(LiveRequestHandler.large_buy_service, _login_fubon)
        large_buy_streams.start()
    else:
        for market in ("TW", "US"):
            LiveRequestHandler.large_buy_service.set_stream_status(market, "disabled")
    LOG.info("Wude live API listening on %s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        monitor_stop.set()
        if large_buy_streams is not None:
            large_buy_streams.stop()


if __name__ == "__main__":
    main()
