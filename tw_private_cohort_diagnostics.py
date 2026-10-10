"""Bounded, metadata-only diagnostics for the fixed two-symbol TW pilot.

Run only inside the existing guarded Fubon worker. This module never logs in,
fetches official prices, reads files, scores, ranks, persists, or schedules work.
The two corporate-action APIs are market-wide date-range reads. Their published
contract does not establish historical completeness or pagination semantics;
even an empty successful response leaves coverage unverified.

Limits apply AFTER the SDK returns. The SDK may allocate a larger response
before validation, and a deadline/cancellation flag cannot cancel its socket.
Only pilot event classifications survive inspection; provider responses and
private feature values never cross the returned diagnostics boundary.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
import re
import time as clock
from typing import Any
from zoneinfo import ZoneInfo

import tw_private_cohort as cohort

VERSION = "TW-FUBON-PRIVATE-DIAGNOSTICS-V1"
MAX_MANIFEST_COUNT = cohort.MAX_SYMBOLS
PILOT = {"2330.TW": ("2330", "TWSE", "TWSE"),
         "6290.TWO": ("6290", "TPEx", "TPEX_MAINBOARD")}
METHODS = ("dividends", "capital_changes")
COVERAGE = "provider_coverage_unverified"
MAX_ROWS = 5000
MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 8
MAX_STRING = 4096
MAX_NODES = 100000
MAX_KEYS = 64
TAIPEI = ZoneInfo("Asia/Taipei")
META_FIELDS = ("start_date", "end_date", "sort", "count", "total", "totalCount",
               "page", "pageSize", "limit", "offset", "hasMore", "next", "nextPage",
               "nextCursor", "cursor")
ROW_FIELDS = {"dividends": ("date", "exchange", "symbol", "dividendType", "cashDividend", "stockDividendShares", "dividend"),
              "capital_changes": ("resumeDate", "exchange", "symbol", "actionType", "raw", "raw.splitType")}
ERROR_CODES = {401: "provider_authentication_denied", 403: "provider_entitlement_denied",
               429: "provider_rate_limited"}
SAFE_REASONS = cohort.REASONS | {
    "invalid_input", "full_manifest_required", "manifest_mismatch", "manifest_identity",
    "formal_batch_timestamp", "formal_source_session_mismatch", "formal_input_mutated",
    "calendar_contract", "history_universe_mismatch", "request_cancelled", "request_time_budget",
    "sdk_method_unavailable", "provider_authentication_denied", "provider_entitlement_denied",
    "provider_rate_limited", "provider_timeout", "provider_failure", "invalid_provider_payload",
    "response_row_budget", "response_byte_budget", "response_shape_budget", "response_string_budget",
}


class DiagnosticBlocked(ValueError):
    """The message is always a locally defined, non-provider reason code."""


def _blocked(reason: str):
    raise DiagnosticBlocked(reason)


def _date(value: Any) -> str | None:
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _number(value: Any, *, positive=False) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def _check_deadline(deadline: float, cancelled: Any) -> None:
    if cancelled is not None and cancelled.is_set():
        _blocked("request_cancelled")
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or clock.monotonic() >= deadline:
        _blocked("request_time_budget")


def _bounded_digest(payload: Any) -> str:
    """Validate JSON primitives first, then stream bounded serialization privately."""
    ancestors: set[int] = set()
    nodes = 0

    def walk(value, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            _blocked("response_shape_budget")
        kind = type(value)
        if kind is str:
            if len(value) > MAX_STRING:
                _blocked("response_string_budget")
        elif value is None or kind is bool:
            pass
        elif kind is int:
            if value.bit_length() > 64:
                _blocked("invalid_provider_payload")
        elif kind is float:
            if not math.isfinite(value):
                _blocked("invalid_provider_payload")
        elif kind in (dict, list):
            if id(value) in ancestors:
                _blocked("invalid_provider_payload")
            if (kind is dict and len(value) > MAX_KEYS) or (kind is list and len(value) > MAX_ROWS):
                _blocked("response_shape_budget")
            ancestors.add(id(value))
            if kind is dict:
                for key, child in value.items():
                    if type(key) is not str or len(key) > 128:
                        _blocked("invalid_provider_payload")
                    walk(key, depth + 1)
                    walk(child, depth + 1)
            else:
                for child in value:
                    walk(child, depth + 1)
            ancestors.remove(id(value))
        else:
            _blocked("invalid_provider_payload")

    walk(payload, 0)
    digest = hashlib.sha256()
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(payload):
        encoded = chunk.encode("utf-8")
        size += len(encoded)
        if size > MAX_BYTES:
            _blocked("response_byte_budget")
        digest.update(encoded)
    return digest.hexdigest()


def _empty_endpoint() -> dict:
    return {"status": "not_attempted", "coverage_status": COVERAGE, "response_count": 0,
            "pilot_row_count": 0, "unrelated_row_count": 0, "malformed_row_count": 0,
            "out_of_window_row_count": 0, "duplicate_row_count": 0, "conflicting_event_count": 0,
            "cash_event_count": 0, "structural_event_count": 0, "unknown_event_count": 0,
            "first_bar_event_count": 0, "observed_date_from": None, "observed_date_to": None,
            "metadata_fields_present": [], "row_field_presence_counts": {},
            "range_echo_status": "absent", "pagination_status": "absent",
            "total_count_status": "absent", "truncation_signal": False,
            "requested_at": None, "observed_at": None, "elapsed_ms": None}


def _pilot_identity(raw: dict) -> tuple[str | None, bool]:
    sid = raw.get("symbol")
    # Recognize a malformed numeric pilot ID as a target, but never validate it.
    for symbol, (code, exchange, _) in PILOT.items():
        if sid == code or (type(sid) is int and sid == int(code)):
            return symbol, type(sid) is str and raw.get("exchange") == exchange
    return None, False


def _classify(raw: dict, method: str, identity_valid: bool, effective: str | None,
              first: str, last: str) -> str:
    if not identity_valid or effective is None or not first <= effective <= last:
        return "unknown"
    if method == "dividends":
        kind, cash, shares = raw.get("dividendType"), raw.get("cashDividend"), raw.get("stockDividendShares")
        if kind in ("權", "權息"):
            return "structural"
        rights_valid = all(key not in raw or (type(raw[key]) in (int, float) and raw[key] == 0)
                           for key in ("rightsSubscriptionShares", "rightsSubscriptionRatio"))
        # Optional documented total rights-plus-cash amount must agree with an
        # asserted cash-only distribution; do not invent adjustment arithmetic.
        total_valid = ("dividend" not in raw or
                       (_number(raw["dividend"]) and cohort._same_number(raw["dividend"], cash)))
        if (kind == "息" and _number(cash, positive=True) and type(shares) in (int, float)
                and shares == 0 and rights_valid and total_valid):
            return "cash"
        return "unknown"
    action = raw.get("actionType")
    if action in ("capital_reduction", "par_value_change", "etf_split_or_merge"):
        # The documented action family itself proves a structural hold. Optional
        # amounts/subtype do not turn a positive structural event into cash-only.
        return "structural"
    return "unknown"


def _response_metadata(payload: dict, count: int, first: str, last: str) -> dict:
    echoed = [(key, expected) for key, expected in (("start_date", first), ("end_date", last)) if key in payload]
    if not echoed:
        range_status = "absent"
    elif any(not _date(payload[key]) for key, _ in echoed):
        range_status = "invalid"
    elif any(payload[key] != expected for key, expected in echoed):
        range_status = "conflict"
    else:
        range_status = "match" if len(echoed) == 2 else "partial_match"
    totals = [payload[key] for key in ("count", "total", "totalCount") if key in payload]
    if not totals:
        total_status = "absent"
    elif any(type(value) is not int or value < 0 for value in totals):
        total_status = "invalid"
    elif len(set(totals)) != 1:
        total_status = "conflicting"
    else:
        total_status = ("matches_response_count" if totals[0] == count else
                        "exceeds_response_count" if totals[0] > count else "below_response_count")
    paging = [key for key in ("page", "pageSize", "limit", "offset", "hasMore", "next", "nextPage", "nextCursor", "cursor") if key in payload]
    indications, invalid = [], False
    if "hasMore" in payload:
        if type(payload["hasMore"]) is bool:
            indications.append(payload["hasMore"])
        else:
            invalid = True
    for key in ("next", "nextPage", "nextCursor"):
        if key not in payload:
            continue
        value = payload[key]
        if value is None or value == "":
            indications.append(False)
        elif type(value) is str:
            indications.append(True)
        elif key == "nextPage" and type(value) is int and value >= 0:
            indications.append(value > 0)
        else:
            invalid = True
    if invalid:
        pagination = "invalid"
    elif True in indications and False in indications:
        pagination = "conflicting_indicators"
    elif True in indications:
        pagination = "continuation_indicated"
    elif False in indications:
        pagination = "no_continuation_indicated"
    else:
        pagination = "present_unknown" if paging else "absent"
    return {"range_echo_status": range_status, "pagination_status": pagination,
            "total_count_status": total_status,
            "truncation_signal": True in indications or any(type(value) is int and value > count for value in totals)}


def inspect_action_response(payload: Any, *, method: str, first: str, last: str) -> tuple[dict, list[dict]]:
    """Return safe counts plus PRIVATE pilot classifications; no provider rows.

    The private second return value contains only a pilot identity, normalized
    date, and a fixed classification. It is never part of public diagnostics.
    Conflicting same-pilot/same-date records are held as unknown. Hashes are
    ephemeral duplicate-check aids and are discarded before returning.
    """
    if method not in METHODS or not _date(first) or not _date(last) or first > last:
        _blocked("invalid_input")
    if type(payload) is not dict:
        _blocked("invalid_provider_payload")
    if type(payload.get("data")) is list and len(payload["data"]) > MAX_ROWS:
        _blocked("response_row_budget")
    _bounded_digest(payload)  # Checks the complete market-wide response transiently.
    for field in ("status_code", "statusCode", "code"):
        code = payload.get(field)
        if type(code) is int and code >= 400:
            _blocked(ERROR_CODES.get(code, "provider_failure"))
    if type(payload.get("data")) is not list:
        _blocked("invalid_provider_payload")
    result = _empty_endpoint()
    result.update(status="response_inspected" if payload["data"] else "empty_response_inspected",
                  response_count=len(payload["data"]),
                  metadata_fields_present=[field for field in META_FIELDS if field in payload])
    result.update(_response_metadata(payload, len(payload["data"]), first, last))
    counts = Counter()
    dates = []
    seen: dict[tuple, tuple[set[str], dict]] = {}
    for raw in payload["data"]:
        if type(raw) is not dict:
            result["malformed_row_count"] += 1
            continue
        for field in ROW_FIELDS[method]:
            if field == "raw.splitType":
                present = type(raw.get("raw")) is dict and "splitType" in raw["raw"]
            else:
                present = field in raw
            counts[field] += int(present)
        effective = _date(raw.get("date" if method == "dividends" else "resumeDate"))
        if effective:
            dates.append(effective)
            result["out_of_window_row_count"] += int(not first <= effective <= last)
        symbol, identity_valid = _pilot_identity(raw)
        if symbol is None:
            if type(raw.get("symbol")) is str and re.fullmatch(r"[0-9][0-9A-Z]{2,7}", raw["symbol"]):
                result["unrelated_row_count"] += 1
            else:
                result["malformed_row_count"] += 1
            continue
        result["pilot_row_count"] += 1
        classification = _classify(raw, method, identity_valid, effective, first, last)
        result["malformed_row_count"] += int(not identity_valid or effective is None)
        event = {"symbol": symbol, "effective_date": effective, "classification": classification}
        fingerprint = _bounded_digest(raw)
        # Malformed dates cannot be used as an identity key for deduplication.
        key = (symbol, effective) if effective else (symbol, None, result["pilot_row_count"])
        if key in seen:
            fingerprints, prior = seen[key]
            if fingerprint in fingerprints:
                result["duplicate_row_count"] += 1
            else:
                fingerprints.add(fingerprint)
                if not prior.get("conflicting"):
                    result["conflicting_event_count"] += 1
                prior.update(classification="unknown", conflicting=True)
            continue
        seen[key] = ({fingerprint}, event)
    private = []
    for _, event in seen.values():
        event.pop("conflicting", None)
        private.append(event)
        result[event["classification"] + "_event_count"] += 1
        result["first_bar_event_count"] += int(event["effective_date"] == first)
    result["row_field_presence_counts"] = {field: counts[field] for field in ROW_FIELDS[method]}
    result["observed_date_from"] = min(dates) if dates else None
    result["observed_date_to"] = max(dates) if dates else None
    return result, private


def _calendar(calendar: Any, now: datetime) -> dict:
    if type(calendar) is dict:
        evidence = calendar
    else:
        # Reuse the history collector's verified exchange-calendar contract.
        from fubon_daily_history import HistoryBlocked, _sessions
        try:
            sessions = _sessions(calendar, now)
        except HistoryBlocked:
            _blocked("calendar_contract")
        evidence = {"available": True, "status": "verified", "source_statuses": ["verified_twse_tpex"],
                    "sessions": sessions}
    sessions = evidence.get("sessions")
    if (evidence.get("available") is not True or evidence.get("status") != "verified"
            or evidence.get("source_statuses") != ["verified_twse_tpex"]
            or type(sessions) is not list or not 60 <= len(sessions) <= 120
            or any(not _date(day) for day in sessions) or sessions != sorted(set(sessions))):
        _blocked("calendar_contract")
    if ((date.fromisoformat(sessions[-1]) - date.fromisoformat(sessions[0])).days >= 120
            or datetime.combine(date.fromisoformat(sessions[-1]), time(16, 30), TAIPEI) > now):
        _blocked("calendar_contract")
    return evidence


def _manifest(formal: Any, manifest: Any, sessions: list[str], now: datetime) -> str:
    if type(formal) is not dict or type(formal.get("data")) is not list or type(manifest) is not list:
        _blocked("invalid_input")
    frozen = [row for row in formal["data"] if type(row) is dict and row.get("market") == "TW"]
    if not 2 <= len(frozen) <= MAX_MANIFEST_COUNT or len(manifest) != len(frozen):
        _blocked("full_manifest_required")
    def members(rows):
        result = {}
        for row in rows:
            if (type(row) is not dict or row.get("market") != "TW"
                    or type(row.get("symbol")) is not str
                    or not re.fullmatch(r"[0-9][0-9A-Z]{2,7}\.(?:TW|TWO)", row["symbol"])
                    or row.get("type") not in ("個股", "ETF") or row["symbol"] in result):
                _blocked("manifest_identity")
            result[row["symbol"]] = row["type"]
        return result
    supplied, expected = members(manifest), members(frozen)
    if supplied != expected or not set(PILOT) <= set(expected):
        _blocked("manifest_mismatch")
    try:
        stamp = datetime.fromisoformat(formal.get("updated_at"))
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=TAIPEI)
        settled = datetime.combine(date.fromisoformat(sessions[-1]), time(13, 30), TAIPEI)
        if not settled <= stamp <= now:
            _blocked("formal_batch_timestamp")
        if any(row.get("official_session_date") != sessions[-1] for row in frozen):
            _blocked("formal_source_session_mismatch")
        return cohort._digest(formal)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, DiagnosticBlocked):
            raise
        _blocked("formal_batch_timestamp")


def _status() -> dict:
    return {"version": VERSION, "stage": "private_pilot_metadata_diagnostics", "status": "held",
            "full_manifest_count": 0, "pilot_limit_count": len(PILOT), "acquired_history_count": 0,
            "unacquired_history_count": 0, "daily_features_rebuilt_count": 0, "source_feature_rebuilt_count": 0,
            "official_crosscheck_verified_count": 0, "corporate_action_coverage_verified": False,
            "official_match_count": 0, "official_mismatch_count": 0, "official_unavailable_count": 0,
            "calendar_session_count": 0, "history_from": None, "history_through": None,
            "corporate_action_request_count": 0, "coverage_status": COVERAGE,
            "corporate_actions": {method: _empty_endpoint() for method in METHODS},
            "reason_counts": {}, "formal_v6_unchanged": False,
            "scoring_executed": False, "ranking_executed": False, "eligibility_evaluated": False,
            "full_cohort_readiness_evaluated": False, "official_network_calls": 0,
            "market_values_exported": False, "raw_bars_exported": False, "raw_actions_exported": False,
            "hashes_exported": False, "durable_raw_retention": False,
            "sdk_response_bound_enforced_before_allocation": False, "sdk_socket_cancellation_supported": False}


def build_diagnostics(reststock: Any, private_by_symbol: dict, calendar: Any, *, formal: dict,
                      manifest: list[dict], official_records: dict | None = None,
                      deadline: float, cancelled: Any, now: datetime | None = None) -> dict:
    """Inspect the fixed pilot inside an existing guarded worker; return safe counts.

    ``formal`` is the full frozen report and ``manifest`` its complete TW
    symbol/type entries (192 in the current batch). Nonpilot venues are never
    inferred. Only the history producer's two known pilot venues are used.
    ``official_records``
    must be injected verified raw envelopes; Stage A reparses their raw OHLCV.
    There is no fallback official network read. Failed/late calls are not retried.
    """
    result, reasons, events = _status(), Counter(), []
    now = now or datetime.now(timezone.utc)
    before = None
    try:
        _check_deadline(deadline, cancelled)
        if not isinstance(now, datetime) or now.tzinfo is None:
            _blocked("invalid_input")
        evidence = _calendar(calendar, now)
        _check_deadline(deadline, cancelled)
        sessions = evidence["sessions"]
        before = _manifest(formal, manifest, sessions, now)
        if type(private_by_symbol) is not dict or set(private_by_symbol) - set(PILOT):
            _blocked("history_universe_mismatch")
        result.update(full_manifest_count=len(manifest), acquired_history_count=len(private_by_symbol),
                      unacquired_history_count=len(manifest) - len(private_by_symbol),
                      calendar_session_count=len(sessions), history_from=sessions[0], history_through=sessions[-1])
        official = official_records if type(official_records) is dict else {}
        types = {item["symbol"]: item["type"] for item in manifest}
        for symbol, (_, _, venue) in PILOT.items():
            _check_deadline(deadline, cancelled)
            item = {"symbol": symbol, "market": "TW", "venue": venue, "type": types[symbol]}
            try:
                bars = cohort._history(private_by_symbol.get(symbol), item, sessions, now)
            except cohort.InvalidCohort as exc:
                reason = str(exc)
                reasons[reason if reason in SAFE_REASONS else "invalid_input"] += 1
                continue
            # Reuse precisely Stage A's measured daily primitives, then discard.
            features = cohort.daily_features(bars)
            result["daily_features_rebuilt_count"] += 1
            result["source_feature_rebuilt_count"] += 1
            if symbol in official:
                _bounded_digest(official[symbol])
            check = cohort._crosscheck(official.get(symbol), item, bars, now)
            if check == "official_crosscheck_mismatch":
                result["official_mismatch_count"] += 1
            elif check:
                result["official_unavailable_count"] += 1
            else:
                result["official_match_count"] += 1
                result["official_crosscheck_verified_count"] += 1
            if check:
                reasons[check] += 1
            del features, bars
        _check_deadline(deadline, cancelled)
        ca = getattr(reststock, "corporate_actions", None)
        for method in METHODS:
            _check_deadline(deadline, cancelled)
            endpoint = getattr(ca, method, None)
            if not callable(endpoint):
                result["corporate_actions"][method]["status"] = "sdk_method_unavailable"
                _blocked("sdk_method_unavailable")
            result["corporate_action_request_count"] += 1
            result["corporate_actions"][method]["status"] = "request_started"
            started = clock.monotonic()
            result["corporate_actions"][method]["requested_at"] = datetime.now(timezone.utc).isoformat()
            def observed():
                status = result["corporate_actions"][method]
                status["observed_at"] = datetime.now(timezone.utc).isoformat()
                status["elapsed_ms"] = min(86400000, max(0, int((clock.monotonic() - started) * 1000)))
            try:
                payload = endpoint(start_date=sessions[0], end_date=sessions[-1])
            except Exception as exc:
                observed()
                _check_deadline(deadline, cancelled)
                code = getattr(exc, "status_code", None)
                reason = "provider_timeout" if isinstance(exc, TimeoutError) else "provider_failure"
                if type(code) is int:
                    reason = ERROR_CODES.get(code, reason)
                result["corporate_actions"][method]["status"] = reason
                _blocked(reason)
            try:
                observed()
                _check_deadline(deadline, cancelled)
                safe, retained = inspect_action_response(payload, method=method, first=sessions[0], last=sessions[-1])
                _check_deadline(deadline, cancelled)
                safe.update({key: result["corporate_actions"][method][key]
                             for key in ("requested_at", "observed_at", "elapsed_ms")})
                result["corporate_actions"][method] = safe
                events.extend(retained)
            except DiagnosticBlocked as exc:
                result["corporate_actions"][method]["status"] = str(exc)
                raise
            finally:
                payload = None
        reasons["action_coverage_unknown"] += result["daily_features_rebuilt_count"]
        result["status"] = "metadata_diagnostics_complete_held"
    except DiagnosticBlocked as exc:
        reason = str(exc)
        reasons[reason if reason in SAFE_REASONS else "invalid_input"] += 1
        result["status"] = "blocked"
        for endpoint_status in result["corporate_actions"].values():
            if endpoint_status["status"] == "request_started":
                endpoint_status["status"] = reason if reason in SAFE_REASONS else "invalid_input"
    except Exception:
        # Never echo provider exceptions, object reprs, or raw input strings.
        reasons["invalid_input"] += 1
        result["status"] = "blocked"
        for endpoint_status in result["corporate_actions"].values():
            if endpoint_status["status"] == "request_started":
                endpoint_status["status"] = "invalid_input"
    finally:
        events.clear()
        if before is not None:
            try:
                result["formal_v6_unchanged"] = before == cohort._digest(formal)
            except Exception:
                result["formal_v6_unchanged"] = False
            if not result["formal_v6_unchanged"]:
                reasons["formal_input_mutated"] += 1
                result["status"] = "blocked"
        result["reason_counts"] = dict(sorted(reasons.items()))
    return result
