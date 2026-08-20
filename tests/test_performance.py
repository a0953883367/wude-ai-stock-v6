import json
from pathlib import Path

from model_lab import MODEL_NAMES, consensus_prediction, model_predictions, track_predictions
from performance import _summary, load_performance_context, update_performance


def _row(
    price: float,
    session: str,
    market: str = "TW",
    symbol: str = "2330.TW",
    group: str = "TW",
    open_price: float | None = None,
) -> dict:
    open_price = price if open_price is None else open_price
    return {
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "type": "個股",
        "backtest_group": group,
        "backtest_rank": 1,
        "official_session_date": session,
        "official_open_price": open_price,
        "official_close_price": price,
        "official_adjusted_open_price": open_price,
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

    second = _row(110, "2026-08-19", open_price=105)
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening"
    )
    assert summary["methodology_version"] == 4
    assert summary["horizons"]["1"]["samples"] == 1
    assert summary["horizons"]["1"]["win_rate_pct"] == 100.0
    # Overnight correctly abstains for this fixture while the session/full-day
    # models have enough evidence to vote UP.
    assert summary["tracks"]["overnight"]["samples"] == 0
    assert summary["tracks"]["session"]["win_rate_pct"] == 100.0
    assert summary["tracks"]["full_day"]["win_rate_pct"] == 100.0
    assert summary["calibration"]["affects_ai_score"] is False

    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    outcome = history["snapshots"][0]["predictions"][0]["outcomes"]["1"]
    assert outcome == {
        "return_pct": 10.0,
        "close_to_close_return_pct": 10.0,
        "close_to_open_return_pct": 5.0,
        "open_to_close_return_pct": 4.7619,
        "evaluated_session_date": "2026-08-19",
        "evaluated_price": 110.0,
        "evaluated_open_price": 105.0,
        "evaluated_close_price": 110.0,
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
    assert tw_summary["groups"]["US_STOCK"]["horizons"]["1"]["samples"] == 0

    us2 = _row(210, "2026-08-19", "US", "NVDA", "US")
    summary = update_performance(
        tmp_path, [us2], [us2], "2026-08-20 06:00:00", "morning"
    )
    assert summary["groups"]["US_STOCK"]["horizons"]["1"]["samples"] == 1
    assert summary["groups"]["TW_STOCK"]["horizons"]["1"]["samples"] == 0


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


def test_missing_open_price_never_fabricates_overnight_or_session_accuracy(tmp_path: Path):
    first = _row(100, "2026-08-18")
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    second = _row(110, "2026-08-19")
    second["official_open_price"] = None
    second["official_adjusted_open_price"] = None
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening"
    )
    assert summary["tracks"]["overnight"]["samples"] == 0
    assert summary["tracks"]["session"]["samples"] == 0
    assert summary["tracks"]["full_day"]["samples"] == 1


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
    assert load_performance_context(tmp_path)["methodology_version"] == 4


def test_ten_models_and_strict_consensus_are_recorded():
    row = _row(100, "2026-08-18")
    votes = model_predictions(row)
    assert tuple(votes) == MODEL_NAMES
    assert len(votes) == 10
    consensus = consensus_prediction(votes)
    assert consensus["direction"] == "UP"
    assert consensus["up_votes"] >= 7


def test_three_return_tracks_vote_independently():
    row = _row(100, "2026-08-18")
    predictions = track_predictions(row)
    assert tuple(predictions) == ("overnight", "session", "full_day")
    assert predictions["overnight"]["consensus"]["direction"] == "ABSTAIN"
    assert predictions["session"]["consensus"]["direction"] == "UP"
    assert predictions["full_day"]["consensus"]["direction"] == "UP"


def test_overextended_or_weak_market_does_not_become_tomorrow_buy():
    hot = _row(100, "2026-08-18")
    hot.update({"rsi": 82, "change_pct": 7, "daily_volume_ratio": .7})
    assert consensus_prediction(model_predictions(hot))["direction"] == "ABSTAIN"
    weak = _row(100, "2026-08-18")
    weak.update({"macro_score": 25, "market_flow_score": 25, "positioning_score": 25})
    assert consensus_prediction(model_predictions(weak))["direction"] != "UP"


def test_tw_and_us_etfs_are_never_combined(tmp_path: Path):
    tw_etf = _row(100, "2026-08-18", symbol="0050.TW", group="TW_ETF")
    tw_etf["type"] = "ETF"
    update_performance(tmp_path, [tw_etf], [tw_etf], "2026-08-18 20:00:00", "evening")
    tw_etf2 = dict(tw_etf, official_session_date="2026-08-19", official_open_price=101,
                   official_adjusted_open_price=101, official_close_price=102,
                   official_adjusted_close_price=102)
    summary = update_performance(tmp_path, [tw_etf2], [tw_etf2], "2026-08-19 20:00:00", "evening")
    assert summary["groups"]["TW_ETF"]["horizons"]["1"]["samples"] == 1
    assert summary["groups"]["US_ETF"]["horizons"]["1"]["samples"] == 0


def test_reaching_sample_gate_never_silently_changes_production_score():
    snapshots = []
    for day in range(60):
        predictions = []
        for index in range(4):
            predictions.append({
                "symbol": f"T{index}",
                "cohort": "TW_STOCK",
                "track_predictions": {
                    track: {
                        "consensus": {"direction": "UP"},
                        "models": {name: {"direction": "UP"} for name in MODEL_NAMES},
                    }
                    for track in ("overnight", "session", "full_day")
                },
                "trade_triggered": False,
                "outcomes": {"1": {"return_pct": 1.0}},
            })
        snapshots.append({
            "market": "TW",
            "session_date": f"2026-09-{day + 1:02d}",
            "predictions": predictions,
        })

    summary = _summary(snapshots)
    calibration = summary["calibration"]
    assert calibration["eligible_one_day_samples"] == 240
    assert calibration["ready_for_model_selection"] is True
    assert calibration["affects_ai_score"] is False
