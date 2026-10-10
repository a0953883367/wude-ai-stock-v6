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
        clean["symbols"][symbol] = entry
    if clean["status"] != "blocked":
        if set(entries) != set(PILOT) or not all(k in clean for k in ("session_date", "calendar_session_count")):
            return blocked
        if clean["validated_count"] != sum(r["status"] == "validated_in_memory" for r in entries.values()):
            return blocked
        if clean["status"] == "validated_in_memory" and clean["validated_count"] != 2:
            return blocked
    return clean


def main():
    result = _relay_request("tw_daily_history_status", {"symbols": list(PILOT)}, timeout=30)
    print("Fubon daily pilot:", json.dumps(sanitize_status(result), sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    main()
