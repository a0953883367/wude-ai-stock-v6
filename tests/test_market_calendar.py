import json
from datetime import datetime, timezone
from pathlib import Path

from market_calendar import (
    OfficialMarketCalendar,
    parse_alpaca_calendar,
    parse_tpex_holidays,
    parse_twse_holidays,
)


def test_twse_parser_reads_official_closed_dates():
    payload = {
        "stat": "ok",
        "data": [
            ["2026-01-01", "New Year"],
            ["2026-02-12", "No Trading"],
            ["2025-01-01", "Other year"],
        ],
    }
    assert parse_twse_holidays(payload, 2026) == {"2026-01-01", "2026-02-12"}


def test_tpex_parser_excludes_last_trading_day_but_keeps_market_holidays():
    payload = {"data": {"html": """
      <table><tr><th>Month</th><th>Date</th><th>Description</th></tr>
      <tr><td>February</td><td>11 (Wednesday)</td><td>Last Trading Day before Lunar New Year Holiday</td></tr>
      <tr><td>12 (Thursday)</td><td rowspan="2">Last Clearing &amp; Settlement Days</td></tr>
      <tr><td>13 (Friday)</td></tr>
      <tr><td>16 (Monday)</td><td>Lunar New Year's Eve</td></tr></table>
    """}}
    assert parse_tpex_holidays(payload, 2026) == {"2026-02-12", "2026-02-13", "2026-02-16"}


def test_alpaca_parser_preserves_early_close():
    sessions, details = parse_alpaca_calendar([
        {"date": "2026-11-27", "open": "09:30", "close": "13:00"},
        {"date": "2026-11-30", "open": "09:30", "close": "16:00"},
    ], 2026)
    assert sessions == ["2026-11-27", "2026-11-30"]
    assert details["2026-11-27"]["early_close"] is True
    assert details["2026-11-30"]["early_close"] is False


def test_lookup_uses_verified_cache_and_never_guesses(tmp_path: Path):
    state = tmp_path / "calendar.json"
    state.write_text(json.dumps({
        "version": 1,
        "markets": {
            "TW": {"years": {"2026": {
                "status": "verified_twse_tpex",
                "sources": ["TWSE", "TPEx"],
                "sessions": ["2026-08-28", "2026-09-01"],
                "session_details": {},
                "fetched_at": "2026-08-29T00:00:00+00:00",
                "fetched_at_epoch": 1787961600,
            }}},
            "US": {"years": {}},
        },
    }), encoding="utf-8")
    clock = lambda: datetime(2026, 8, 29, tzinfo=timezone.utc).timestamp()
    calendar = OfficialMarketCalendar(state, clock=clock, auto_refresh=False)
    lookup = calendar.lookup("TW", "2026-08-28", "2026-09-01")
    assert lookup["available"] is True
    assert lookup["sessions"] == ["2026-09-01"]
    missing = calendar.lookup("US", "2026-08-28", "2026-09-01")
    assert missing["available"] is False
    assert missing["sessions"] == []


def test_us_early_close_controls_completion_time(tmp_path: Path):
    state = tmp_path / "calendar.json"
    state.write_text(json.dumps({
        "version": 1,
        "markets": {
            "TW": {"years": {}},
            "US": {"years": {"2026": {
                "status": "verified_alpaca",
                "sources": ["Alpaca Market Calendar"],
                "sessions": ["2026-11-27"],
                "session_details": {"2026-11-27": {"open": "09:30", "close": "13:00", "early_close": True}},
                "fetched_at": "2026-08-29T00:00:00+00:00",
                "fetched_at_epoch": 1787961600,
            }}},
        },
    }), encoding="utf-8")
    calendar = OfficialMarketCalendar(state, auto_refresh=False)
    before = datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 11, 27, 18, 1, tzinfo=timezone.utc).timestamp()
    assert calendar.session_complete("US", "2026-11-27", at_epoch=before) is False
    assert calendar.session_complete("US", "2026-11-27", at_epoch=after) is True


def test_session_status_reports_holidays_and_verified_early_close(tmp_path: Path):
    state = tmp_path / "calendar.json"
    state.write_text(json.dumps({
        "version": 1,
        "markets": {
            "TW": {"years": {}},
            "US": {"years": {"2026": {
                "status": "verified_alpaca",
                "sources": ["Alpaca Market Calendar"],
                "sessions": ["2026-11-27"],
                "session_details": {
                    "2026-11-27": {"open": "09:30", "close": "13:00", "early_close": True}
                },
            }}},
        },
    }), encoding="utf-8")
    calendar = OfficialMarketCalendar(state, auto_refresh=False)

    session = calendar.session_status("US", "2026-11-27")
    assert session["available"] is True
    assert session["is_session"] is True
    assert session["close"] == "13:00"
    assert session["early_close"] is True
    holiday = calendar.session_status("US", "2026-11-26")
    assert holiday["available"] is True
    assert holiday["is_session"] is False
