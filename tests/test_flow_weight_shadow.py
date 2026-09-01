import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flow_weight_shadow import FlowWeightShadow


TAIPEI = ZoneInfo("Asia/Taipei")


class _TestCalendar:
    def __init__(self, holidays=None):
        self.holidays = set(holidays or [])

    def lookup(self, market: str, start_exclusive: str, end_inclusive: str):
        cursor = date.fromisoformat(start_exclusive) + timedelta(days=1)
        end = date.fromisoformat(end_inclusive)
        sessions = []
        while cursor <= end:
            if cursor.weekday() < 5 and cursor.isoformat() not in self.holidays:
                sessions.append(cursor.isoformat())
            cursor += timedelta(days=1)
        return {"available": True, "status": "verified", "sessions": sessions}

    def status(self, market: str):
        return {
            "market": market,
            "available": True,
            "status": "verified_test_calendar",
            "sources": ["test"],
            "covered_years": [2026],
            "fallback_policy": "verified_cache_or_quarantine_never_guess",
        }


def _model(report: Path, state: Path, *, clock=None, holidays=None):
    kwargs = {"calendar": _TestCalendar(holidays)}
    if clock is not None:
        kwargs["clock"] = clock
    return FlowWeightShadow(report, state, **kwargs)


def _rows(date: str, *, next_prices: bool = False):
    rows = []
    for market, prefix in (("TW", "T"), ("US", "U")):
        for index in range(20):
            rows.append({
                "symbol": f"{prefix}{index:02d}",
                "name": f"{market}{index:02d}",
                "market": market,
                "type": "個股",
                "official_session_date": date,
                "official_open_price": 100 + index if next_prices else 99 + index,
                "official_close_price": 101 + index,
                "short_term_ranking_score": 90 - index,
                "short_term_score": 90 - index,
                "short_term_rank_tier": 2,
                "short_term_eligible": True,
                "trade_guard_blocked": False,
                "market_contract_valid": True,
            })
    return rows


def _write_report(path: Path, date: str, period: str, *, next_prices: bool = False):
    path.write_text(json.dumps({
        "updated_at": f"{date} 16:00:00",
        "period": period,
        "data": _rows(date, next_prices=next_prices),
    }), encoding="utf-8")


def _alert(symbol: str, *, side: str = "buy", trigger: str = "single"):
    epoch = datetime(2026, 8, 28, 13, 30, tzinfo=TAIPEI).timestamp()
    return {
        "market": "TW", "symbol": symbol, "name": symbol,
        "alert_side": side, "trigger_type": trigger,
        "detected_at_epoch": epoch, "detected_at": "2026-08-28T05:30:00Z",
        "price": 100,
    }


def test_flow_overlay_is_capped_and_does_not_mutate_formal_report(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "flow_weight.json"
    _write_report(report, "2026-08-28", "evening")
    original = report.read_bytes()
    model = _model(
        report, state,
        clock=lambda: datetime(2026, 8, 28, 14, 30, tzinfo=TAIPEI).timestamp(),
    )
    model.record_alert(_alert("T05", trigger="cluster"))
    model.record_alert(_alert("T05", trigger="cluster"))
    flow = {"markets": {"TW": {"windows": {"15m": {
        "top_inflows": [{
            "symbol": "T05", "confidence": 80, "net_flow": 10_000,
            "price_change_pct": 1.2,
        }], "top_outflows": [],
    }}}}}
    snapshot = model.snapshot(flow)
    shadow = {row["symbol"]: row for row in snapshot["markets"]["TW"]["shadow_top10"]}
    assert shadow["T05"]["flow_adjustment_points"] == 3
    assert snapshot["policy"]["medium_45_day_unchanged"] is True
    assert snapshot["policy"]["long_6_month_unchanged"] is True
    assert snapshot["policy"]["formal_ranking_locked"] is True
    assert report.read_bytes() == original


def test_taiwan_and_us_signals_are_strictly_separate(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, tmp_path / "state.json")
    model.record_alert(_alert("T03"))
    snapshot = model.snapshot({})
    assert snapshot["markets"]["TW"]["signals"]
    assert snapshot["markets"]["US"]["signals"] == []
    assert all(row["flow_adjustment_points"] == 0 for row in snapshot["markets"]["US"]["shadow_top10"])


def test_completed_session_settles_next_open_to_close_with_cost(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, state)
    model.record_alert(_alert("T01"))
    frozen = model.snapshot({})
    assert frozen["markets"]["TW"]["summary"]["valid_trading_days"] == 0

    _write_report(report, "2026-08-31", "evening", next_prices=True)
    restored = _model(report, state)
    settled = restored.snapshot({})
    outcome = settled["markets"]["TW"]["latest_outcomes"][0]
    assert outcome["status"] == "valid"
    assert outcome["models"]["flow_shadow"]["valid_positions"] == 10
    assert settled["markets"]["TW"]["summary"]["valid_trading_days"] == 1
    assert settled["markets"]["TW"]["summary"]["review_status"] == "collecting_only"


def test_alert_signal_tracks_5_15_60_minute_direction(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, tmp_path / "state.json")
    alert = _alert("T01")
    alert["sequence"] = 7
    model.record_alert(alert)
    at = alert["detected_at_epoch"]
    model.observe_trade("T01", "TW", price=101, timestamp=at + 301)
    model.observe_trade("T01", "TW", price=102, timestamp=at + 901)
    model.observe_trade("T01", "TW", price=103, timestamp=at + 3601)
    performance = model.snapshot({})["markets"]["TW"]["signal_performance"]
    assert performance["tracked_signals"] == 1
    assert performance["horizons"]["5m"]["success_rate_pct"] == 100
    assert performance["horizons"]["15m"]["average_directional_return_pct"] == 2
    assert performance["horizons"]["60m"]["average_directional_return_pct"] == 3
    assert performance["horizons"]["eod"]["samples"] == 1


def test_pending_5_15_60_minute_horizons_survive_restarts(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    alert = _alert("T01")
    alert["sequence"] = 701
    at = alert["detected_at_epoch"]

    model = _model(report, state)
    model.record_alert(alert)
    model.observe_trade("T01", "TW", price=101, timestamp=at + 301)

    model = _model(report, state)
    first_restart = model.snapshot({})["markets"]["TW"]["signal_performance"]["horizons"]
    assert first_restart["5m"]["samples"] == 1
    assert first_restart["15m"]["samples"] == 0
    assert first_restart["60m"]["samples"] == 0
    model.observe_trade("T01", "TW", price=102, timestamp=at + 901)

    model = _model(report, state)
    model.observe_trade("T01", "TW", price=103, timestamp=at + 3601)
    final = model.snapshot({})["markets"]["TW"]["signal_performance"]["horizons"]
    assert final["5m"]["samples"] == 1
    assert final["15m"]["samples"] == 1
    assert final["60m"]["samples"] == 1
    assert final["60m"]["average_directional_return_pct"] == 3


def test_ephemeral_storage_never_counts_as_official_validation(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    model = FlowWeightShadow(
        report,
        state,
        calendar=_TestCalendar(),
        validation_enabled=False,
    )
    alert = _alert("T01")
    alert["sequence"] = 702
    model.record_alert(alert)

    snapshot = model.snapshot({})
    tw = snapshot["markets"]["TW"]
    assert tw["status"] == "storage_not_persistent"
    assert tw["summary"]["valid_trading_days"] == 0
    assert tw["summary"]["valid_signals"] == 0
    assert tw["summary"]["review_status"] == "storage_not_persistent"
    assert snapshot["policy"]["storage_persistent"] is False
    assert snapshot["policy"]["validation_eligible"] is False
    assert model._state["markets"]["TW"]["sessions"] == []


def test_sell_signal_counts_price_decline_as_correct_direction(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, tmp_path / "state.json")
    alert = _alert("T02", side="sell")
    alert["sequence"] = 8
    model.record_alert(alert)
    model.observe_trade(
        "T02", "TW", price=99,
        timestamp=alert["detected_at_epoch"] + 301,
    )
    metric = model.snapshot({})["markets"]["TW"]["signal_performance"]["horizons"]["5m"]
    assert metric["samples"] == 1
    assert metric["success_rate_pct"] == 100
    assert metric["average_directional_return_pct"] == 1


def test_signal_uses_completed_trading_days_not_calendar_days(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, state)
    alert = _alert("T03")
    alert["sequence"] = 9
    model.record_alert(alert)
    model.snapshot({})
    for session_date in ("2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"):
        _write_report(report, session_date, "evening", next_prices=True)
        model = _model(report, state)
        snapshot = model.snapshot({})
    horizons = snapshot["markets"]["TW"]["signal_performance"]["horizons"]
    assert horizons["day1"]["samples"] == 1
    assert horizons["day3"]["samples"] == 1
    assert horizons["day5"]["samples"] == 1
    assert horizons["day5"]["quarantined"] == 0


def test_missing_report_is_quarantined_without_shifting_day_one(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, state)
    alert = _alert("T04")
    alert["sequence"] = 10
    model.record_alert(alert)
    model.snapshot({})

    # Monday's official report is absent; Tuesday is official trading day 2.
    _write_report(report, "2026-09-01", "evening", next_prices=True)
    model = _model(report, state)
    snapshot = model.snapshot({})
    signal = model._state["markets"]["TW"]["intraday_signals"][0]
    by_index = {row["trading_day_index"]: row for row in signal["daily_results"]}
    assert by_index[1]["session_date"] == "2026-08-31"
    assert by_index[1]["status"] == "quarantined_missing_completed_session"
    assert by_index[2]["session_date"] == "2026-09-01"
    assert by_index[2]["status"] == "valid"
    assert snapshot["markets"]["TW"]["signal_performance"]["horizons"]["day1"] == {
        "samples": 0,
        "quarantined": 1,
        "success_rate_pct": None,
        "meaningful_move_rate_pct": None,
        "average_directional_return_pct": None,
    }
    outcome = snapshot["markets"]["TW"]["latest_outcomes"][0]
    assert outcome["outcome_session_date"] == "2026-08-31"
    assert outcome["status"] == "quarantined_missing_completed_session"


def test_official_holiday_is_skipped_before_day_one(tmp_path: Path):
    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    model = _model(report, state, holidays={"2026-08-31"})
    alert = _alert("T05")
    alert["sequence"] = 11
    model.record_alert(alert)
    model.snapshot({})

    _write_report(report, "2026-09-01", "evening", next_prices=True)
    model = _model(report, state, holidays={"2026-08-31"})
    snapshot = model.snapshot({})
    signal = model._state["markets"]["TW"]["intraday_signals"][0]
    assert signal["daily_results"][0]["session_date"] == "2026-09-01"
    assert signal["daily_results"][0]["trading_day_index"] == 1
    assert signal["daily_results"][0]["status"] == "valid"
    assert snapshot["markets"]["TW"]["signal_performance"]["horizons"]["day1"]["samples"] == 1


def test_calendar_unavailable_stops_daily_settlement(tmp_path: Path):
    class UnavailableCalendar(_TestCalendar):
        def lookup(self, market: str, start_exclusive: str, end_inclusive: str):
            return {"available": False, "status": "official_calendar_unavailable", "sessions": []}

        def status(self, market: str):
            return {"market": market, "available": False, "status": "official_calendar_unavailable"}

    report = tmp_path / "all_analysis.json"
    state = tmp_path / "state.json"
    _write_report(report, "2026-08-28", "evening")
    model = FlowWeightShadow(report, state, calendar=UnavailableCalendar())
    alert = _alert("T06")
    alert["sequence"] = 12
    model.record_alert(alert)
    model.snapshot({})
    _write_report(report, "2026-08-31", "evening", next_prices=True)
    model = FlowWeightShadow(report, state, calendar=UnavailableCalendar())
    snapshot = model.snapshot({})
    assert snapshot["markets"]["TW"]["summary"]["valid_trading_days"] == 0
    assert model._state["markets"]["TW"]["intraday_signals"][0]["daily_results"] == []
    assert snapshot["policy"]["google_calendar_connected"] is False
