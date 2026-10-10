"""Fixed two-symbol, existing-session Fubon daily pilot; raw bars stay in memory."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
import threading
import time as clock
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "TW-FUBON-DAILY-PILOT-V1"
SOURCE = "Fubon Neo historical candles"
TAIPEI = ZoneInfo("Asia/Taipei")
PILOT = {"2330.TW": ("TWSE", "TSE", "TWSE"), "6290.TWO": ("TPEx", "OTC", "TPEX_MAINBOARD")}
MAX_DAYS = 120
MAX_SECONDS = 20
MAX_ROWS = 120
REASONS = {
    "existing_session_unavailable", "request_in_progress", "request_time_budget",
    "official_calendar_unavailable", "official_calendar_unverified", "official_calendar_invalid",
    "sdk_method_unavailable", "provider_authentication_denied", "provider_entitlement_denied",
    "provider_rate_limited", "provider_failure", "invalid_provider_payload", "invalid_provider_identity",
    "invalid_bar_session", "duplicate_provider_session", "invalid_ohlcv", "incomplete_session_coverage",
    "insufficient_history", "adjustment_conflict", "response_row_budget",
}


class HistoryBlocked(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate_symbols(symbols: Any) -> list[str]:
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 2
            or any(not isinstance(s, str) or s not in PILOT for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("Fubon daily pilot permits only 2330.TW and 6290.TWO, once each")
    return list(symbols)


def _sessions(calendar: Any, now: datetime) -> list[str]:
    today = now.astimezone(TAIPEI).date()
    # Official publication contract: by 16:30 Taipei. Never guess weekday sessions.
    end = today if now.astimezone(TAIPEI).time() >= time(16, 30) else today - timedelta(days=1)
    start = end - timedelta(days=MAX_DAYS - 1)
    try:
        result = calendar.lookup("TW", (start - timedelta(days=1)).isoformat(), end.isoformat())
    except Exception:
        raise HistoryBlocked("official_calendar_unavailable") from None
    if not isinstance(result, dict) or result.get("available") is not True:
        raise HistoryBlocked("official_calendar_unavailable")
    if result.get("status") != "verified" or result.get("source_statuses") != ["verified_twse_tpex"]:
        raise HistoryBlocked("official_calendar_unverified")
    sessions = result.get("sessions")
    try:
        if not isinstance(sessions, list) or not sessions or len(sessions) > MAX_ROWS:
            raise ValueError()
        if sessions != sorted(set(sessions)):
            raise ValueError()
        if any(not isinstance(s, str) or date.fromisoformat(s).isoformat() != s
               or not start <= date.fromisoformat(s) <= end for s in sessions):
            raise ValueError()
    except (ValueError, TypeError):
        raise HistoryBlocked("official_calendar_invalid") from None
    return sessions


def _number(value: Any, *, volume=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HistoryBlocked("invalid_ohlcv")
    if not math.isfinite(value) or (value < 0 if volume else value <= 0):
        raise HistoryBlocked("invalid_ohlcv")
    if volume and (not float(value).is_integer() or value > 2**53):
        raise HistoryBlocked("invalid_ohlcv")
    return int(value) if volume else float(value)


def normalize(payload: Any, symbol: str, sessions: list[str], now: datetime) -> dict[str, Any]:
    exchange, market, venue = PILOT[symbol]
    if not isinstance(payload, dict):
        raise HistoryBlocked("invalid_provider_payload")
    # Fubon uses TPEx for exchange, OTC for main-board market; ESB is excluded.
    if (payload.get("symbol") != symbol.split(".")[0] or payload.get("type") != "EQUITY"
            or payload.get("exchange") != exchange or payload.get("market") != market
            or payload.get("timeframe") != "D"):
        raise HistoryBlocked("invalid_provider_identity")
    if "adjusted" in payload and payload["adjusted"] is not False:
        raise HistoryBlocked("adjustment_conflict")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise HistoryBlocked("invalid_provider_payload")
    if len(rows) > MAX_ROWS:
        raise HistoryBlocked("response_row_budget")
    expected = set(sessions)
    by_date = {}
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("date"), str) or raw["date"] not in expected:
            raise HistoryBlocked("invalid_bar_session")
        day = raw["date"]
        if day in by_date:
            raise HistoryBlocked("duplicate_provider_session")
        values = {k: _number(raw.get(k), volume=k == "volume") for k in ("open", "high", "low", "close", "volume")}
        if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]) or values["low"] > values["high"]:
            raise HistoryBlocked("invalid_ohlcv")
        by_date[day] = {"date": day, **values}
    if set(by_date) != expected:
        raise HistoryBlocked("incomplete_session_coverage")
    if len(by_date) < 60:
        raise HistoryBlocked("insufficient_history")
    bars = [by_date[day] for day in sessions]
    return {"status": "validated_in_memory", "symbol": symbol, "market": "TW", "venue": venue,
            "source": SOURCE, "source_session_date": sessions[-1], "observed_at": now.isoformat(),
            "price_basis": "raw_unadjusted", "adjustment_evidence": "explicit_request_and_provider_contract",
            "adjustment_echo_verified": payload.get("adjusted") is False,
            "volume_unit": "shares", "volume_unit_evidence": "provider_daily_contract",
            "request": {"timeframe": "D", "adjusted": "false", "from": sessions[0], "to": sessions[-1]},
            "bars": bars, "response_sha256": hashlib.sha256(json.dumps(bars, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "decision_eligible": False, "official_latest_crosscheck_verified": False,
            "corporate_action_coverage_verified": False, "point_in_time_availability_proven": False}


def empty_status(symbols: list[str], now: datetime, reason: str | None = None) -> dict[str, Any]:
    result = {"version": VERSION, "source": SOURCE, "status": "blocked" if reason else "validated_in_memory",
              "requested_count": len(symbols), "validated_count": 0, "request_count": 0, "request_count_known": True,
              "observed_at": now.isoformat(), "decision_eligible": False, "affects_formal": False,
              "durable_raw_retention": False, "market_values_exported": False,
              "corporate_action_coverage_verified": False, "official_latest_crosscheck_verified": False,
              "point_in_time_availability_proven": False, "symbols": {}}
    if reason:
        result["reason"] = reason
    return result


def collect_private(reststock: Any, symbols: list[str], calendar: Any, *, now=None, deadline=None,
                    cancelled=None, observed_clock=None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return safe status and private normalized rows separately; never persist either."""
    symbols = validate_symbols(symbols)
    now = now or datetime.now(TAIPEI)
    deadline = deadline if deadline is not None else clock.monotonic() + MAX_SECONDS
    status, private = empty_status(symbols, now), {}
    observed_clock = observed_clock or (lambda: datetime.now(TAIPEI))
    status["requested_at"] = now.isoformat()
    try:
        sessions = _sessions(calendar, now)
        status.update(session_date=sessions[-1], calendar_session_count=len(sessions))
        method = getattr(getattr(reststock, "historical", None), "candles", None)
        if not callable(method):
            raise HistoryBlocked("sdk_method_unavailable")
        for symbol in symbols:
            if clock.monotonic() >= deadline or (cancelled and cancelled.is_set()):
                raise HistoryBlocked("request_time_budget")
            status["request_count"] += 1
            try:
                payload = method(**{"symbol": symbol.split(".")[0], "from": sessions[0], "to": sessions[-1],
                                    "timeframe": "D", "adjusted": "false", "fields": "open,high,low,close,volume", "sort": "asc"})
            except Exception as exc:
                code = getattr(exc, "status_code", None)
                reason = {401: "provider_authentication_denied", 403: "provider_entitlement_denied", 429: "provider_rate_limited"}.get(code, "provider_failure")
                raise HistoryBlocked(reason) from None
            if clock.monotonic() >= deadline or (cancelled and cancelled.is_set()):
                raise HistoryBlocked("request_time_budget")
            try:
                private[symbol] = normalize(payload, symbol, sessions, observed_clock())
                private[symbol]["requested_at"] = now.isoformat()
                status["symbols"][symbol] = {"status": "validated_in_memory", "bar_count": len(private[symbol]["bars"]), "venue": PILOT[symbol][2]}
                status["validated_count"] += 1
            except HistoryBlocked as exc:
                status["symbols"][symbol] = {"status": "blocked", "reason": exc.reason, "bar_count": 0, "venue": PILOT[symbol][2]}
        if status["validated_count"] != len(symbols):
            status["status"] = "partial_in_memory" if private else "blocked"
    except HistoryBlocked as exc:
        status.update(status="blocked", reason=exc.reason)
        private.clear()
        status["validated_count"] = 0
        status["symbols"] = {}
    status["observed_at"] = observed_clock().isoformat()
    return status, private


class DailyPilot:
    """One in-flight request; caller timeout cannot cancel an SDK socket operation.

    An overdue worker retains the guard until it exits, discards its response,
    and starts no second symbol. No SDK auth/session/timeouts are mutated.
    """
    def __init__(self):
        self._guard = threading.Lock()

    def status(self, sdk: Any, symbols: Any, calendar: Any, *, timeout=MAX_SECONDS):
        symbols = validate_symbols(symbols)
        now = datetime.now(TAIPEI)
        if sdk is None:
            return empty_status(symbols, now, "existing_session_unavailable")
        if not self._guard.acquire(blocking=False):
            return empty_status(symbols, now, "request_in_progress")
        done, cancelled = threading.Event(), threading.Event()
        result = {}
        def worker():
            try:
                safe, private = collect_private(sdk.marketdata.rest_client.stock, symbols, calendar,
                                                now=now, deadline=clock.monotonic() + timeout, cancelled=cancelled)
                private.clear()
                if not cancelled.is_set():
                    result.update(safe)
            except Exception:
                if not cancelled.is_set():
                    result.update(empty_status(symbols, now, "provider_failure"))
            finally:
                self._guard.release()
                done.set()
        threading.Thread(target=worker, daemon=True).start()
        if not done.wait(timeout):
            cancelled.set()
            timed_out = empty_status(symbols, now, "request_time_budget")
            timed_out.update(request_count=None, request_count_known=False)
            return timed_out
        return result
