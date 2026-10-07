"""Conservative clock gate for existing official US session evidence.

This does not fetch prices, infer trading days, or certify price coverage.
The callers retain their frozen-pick and exact-session completeness checks.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


def completed_us_session(rows, updated_at: str) -> bool:
    dates = {
        str(row.get("official_session_date") or "")
        for row in rows
        if str(row.get("market") or "").upper() == "US"
        and "ETF" not in str(row.get("type") or "").upper()
        and row.get("official_session_date")
    }
    if len(dates) != 1:
        return False
    try:
        session = date.fromisoformat(next(iter(dates)))
        stamp = datetime.fromisoformat(updated_at)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo("Asia/Taipei"))
        closed = datetime.combine(session, time(16), ZoneInfo("America/New_York"))
    except (ValueError, TypeError):
        return False
    # 16:00 is intentionally conservative on early-close days too. Official
    # session evidence is required; no weekday/holiday calendar is invented.
    return stamp >= closed
