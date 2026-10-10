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


def test_reviewed_july_10_closure_migrates_existing_cache_without_inventing_sessions(tmp_path):
    from market_calendar import TW_EXCEPTIONAL_CLOSURES
    sessions = ["2026-07-09", "2026-07-10", "2026-07-13"]
    original = {"version": 1, "markets": {
        "TW": {"years": {"2026": {"year": 2026, "status": "verified_twse_tpex", "sources": ["TWSE", "TPEx"],
                                   "sessions": sessions, "session_details": {"2026-07-10": {"open": "09:00"}},
                                   "fetched_at": "2026-07-01T00:00:00+00:00", "fetched_at_epoch": 1782864000}}},
        "US": {"years": {"2026": {"year":2026,"status":"verified_alpaca","sessions":sessions,
                                    "session_details":{"2026-07-10":{"open":"09:30","close":"16:00"}}}}}}}
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(original))
    calendar = OfficialMarketCalendar(path, auto_refresh=False, allow_network=False)
    lookup = calendar.lookup("TW", "2026-07-08", "2026-07-13")
    assert lookup["sessions"] == ["2026-07-09", "2026-07-13"]
    assert lookup["source_statuses"] == ["verified_twse_tpex"]
    assert lookup["exceptional_closures"] == list(TW_EXCEPTIONAL_CLOSURES)
    assert calendar.session_status("TW", "2026-07-10")["is_session"] is False
    assert calendar.session_complete("TW", "2026-07-10", at_epoch=9999999999) is False
    assert calendar.session_status("TW", "2026-07-13")["is_session"] is True
    assert calendar.lookup("US", "2026-07-08", "2026-07-13")["sessions"] == sessions
    assert json.loads(path.read_text()) == original  # read-only load migration stays in memory
    migrated = calendar._state["markets"]["TW"]["years"]["2026"]
    assert migrated["fetched_at"] == original["markets"]["TW"]["years"]["2026"]["fetched_at"]
    calendar._save()
    reloaded = OfficialMarketCalendar(path, auto_refresh=False, allow_network=False)
    assert reloaded.lookup("TW", "2026-07-08", "2026-07-13") == lookup


def test_new_calendar_fetch_applies_official_exception_and_preserves_annual_provenance(tmp_path):
    def fetch(url, headers):
        if "twse" in url:
            return {"stat":"ok", "data":[["2026-01-01", "New Year"]]}
        return {"data":{"html":"<table><tr><td>January</td><td>1 (Thursday)</td><td>New Year</td></tr></table>"}}
    calendar = OfficialMarketCalendar(tmp_path/"calendar.json", fetch_json=fetch, auto_refresh=False, allow_network=False)
    row = calendar._fetch_tw(2026)
    assert row["status"] == "verified_twse_tpex" and row["sources"] == ["TWSE","TPEx"]
    assert "2026-07-10" not in row["sessions"] and "2026-07-10" in row["closed_dates"]
    assert "2026-07-09" in row["sessions"] and "2026-07-13" in row["sessions"]
    proof = row["exceptional_closures"][0]
    assert {s["authority"] for s in proof["sources"]} == {"TWSE", "TPEx"}
    assert proof["announced_on"] == "2026-07-09" and proof["verified_on"] == "2026-10-10"


def test_exception_does_not_promote_unverified_calendar_or_other_years():
    from market_calendar import apply_tw_exceptional_closures
    row = {"status":"cached", "sources":[], "sessions":["2026-07-10","2026-07-13"]}
    assert apply_tw_exceptional_closures(row,2026)["status"] == "cached"
    old = {"status":"verified_twse_tpex", "sessions":["2025-07-10"]}
    assert apply_tw_exceptional_closures(dict(old),2025) == old


def test_fubon_range_changes_82_to_81_only_on_exchange_proof(tmp_path):
    from datetime import date, timedelta
    start, end = date(2026,6,12), date(2026,10,8)
    annual_closed = {"2026-06-19", "2026-09-25", "2026-09-28"}
    sessions = [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)
                if (start+timedelta(days=i)).weekday()<5 and (start+timedelta(days=i)).isoformat() not in annual_closed]
    assert len(sessions) == 82
    path=tmp_path/"calendar.json"
    path.write_text(json.dumps({"version":1,"markets":{"TW":{"years":{"2026":{
        "status":"verified_twse_tpex","sources":["TWSE","TPEx"],"sessions":sessions}}},"US":{"years":{}}}}))
    calendar=OfficialMarketCalendar(path,auto_refresh=False,allow_network=False)
    result=calendar.lookup("TW","2026-06-11","2026-10-08")
    assert len(result["sessions"]) == 81
    assert set(sessions)-set(result["sessions"]) == {"2026-07-10"}
    # Feed a real calendar-derived set to the same exact source validator.
    from fubon_daily_history import normalize, PILOT, TAIPEI
    for symbol,(exchange,market,_) in PILOT.items():
        raw={"symbol":symbol.split('.')[0],"type":"EQUITY","exchange":exchange,"market":market,"timeframe":"D",
             "data":[{"date":d,"open":10,"high":11,"low":9,"close":10,"volume":1000} for d in result["sessions"]]}
        assert len(normalize(raw,symbol,result["sessions"],datetime(2026,10,10,tzinfo=TAIPEI))["bars"]) == 81
