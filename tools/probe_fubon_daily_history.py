"""Existing trusted workflow diagnostic; print only validated operational fields.

No files, artifacts, raw values, arbitrary symbols, new credentials, or schedules.
"""
from datetime import date, datetime
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fubon_daily_history import OFFICIAL_REASONS, PILOT, REASONS, SOURCE, VERSION
from us_market_data import _relay_request


def sanitize_cohort_diagnostics(result):
    """Fixed counts/reasons/schema/date projection; never copy provider values."""
    from tw_private_cohort_diagnostics import VERSION as DIAGNOSTIC_VERSION, COVERAGE, METHODS, META_FIELDS, ROW_FIELDS, SAFE_REASONS
    if not isinstance(result, dict):
        raise ValueError("invalid cohort diagnostics")
    constants = {"version": DIAGNOSTIC_VERSION, "stage": "private_pilot_metadata_diagnostics", "coverage_status": COVERAGE}
    false_keys = ("corporate_action_coverage_verified", "scoring_executed", "ranking_executed", "eligibility_evaluated",
                  "full_cohort_readiness_evaluated", "market_values_exported", "raw_bars_exported", "raw_actions_exported",
                  "hashes_exported", "durable_raw_retention", "sdk_response_bound_enforced_before_allocation",
                  "sdk_socket_cancellation_supported")
    if any(result.get(k) != v for k, v in constants.items()) or any(result.get(k) is not False for k in false_keys):
        raise ValueError("unsafe cohort diagnostics")
    if result.get("status") not in {"held", "blocked", "metadata_diagnostics_complete_held"} or type(result.get("formal_v6_unchanged")) is not bool:
        raise ValueError("invalid diagnostic state")
    clean = {**constants, **{k: False for k in false_keys}, "status": result["status"], "formal_v6_unchanged": result["formal_v6_unchanged"]}
    def count(value, maximum):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("invalid diagnostic count")
        return value
    def day(value):
        if value is None:
            return None
        if type(value) is not str or date.fromisoformat(value).isoformat() != value:
            raise ValueError("invalid diagnostic date")
        return value
    for key, maximum in {
        "full_manifest_count": 256, "pilot_limit_count": 2, "acquired_history_count": 2,
        "unacquired_history_count": 256, "daily_features_rebuilt_count": 2, "source_feature_rebuilt_count": 2,
        "official_crosscheck_verified_count": 2, "official_match_count": 2, "official_mismatch_count": 2,
        "official_unavailable_count": 2, "calendar_session_count": 120, "corporate_action_request_count": 2,
        "official_network_calls": 2, "official_request_count": 2, "official_reused_record_count": 2,
    }.items():
        clean[key] = count(result.get(key), maximum)
    if (clean["pilot_limit_count"] != 2
            or clean["full_manifest_count"] != clean["acquired_history_count"] + clean["unacquired_history_count"]
            or clean["daily_features_rebuilt_count"] > clean["acquired_history_count"]
            or clean["source_feature_rebuilt_count"] != clean["daily_features_rebuilt_count"]
            or clean["official_match_count"] != clean["official_crosscheck_verified_count"]
            or sum(clean[k] for k in ("official_match_count", "official_mismatch_count", "official_unavailable_count")) > clean["daily_features_rebuilt_count"]
            or clean["official_network_calls"] != clean["official_request_count"]
            or clean["official_reused_record_count"] + clean["official_request_count"] > 2):
        raise ValueError("inconsistent diagnostic counts")
    clean["history_from"], clean["history_through"] = day(result.get("history_from")), day(result.get("history_through"))
    if (clean["history_from"] is None) != (clean["history_through"] is None):
        raise ValueError("incomplete history dates")
    if clean["history_from"] and not 0 <= (date.fromisoformat(clean["history_through"]) - date.fromisoformat(clean["history_from"])).days < 120:
        raise ValueError("invalid history interval")
    reasons = result.get("reason_counts")
    if not isinstance(reasons, dict) or any(k not in SAFE_REASONS for k in reasons):
        raise ValueError("invalid diagnostic reasons")
    clean["reason_counts"] = {key: count(value, 512) for key, value in reasons.items()}
    official_reasons = result.get("official_reason_counts")
    if not isinstance(official_reasons, dict) or any(k not in OFFICIAL_REASONS for k in official_reasons):
        raise ValueError("invalid official reasons")
    clean["official_reason_counts"] = {key: count(value, 2) for key, value in official_reasons.items()}
    endpoints = result.get("corporate_actions")
    if not isinstance(endpoints, dict) or set(endpoints) != set(METHODS):
        raise ValueError("invalid diagnostic endpoints")
    clean["corporate_actions"] = {}
    for method in METHODS:
        row = endpoints[method]
        statuses = {"not_attempted", "request_started", "response_inspected", "empty_response_inspected"} | SAFE_REASONS
        if not isinstance(row, dict) or row.get("status") not in statuses or row.get("coverage_status") != COVERAGE:
            raise ValueError("invalid diagnostic endpoint state")
        entry = {"status": row["status"], "coverage_status": COVERAGE}
        for key, values in {
            "range_echo_status": {"absent", "partial_match", "match", "conflict", "invalid"},
            "pagination_status": {"absent", "present_unknown", "continuation_indicated", "no_continuation_indicated", "conflicting_indicators", "invalid"},
            "total_count_status": {"absent", "matches_response_count", "exceeds_response_count", "below_response_count", "conflicting", "invalid"},
        }.items():
            if type(row.get(key)) is not str or row[key] not in values:
                raise ValueError("invalid schema state")
            entry[key] = row[key]
        if type(row.get("truncation_signal")) is not bool:
            raise ValueError("invalid truncation flag")
        entry["truncation_signal"] = row["truncation_signal"]
        stamps = {}
        for key in ("requested_at", "observed_at"):
            value = row.get(key)
            if value is None:
                entry[key] = None
                continue
            if type(value) is not str:
                raise ValueError("invalid endpoint timestamp")
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is None:
                raise ValueError("missing endpoint timezone")
            stamps[key] = stamp
            entry[key] = stamp.isoformat()
        if "observed_at" in stamps and ("requested_at" not in stamps or stamps["observed_at"] < stamps["requested_at"]):
            raise ValueError("invalid endpoint time order")
        elapsed = row.get("elapsed_ms")
        entry["elapsed_ms"] = None if elapsed is None else count(elapsed, 86400000)
        for key in ("response_count", "pilot_row_count", "unrelated_row_count", "malformed_row_count", "out_of_window_row_count",
                    "duplicate_row_count", "conflicting_event_count", "cash_event_count", "structural_event_count",
                    "unknown_event_count", "first_bar_event_count"):
            entry[key] = count(row.get(key), 5000)
        if any(entry[key] > entry["response_count"] for key in entry if key.endswith("_count")):
            raise ValueError("inconsistent endpoint count")
        entry["observed_date_from"], entry["observed_date_to"] = day(row.get("observed_date_from")), day(row.get("observed_date_to"))
        if ((entry["observed_date_from"] is None) != (entry["observed_date_to"] is None)
                or (entry["observed_date_from"] and entry["observed_date_from"] > entry["observed_date_to"])):
            raise ValueError("invalid observed bounds")
        metadata = row.get("metadata_fields_present")
        fields = row.get("row_field_presence_counts")
        if (not isinstance(metadata, list) or any(type(k) is not str or k not in META_FIELDS for k in metadata)
                or len(metadata) != len(set(metadata)) or not isinstance(fields, dict)
                or any(k not in ROW_FIELDS[method] for k in fields)):
            raise ValueError("invalid schema fields")
        entry["metadata_fields_present"] = list(metadata)
        entry["row_field_presence_counts"] = {key: count(value, entry["response_count"]) for key, value in fields.items()}
        clean["corporate_actions"][method] = entry
    return clean


def sanitize_date_diagnostics(row):
    keys = {"observed_date_count", "expected_session_count", "missing_session_dates",
            "unexpected_session_dates", "first_returned_date", "last_returned_date"}
    if not keys.intersection(row):
        return {}
    if not keys.issubset(row):
        raise ValueError("incomplete diagnostic metadata")
    clean = {}
    for key in ("observed_date_count", "expected_session_count"):
        n = row[key]
        if type(n) is not int or not 0 <= n <= 120:
            raise ValueError("invalid count")
        clean[key] = n
    for key in ("missing_session_dates", "unexpected_session_dates"):
        values = row[key]
        if (not isinstance(values, list) or len(values) > 120
                or any(not isinstance(v, str) or date.fromisoformat(v).isoformat() != v for v in values)
                or values != sorted(set(values))):
            raise ValueError("invalid dates")
        clean[key] = list(values)
    for key in ("first_returned_date", "last_returned_date"):
        value = row[key]
        if value is not None and (not isinstance(value, str) or date.fromisoformat(value).isoformat() != value):
            raise ValueError("invalid date")
        clean[key] = value
    if clean["observed_date_count"] == 0:
        if clean["first_returned_date"] is not None or clean["last_returned_date"] is not None:
            raise ValueError("inconsistent empty bounds")
    elif clean["first_returned_date"] is None or clean["last_returned_date"] is None:
        raise ValueError("inconsistent bounds")
    if set(clean["missing_session_dates"]) & set(clean["unexpected_session_dates"]):
        raise ValueError("overlapping dates")
    if len(clean["missing_session_dates"]) > clean["expected_session_count"] or len(clean["unexpected_session_dates"]) > clean["observed_date_count"]:
        raise ValueError("impossible counts")
    if clean["observed_date_count"] and clean["first_returned_date"] > clean["last_returned_date"]:
        raise ValueError("reversed bounds")
    if clean["observed_date_count"] != clean["expected_session_count"] - len(clean["missing_session_dates"]) + len(clean["unexpected_session_dates"]):
        raise ValueError("inconsistent counts")
    return clean


def sanitize_status(result):
    blocked = {"version": VERSION, "status": "blocked", "reason": "invalid_or_unavailable_relay_status"}
    if not isinstance(result, dict) or result.get("version") != VERSION or result.get("source") != SOURCE:
        return blocked
    if result.get("status") not in {"blocked", "validated_in_memory", "partial_in_memory"}:
        return blocked
    false_keys = ("decision_eligible", "affects_formal", "durable_raw_retention", "market_values_exported",
                  "corporate_action_coverage_verified", "official_latest_crosscheck_verified", "point_in_time_availability_proven")
    if any(result.get(k) is not False for k in false_keys):
        return blocked
    clean = {"version": VERSION, "source": SOURCE, "status": result["status"], **{k: False for k in false_keys}}
    for key, low, high in (("requested_count", 2, 2), ("validated_count", 0, 2)):
        value = result.get(key)
        if type(value) is not int or not low <= value <= high:
            return blocked
        clean[key] = value
    known = result.get("request_count_known")
    count = result.get("request_count")
    if known is True:
        if type(count) is not int or not 0 <= count <= 2:
            return blocked
    elif known is False:
        if count is not None or result.get("status") != "blocked" or result.get("reason") != "request_time_budget":
            return blocked
    else:
        return blocked
    clean.update(request_count=count, request_count_known=known)
    try:
        stamp = datetime.fromisoformat(result["observed_at"])
        if stamp.tzinfo is None:
            return blocked
        clean["observed_at"] = stamp.isoformat()
        if "session_date" in result:
            clean["session_date"] = date.fromisoformat(result["session_date"]).isoformat()
        if "calendar_session_count" in result:
            n = result["calendar_session_count"]
            if type(n) is not int or not 1 <= n <= 120:
                return blocked
            clean["calendar_session_count"] = n
    except (TypeError, ValueError, KeyError):
        return blocked
    if "request_from" in result:
        try:
            first, last = date.fromisoformat(result["request_from"]), date.fromisoformat(result["request_to"])
            if not 0 <= (last - first).days < 120 or last.isoformat() != clean.get("session_date"):
                return blocked
            constants = {"request_timeframe": "D", "request_adjusted": "false", "volume_unit": "shares",
                         "calendar_source_status": "verified_twse_tpex"}
            if any(result.get(k) != v for k, v in constants.items()):
                return blocked
            clean.update(request_from=first.isoformat(), request_to=last.isoformat(), **constants)
        except (TypeError, ValueError, KeyError):
            return blocked
    if "reason" in result:
        if not isinstance(result["reason"], str) or result["reason"] not in REASONS:
            return blocked
        clean["reason"] = result["reason"]
    entries = result.get("symbols")
    if not isinstance(entries, dict) or set(entries) - set(PILOT):
        return blocked
    clean["symbols"] = {}
    for symbol, row in entries.items():
        if not isinstance(row, dict) or row.get("venue") != PILOT[symbol][2]:
            return blocked
        if row.get("status") not in {"blocked", "validated_in_memory"}:
            return blocked
        n = row.get("bar_count")
        if type(n) is not int or not 0 <= n <= 120:
            return blocked
        entry = {"status": row["status"], "bar_count": n, "venue": row["venue"]}
        if row["status"] == "blocked":
            if not isinstance(row.get("reason"), str) or row["reason"] not in REASONS or n:
                return blocked
            entry["reason"] = row["reason"]
        elif n < 60:
            return blocked
        try:
            diagnostics = sanitize_date_diagnostics(row)
            if diagnostics:
                if diagnostics["expected_session_count"] != clean.get("calendar_session_count"):
                    return blocked
                if "request_from" in clean and any(not clean["request_from"] <= d <= clean["request_to"] for d in diagnostics["missing_session_dates"]):
                    return blocked
            if row["status"] == "validated_in_memory":
                if (not diagnostics or diagnostics["observed_date_count"] != n
                        or diagnostics["missing_session_dates"] or diagnostics["unexpected_session_dates"]
                        or diagnostics["first_returned_date"] != clean.get("request_from")
                        or diagnostics["last_returned_date"] != clean.get("request_to")):
                    return blocked
            entry.update(diagnostics)
        except (TypeError, ValueError):
            return blocked
        clean["symbols"][symbol] = entry
    if clean["status"] != "blocked":
        if set(entries) != set(PILOT) or not all(k in clean for k in ("session_date", "calendar_session_count")):
            return blocked
        if clean["validated_count"] != sum(r["status"] == "validated_in_memory" for r in entries.values()):
            return blocked
        if clean["status"] == "validated_in_memory" and clean["validated_count"] != 2:
            return blocked
    if "cohort_diagnostics" in result:
        try:
            clean["cohort_diagnostics"] = sanitize_cohort_diagnostics(result["cohort_diagnostics"])
        except (TypeError, ValueError, KeyError):
            return blocked
    return clean


def run_probe(relay=_relay_request, *, include_cohort_diagnostics=False):
    # Stage B is existing-session only, including the workflow entry point.
    # The legacy default probe alone retains its exact no-session warmup path.
    # Provider failures, timeouts and malformed responses never authorize retry.
    symbols = list(PILOT)
    request = {"symbols": symbols}
    if include_cohort_diagnostics is True:
        request["include_cohort_diagnostics"] = True
    result = relay("tw_daily_history_status", request, timeout=30)
    clean = sanitize_status(result)
    no_session = (
        clean.get("status") == "blocked"
        and clean.get("reason") == "existing_session_unavailable"
        and clean.get("request_count_known") is True
        and clean.get("request_count") == 0
        and clean.get("validated_count") == 0
        and clean.get("symbols") == {}
        and isinstance(result, dict)
        and set(result) == set(clean)
        and not any(k in clean for k in ("session_date", "calendar_session_count", "request_from", "request_to"))
    )
    if not no_session or include_cohort_diagnostics is True:
        return clean
    warmed = relay("ownership", {"symbols": symbols}, timeout=20)
    denied = False
    valid = isinstance(warmed, dict) and set(warmed) == set(symbols)
    if valid:
        for symbol in symbols:
            entries = warmed[symbol]
            if not isinstance(entries, dict) or set(entries) != {"institutional_trades", "tdcc_distribution", "director_holdings"}:
                valid = False
                break
            for entry in entries.values():
                if not isinstance(entry, dict):
                    valid = False
                    break
                state, code = entry.get("status"), entry.get("error_code")
                if not isinstance(state, str) or state not in {"available", "no_data", "not_applicable"}:
                    valid = False
                    break
                if state in {"rate_limited", "relay_unavailable"} or (type(code) is int and code in {401, 403, 429}):
                    denied = True
    if not valid or denied:
        return {"version": VERSION, "status": "blocked", "reason": "existing_session_warmup_unavailable"}
    result = relay("tw_daily_history_status", request, timeout=30)
    return sanitize_status(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-cohort-diagnostics", action="store_true")
    args = parser.parse_args()
    print("Fubon daily pilot:", json.dumps(run_probe(include_cohort_diagnostics=args.include_cohort_diagnostics), sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    main()

