"""Fixed two-symbol, existing-session Fubon daily pilot; raw bars stay in memory."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time as clock
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "TW-FUBON-DAILY-PILOT-V2"
SOURCE = "Fubon Neo historical candles"
TAIPEI = ZoneInfo("Asia/Taipei")
PILOT = {"2330.TW": ("TWSE", "TSE", "TWSE"), "6290.TWO": ("TPEx", "OTC", "TPEX_MAINBOARD")}
MAX_DAYS = 120
MAX_SECONDS = 20
MAX_ROWS = 120
MAX_FORMAL_BYTES = 10 * 1024 * 1024
MAX_OFFICIAL_BYTES = 2 * 1024 * 1024
MAX_OFFICIAL_ROWS = 5000
OFFICIAL_REASONS = {
    "official_snapshot_reused", "official_records_available", "official_record_unavailable",
    "official_session_mismatch", "official_http_denied", "official_rate_limited", "official_http_error",
    "official_redirect_blocked", "official_body_budget", "official_row_budget",
    "official_payload_invalid", "official_parse_failure", "official_request_failure",
}
REASONS = {
    "existing_session_unavailable", "request_in_progress", "request_time_budget",
    "official_calendar_unavailable", "official_calendar_unverified", "official_calendar_invalid",
    "sdk_method_unavailable", "provider_authentication_denied", "provider_entitlement_denied",
    "provider_rate_limited", "provider_failure", "invalid_provider_payload", "invalid_provider_identity",
    "invalid_bar_session", "duplicate_provider_session", "invalid_ohlcv", "incomplete_session_coverage",
    "insufficient_history", "adjustment_conflict", "response_row_budget",
}


class HistoryBlocked(Exception):
    def __init__(self, reason: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


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


def _date_diagnostics(rows: list[Any], sessions: list[str]) -> dict[str, Any]:
    """Only canonical date/count metadata, never values or arbitrary strings."""
    observed = set()
    for row in rows:
        value = row.get("date") if isinstance(row, dict) else None
        if not isinstance(value, str):
            continue
        try:
            if date.fromisoformat(value).isoformat() == value:
                observed.add(value)
        except ValueError:
            continue
    dates = sorted(observed)
    return {"observed_date_count": len(dates), "expected_session_count": len(sessions),
            "missing_session_dates": sorted(set(sessions) - observed),
            "unexpected_session_dates": sorted(observed - set(sessions)),
            "first_returned_date": dates[0] if dates else None,
            "last_returned_date": dates[-1] if dates else None}


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
    diagnostics = _date_diagnostics(rows, sessions)
    by_date = {}
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("date"), str) or raw["date"] not in expected:
            raise HistoryBlocked("invalid_bar_session", diagnostics)
        day = raw["date"]
        if day in by_date:
            raise HistoryBlocked("duplicate_provider_session", diagnostics)
        values = {k: _number(raw.get(k), volume=k == "volume") for k in ("open", "high", "low", "close", "volume")}
        if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]) or values["low"] > values["high"]:
            raise HistoryBlocked("invalid_ohlcv")
        by_date[day] = {"date": day, **values}
    if set(by_date) != expected:
        raise HistoryBlocked("incomplete_session_coverage", diagnostics)
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
        status.update(session_date=sessions[-1], calendar_session_count=len(sessions),
                      request_from=sessions[0], request_to=sessions[-1], request_timeframe="D",
                      request_adjusted="false", volume_unit="shares",
                      calendar_source_status="verified_twse_tpex")
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
                status["symbols"][symbol] = {"status": "validated_in_memory", "bar_count": len(private[symbol]["bars"]), "venue": PILOT[symbol][2],
                                             **_date_diagnostics(private[symbol]["bars"], sessions)}
                status["validated_count"] += 1
            except HistoryBlocked as exc:
                status["symbols"][symbol] = {"status": "blocked", "reason": exc.reason, "bar_count": 0, "venue": PILOT[symbol][2], **exc.diagnostics}
        if status["validated_count"] != len(symbols):
            status["status"] = "partial_in_memory" if private else "blocked"
    except HistoryBlocked as exc:
        status.update(status="blocked", reason=exc.reason)
        private.clear()
        status["validated_count"] = 0
        status["symbols"] = {}
    status["observed_at"] = observed_clock().isoformat()
    return status, private


def _load_frozen_cohort_input(path=None):
    """Read the whole frozen report locally; request JSON cannot supply prices."""
    path = path or Path(__file__).resolve().parent / "reports" / "all_analysis.json"
    with Path(path).open("rb") as source:
        raw = source.read(MAX_FORMAL_BYTES + 1)
    if len(raw) > MAX_FORMAL_BYTES:
        raise ValueError("formal_input_byte_budget")
    formal = json.loads(raw)
    if not isinstance(formal, dict) or not isinstance(formal.get("data"), list):
        raise ValueError("formal_batch_invalid")
    manifest = []
    for row in formal["data"]:
        if not isinstance(row, dict):
            raise ValueError("formal_batch_invalid")
        if row.get("market") != "TW":
            continue
        symbol = row.get("symbol")
        if (not isinstance(symbol, str) or not re.fullmatch(r"[0-9][0-9A-Z]{2,7}\.(?:TW|TWO)", symbol)
                or row.get("type") not in {"個股", "ETF"}):
            raise ValueError("manifest_identity")
        # Suffix is not evidence of mainboard membership; nonpilot venues are
        # deliberately absent because no history is acquired for those rows.
        manifest.append({"symbol": symbol, "market": "TW", "type": row["type"]})
    members = {r["symbol"] for r in manifest}
    if not set(PILOT) <= members or len(manifest) > 256 or len(members) != len(manifest):
        raise ValueError("manifest_mismatch")
    return formal, manifest


def _remaining(deadline, cancelled):
    if (cancelled is not None and cancelled.is_set()) or clock.monotonic() >= deadline:
        raise HistoryBlocked("request_time_budget")
    return deadline - clock.monotonic()


def _official_pilot_records(formal, private, *, deadline, cancelled, get=None):
    """At most two trusted GETs; retain only pilot raw snapshots in memory.

    Streaming limits bound the consumed HTTP body. Socket timeouts do not cancel
    an in-flight read; the enclosing caller deadline still discards late work.
    """
    import requests
    from tw_daily_shadow_attestation import SOURCES, parse_official_price_rows

    records = {}
    audit = {"official_request_count": 0, "official_reused_record_count": 0, "official_reason_counts": {}}
    def reason(code):
        audit["official_reason_counts"][code] = audit["official_reason_counts"].get(code, 0) + 1
    needed = set(private).intersection(PILOT)
    for row in formal.get("data", []):
        if not isinstance(row, dict) or row.get("symbol") not in needed:
            continue
        symbol = row["symbol"]
        proof = row.get("shadow_daily_ohlcv_proof")
        if not isinstance(proof, dict):
            continue
        source = "TWSE OpenAPI" if symbol.endswith(".TW") else "TPEx OpenAPI"
        spec = SOURCES[source]
        if proof.get("source") != source or proof.get("source_url") != spec["url"]:
            continue
        parsed = parse_official_price_rows(source, [proof.get("raw_record")], fetched_at=proof.get("fetched_at"))
        record = parsed.get(symbol)
        if (record and record["source_session_date"] == private[symbol].get("source_session_date")
                and record["raw_record_sha256"] == proof.get("raw_record_sha256")):
            observed = datetime.fromisoformat(record["fetched_at"])
            settled = datetime.combine(date.fromisoformat(record["source_session_date"]), time(13, 30), TAIPEI)
            if not settled <= observed <= datetime.now(TAIPEI):
                continue
            records[symbol] = record
            audit["official_reused_record_count"] += 1
            reason("official_snapshot_reused")
    fetch = get or requests.get
    for source, spec in SOURCES.items():
        targets = {s for s in needed - set(records) if s.endswith(spec["suffix"])}
        if not targets:
            continue
        _remaining(deadline, cancelled)
        # The URL comes only from the existing official-source constants.
        audit["official_request_count"] += 1
        response = None
        body, payload = bytearray(), None
        recorded_before = sum(audit["official_reason_counts"].values())
        try:
            response = fetch(spec["url"], stream=True, allow_redirects=False,
                             timeout=min(5.0, _remaining(deadline, cancelled)),
                             headers={"Accept": "application/json", "User-Agent": "WudeAIStock/1.0 official-market-data"})
            if response.status_code != 200:
                reason("official_http_denied" if response.status_code in (401, 403) else
                       "official_rate_limited" if response.status_code == 429 else
                       "official_redirect_blocked" if 300 <= response.status_code < 400 else "official_http_error")
                continue
            advertised = response.headers.get("Content-Length")
            if advertised is not None and (not str(advertised).isdigit() or int(advertised) > MAX_OFFICIAL_BYTES):
                reason("official_body_budget")
                continue
            for chunk in response.iter_content(chunk_size=16 * 1024):
                _remaining(deadline, cancelled)
                if len(body) + len(chunk) > MAX_OFFICIAL_BYTES:
                    reason("official_body_budget")
                    raise ValueError("official_body_budget")
                body.extend(chunk)
            _remaining(deadline, cancelled)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                reason("official_parse_failure")
                continue
            finally:
                body.clear()
            if not isinstance(payload, list):
                reason("official_payload_invalid")
                continue
            if len(payload) > MAX_OFFICIAL_ROWS:
                reason("official_row_budget")
                continue
            # Keep duplicates so the existing parser rejects ambiguous identity.
            selected = [r for r in payload if isinstance(r, dict)
                        and str(r.get(spec["symbol"]) or "").strip() + spec["suffix"] in targets]
            payload.clear()
            parsed = parse_official_price_rows(source, selected, fetched_at=datetime.now(TAIPEI).isoformat())
            selected.clear()
            _remaining(deadline, cancelled)
            records.update({s: r for s, r in parsed.items() if s in targets})
            reason("official_record_unavailable" if not parsed else
                   "official_session_mismatch" if any(r["source_session_date"] != private[s].get("source_session_date") for s, r in parsed.items())
                   else "official_records_available")
        except HistoryBlocked:
            raise
        except Exception:
            # Never export provider content or exception messages, and never retry.
            if sum(audit["official_reason_counts"].values()) == recorded_before:
                reason("official_request_failure")
        finally:
            body.clear()
            if isinstance(payload, list):
                payload.clear()
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    return records, audit


class DailyPilot:
    """One in-flight request; caller timeout cannot cancel an SDK socket operation.

    An overdue worker retains the guard until it exits, discards its response,
    and starts no second symbol. No SDK auth/session/timeouts are mutated.
    """
    def __init__(self):
        self._guard = threading.Lock()

    def status(self, sdk: Any, symbols: Any, calendar: Any, *, timeout=MAX_SECONDS,
               include_cohort_diagnostics=False):
        symbols = validate_symbols(symbols)
        if include_cohort_diagnostics is True and set(symbols) != set(PILOT):
            raise ValueError("cohort diagnostics requires both fixed pilot symbols")
        now = datetime.now(TAIPEI)
        if sdk is None:
            return empty_status(symbols, now, "existing_session_unavailable")
        if not self._guard.acquire(blocking=False):
            return empty_status(symbols, now, "request_in_progress")
        done, cancelled = threading.Event(), threading.Event()
        deadline = clock.monotonic() + min(timeout, MAX_SECONDS)
        result = {}
        def worker():
            private = {}
            try:
                reststock = sdk.marketdata.rest_client.stock
                safe, private = collect_private(reststock, symbols, calendar,
                                                now=now, deadline=deadline, cancelled=cancelled)
                if include_cohort_diagnostics is True and safe.get("validated_count") == len(PILOT):
                    from tw_private_cohort_diagnostics import build_diagnostics
                    _remaining(deadline, cancelled)
                    try:
                        formal, manifest = _load_frozen_cohort_input()
                    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
                        formal, manifest = {}, []
                    official, audit = ({}, {"official_request_count": 0, "official_reused_record_count": 0, "official_reason_counts": {}})
                    if formal and manifest:
                        official, audit = _official_pilot_records(formal, private, deadline=deadline, cancelled=cancelled)
                    try:
                        safe["cohort_diagnostics"] = build_diagnostics(
                            reststock, private, calendar, formal=formal, manifest=manifest,
                            official_records=official, deadline=deadline, cancelled=cancelled,
                            now=datetime.now(TAIPEI),
                        )
                        safe["cohort_diagnostics"].update(audit, official_network_calls=audit["official_request_count"])
                    finally:
                        official.clear()
                if not cancelled.is_set():
                    result.update(safe)
            except HistoryBlocked as exc:
                if not cancelled.is_set():
                    blocked = empty_status(symbols, now, exc.reason)
                    if exc.reason == "request_time_budget":
                        blocked.update(request_count=None, request_count_known=False)
                    result.update(blocked)
            except Exception:
                if not cancelled.is_set():
                    result.update(empty_status(symbols, now, "provider_failure"))
            finally:
                private.clear()
                self._guard.release()
                done.set()
        threading.Thread(target=worker, daemon=True).start()
        if not done.wait(max(0, deadline - clock.monotonic())):
            cancelled.set()
            timed_out = empty_status(symbols, now, "request_time_budget")
            timed_out.update(request_count=None, request_count_known=False)
            return timed_out
        return result
