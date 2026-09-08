import copy
import json
from pathlib import Path

from briefing import _update_chart_pattern_validation_safely
from chart_pattern_validation import update_chart_pattern_validation


def _row(date: str, close: float, *, pattern: str = "雙重底", direction: str = "bullish"):
    return {
        "symbol": "TEST.TW", "name": "測試股", "market": "TW", "type": "個股",
        "official_session_date": date, "official_close_price": close,
        "official_high_price": close * 1.01, "official_low_price": close * 0.99,
        "relative_volume": 1.2, "overall_rank": 7, "overall_ranking_score": 72.0,
        "chart_pattern_shadow_name": pattern,
        "chart_pattern_shadow_direction": direction,
        "chart_pattern_shadow_status": "疑似形成，等待收盤確認",
        "chart_pattern_shadow_confidence": 66.0,
        "chart_pattern_shadow_volume_confirmed": False,
        "chart_pattern_shadow_entry": 101.0,
        "chart_pattern_shadow_stop": 95.0,
        "chart_pattern_shadow_target": 107.0,
    }


def test_forward_only_confirmation_and_1_3_5_day_outcomes(tmp_path: Path):
    original = [_row("2026-09-01", 100)]
    frozen = copy.deepcopy(original)
    first = update_chart_pattern_validation(
        tmp_path, original, period="evening", updated_at="day0", intraday=False
    )
    assert original == frozen
    assert first["summary"]["signal_count"] == 1
    assert first["summary"]["matured_1d"] == 0
    assert first["signals"][0]["next_session_confirmation"] is None

    dates = ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08"]
    closes = [102, 101, 104, 103, 106]
    report = first
    for date, close in zip(dates, closes):
        report = update_chart_pattern_validation(
            tmp_path, [_row(date, close)], period="evening",
            updated_at=date, intraday=False,
        )
    signal = report["signals"][0]
    assert report["summary"]["signal_count"] == 1  # continuous pattern is not duplicated
    assert signal["next_session_confirmation"]["direction_confirmed"] is True
    assert signal["next_session_confirmation"]["volume_confirmed"] is True
    assert set(signal["outcomes"]) == {"1", "3", "5"}
    assert signal["outcomes"]["5"]["raw_return_pct"] == 6.0
    assert report["summary"]["matured_5d"] == 1


def test_intraday_and_wrong_market_period_are_read_only(tmp_path: Path):
    rows = [_row("2026-09-01", 100)]
    intraday = update_chart_pattern_validation(
        tmp_path, rows, period="evening", updated_at="noon", intraday=True
    )
    assert intraday["summary"]["signal_count"] == 0
    wrong_period = update_chart_pattern_validation(
        tmp_path, rows, period="morning", updated_at="morning", intraday=False
    )
    assert wrong_period["summary"]["signal_count"] == 0


def test_safe_wrapper_quarantines_failure(monkeypatch, tmp_path: Path):
    import chart_pattern_validation

    def fail(*args, **kwargs):
        raise RuntimeError("isolated")

    monkeypatch.setattr(chart_pattern_validation, "update_chart_pattern_validation", fail)
    assert not _update_chart_pattern_validation_safely(
        tmp_path, [_row("2026-09-01", 100)], period="evening",
        updated_at="now", intraday=False,
    )
    health = json.loads((tmp_path / "chart_pattern_validation_health.json").read_text())
    assert health["status"] == "warning"
    assert health["formal_pipeline_continues"] is True
    assert health["changes_rankings"] is False
