"""Existing trusted workflow diagnostic; print only validated operational fields.

No files, artifacts, raw values, arbitrary symbols, new credentials, or schedules.
"""
from datetime import date, datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fubon_daily_history import PILOT, REASONS, SOURCE, VERSION
from us_market_data import _relay_request


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
    if bool(clean["observed_date_count"]) != (clean["first_returned_date"] is not None and clean["last_returned_date"] is not None):
        raise ValueError("inconsistent bounds")
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
            if not 0 <= (last - first).days < 120:
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
            entry.update(sanitize_date_diagnostics(row))
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
    return clean


def run_probe(relay=_relay_request):
    # Use one bounded existing ownership batch to initialize/reuse the usual
    # service session. No new authentication path, retries or raw logging.
    # The full optional ownership collector still runs after durable receipts.
    symbols = list(PILOT)
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
    result = relay("tw_daily_history_status", {"symbols": symbols}, timeout=30)
    return sanitize_status(result)


def main():
    print("Fubon daily pilot:", json.dumps(run_probe(), sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    main()
