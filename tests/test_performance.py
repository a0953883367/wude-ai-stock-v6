import json
from pathlib import Path

from model_lab import MODEL_NAMES, consensus_prediction, model_predictions
from performance import load_performance_context, update_performance


def _row(
    price: float,
    session: str,
    market: str = "TW",
    symbol: str = "2330.TW",
    group: str = "TW",
) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "type": "個股",
        "backtest_group": group,
        "backtest_rank": 1,
        "official_session_date": session,
        "official_close_price": price,
        "official_adjusted_close_price": price,
        "market_data_quality_score": 90,
        "market_contract_valid": True,
        "trade_guard_blocked": False,
        "short_term_eligible": True,
        "short_term_entry_low": price * .98,
        "short_term_entry_high": price * 1.02,
        "technical_score": 88,
        "volume_score": 88,
        "market_flow_score": 88,
        "institution_score": 88,
        "positioning_score": 88,
        "financial_quality_score": 88,
        "growth_score": 88,
        "valuation_score": 75,
        "group_score": 88,
        "entry_score": 88,
        "short_term_score": 88,
        "score": 88,
        "overall_confidence": 90,
        "macro_score": 70,
        "rsi": 55,
        "attack_volume": 20,
        "change_pct": 2,
        "breakout20": True,
        "breakdown20": False,
        "news_penalty": 0,
    }


def test_taiwan_uses_evening_completed_sessions_and_auditable_prices(tmp_path: Path):
    first = _row(100, "2026-08-18")
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")

    # Noon is not a completed Taiwan close checkpoint and must not create or
    # evaluate a second session.
    noon = _row(105, "2026-08-19")
    noon_summary = update_performance(
        tmp_path, [noon], [noon], "2026-08-19 12:00:00", "noon"
    )
    assert noon_summary["snapshot_count"] == 1
    assert noon_summary["horizons"]["1"]["samples"] == 0

    second = _row(110, "2026-08-19")
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening"
    )
    assert summary["methodology_version"] == 2
    assert summary["horizons"]["1"]["samples"] == 1
    assert summary["horizons"]["1"]["win_rate_pct"] == 100.0
    assert summary["calibration"]["affects_ai_score"] is False

    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    outcome = history["snapshots"][0]["predictions"][0]["outcomes"]["1"]
    assert outcome == {
        "return_pct": 10.0,
        "evaluated_session_date": "2026-08-19",
        "evaluated_price": 110.0,
    }


def test_us_and_tw_sessions_are_isolated_and_duplicate_session_is_ignored(tmp_path: Path):
    us1 = _row(200, "2026-08-18", "US", "NVDA", "US")
    update_performance(tmp_path, [us1], [us1], "2026-08-19 06:00:00", "morning")
    duplicate = _row(202, "2026-08-18", "US", "NVDA", "US")
    duplicate_summary = update_performance(
        tmp_path, [duplicate], [duplicate], "2026-08-19 07:00:00", "morning"
    )
    assert duplicate_summary["snapshot_count"] == 1

    # Taiwan's close cannot complete a US next-session outcome.
    tw = _row(100, "2026-08-19")
    tw_summary = update_performance(
        tmp_path, [tw], [tw], "2026-08-19 20:00:00", "evening"
    )
    assert tw_summary["groups"]["US"]["horizons"]["1"]["samples"] == 0

    us2 = _row(210, "2026-08-19", "US", "NVDA", "US")
    summary = update_performance(
        tmp_path, [us2], [us2], "2026-08-20 06:00:00", "morning"
    )
    assert summary["groups"]["US"]["horizons"]["1"]["samples"] == 1
    assert summary["groups"]["TW"]["horizons"]["1"]["samples"] == 0


def test_trade_metric_only_counts_real_entry_zone_trigger(tmp_path: Path):
    first = _row(100, "2026-08-18")
    first["short_term_entry_low"] = 90
    first["short_term_entry_high"] = 95
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    second = _row(110, "2026-08-19")
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening"
    )
    assert summary["horizons"]["1"]["samples"] == 1
    assert summary["trade_signals"]["1"]["samples"] == 0


def test_legacy_history_is_reset_and_cannot_affect_score(tmp_path: Path):
    (tmp_path / "prediction_history.json").write_text(
        json.dumps({"version": 1, "snapshots": [{"id": "bad"}]}), encoding="utf-8"
    )
    row = _row(100, "2026-08-18")
    summary = update_performance(
        tmp_path, [row], [row], "2026-08-18 20:00:00", "evening"
    )
    assert summary["calibration"]["legacy_history_reset"] is True
    assert summary["calibration"]["affects_ai_score"] is False
    assert load_performance_context(tmp_path)["methodology_version"] == 2


def test_ten_models_and_strict_consensus_are_recorded():
    row = _row(100, "2026-08-18")
    votes = model_predictions(row)
    assert tuple(votes) == MODEL_NAMES
    assert len(votes) == 10
    consensus = consensus_prediction(votes)
    assert consensus["direction"] == "UP"
    assert consensus["up_votes"] >= 7
