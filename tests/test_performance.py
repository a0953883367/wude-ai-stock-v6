import json
from pathlib import Path

from model_lab import MODEL_NAMES, consensus_prediction, model_predictions, track_predictions
from performance import (
    AUDIT_SCHEMA_VERSION,
    _snapshot_integrity,
    _summary,
    _tw_threshold_calibration,
    _tw_accumulation_validation,
    load_frozen_forecasts,
    load_performance_context,
    update_performance,
)


def test_current_v6_metrics_exclude_legacy_schema_but_keep_audit_count():
    base = _row(100, "2026-08-24", "US", "NVDA", "US")
    tracks = track_predictions(base)

    def snapshot(session_date: str, schema: int, result: float) -> dict:
        row = {
            "symbol": "NVDA",
            "name": "NVDA",
            "market": "US",
            "cohort": "US_STOCK",
            "rank": 1,
            "validation_eligible": True,
            "track_predictions": tracks,
            "consensus": tracks["full_day"]["consensus"],
            "outcomes": {"1": {
                "return_pct": result,
                "close_to_close_return_pct": result,
                "close_to_open_return_pct": result,
                "open_to_close_return_pct": result,
                "evaluated_session_date": "2026-08-26",
            }},
        }
        return {
            "id": f"US:{session_date}",
            "audit_schema_version": schema,
            "market": "US",
            "session_date": session_date,
            "market_regime": {"regime": "bull"},
            "predictions": [row],
        }

    legacy = snapshot("2026-08-24", AUDIT_SCHEMA_VERSION - 1, -10.0)
    current = snapshot("2026-08-25", AUDIT_SCHEMA_VERSION, 10.0)
    summary = _summary([legacy, current])

    metric = summary["groups"]["US_STOCK"]["tracks"]["full_day"]
    assert metric["samples"] == 1
    assert metric["win_rate_pct"] == 100.0
    assert metric["avg_return_pct"] == 10.0
    assert summary["snapshot_count"] == 1
    assert summary["all_snapshot_count"] == 2
    assert summary["calibration"]["trading_days_collected"] == 1
    isolation = summary["version_isolation"]
    assert isolation["current_snapshot_count"] == 1
    assert isolation["legacy_reference_snapshot_count"] == 1
    assert isolation["legacy_affects_headline_metrics"] is False


def test_tw_accumulation_validation_deducts_costs_and_needs_diverse_samples():
    snapshots = []
    for day in range(20):
        rows = []
        for symbol_index in range(10):
            rows.append({
                "symbol": f"T{symbol_index}.TW", "cohort": "TW_STOCK",
                "tw_accumulation_candidate": True,
                "tw_accumulation_strong": True,
                "tw_accumulation_launch_confirmed": True,
                "outcomes": {"5": {
                    "return_pct": 2.0,
                    "close_to_close_return_pct": 2.0,
                    "benchmark_close_to_close_return_pct": .5,
                }},
            })
        snapshots.append({
            "market": "TW", "session_date": f"2026-09-{day + 1:02d}",
            "market_regime": {"regime": "bull"}, "predictions": rows,
        })
    result = _tw_accumulation_validation(snapshots)
    metric = result["regimes"]["all"]["signals"]["candidate_70"]["5"]
    assert metric["samples"] == 200
    assert metric["sessions"] == 20
    assert metric["unique_symbols"] == 10
    assert metric["avg_net_return_pct"] == 1.31
    assert metric["avg_excess_return_pct"] == .81
    assert metric["status"] == "驗證有效"
    assert result["affects_ai_score"] is False

    concentrated = [dict(snapshots[0], predictions=[snapshots[0]["predictions"][0]]) for _ in range(100)]
    concentrated_result = _tw_accumulation_validation(concentrated)
    concentrated_metric = concentrated_result["regimes"]["all"]["signals"]["candidate_70"]["5"]
    assert concentrated_metric["samples"] == 100
    assert concentrated_metric["unique_symbols"] == 1
    assert concentrated_metric["status"] == "樣本不足"


def test_tw_accumulation_signal_is_frozen_then_evaluated_next_session(tmp_path: Path):
    regimes = {"TW": {
        "2026-08-18": {"benchmark": "加權指數", "regime": "bull", "benchmark_close": 100},
        "2026-08-19": {"benchmark": "加權指數", "regime": "bull", "benchmark_close": 101},
    }}
    first = _row(100, "2026-08-18")
    first.update({
        "tw_accumulation_available": True,
        "tw_accumulation_score": 85,
        "tw_accumulation_candidate": True,
        "tw_accumulation_strong": True,
        "tw_accumulation_launch_confirmed": False,
    })
    update_performance(
        tmp_path, [first], [first], "2026-08-18 20:00:00", "evening",
        market_regimes=regimes,
    )
    second = _row(110, "2026-08-19", open_price=105)
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening",
        market_regimes=regimes,
    )
    metric = summary["tw_accumulation_validation"]["regimes"]["bull"]["signals"]["strong_80"]["1"]
    assert metric["samples"] == 1
    assert metric["avg_gross_return_pct"] == 10.0
    assert metric["avg_net_return_pct"] == 9.31
    assert metric["avg_excess_return_pct"] == 8.31
    assert metric["status"] == "樣本不足"
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    frozen = history["snapshots"][0]["predictions"][0]
    assert frozen["tw_accumulation_score"] == 85
    assert frozen["tw_accumulation_strong"] is True


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
        "price": price,
        "avg_volume20": 1_000_000,
        "daily_volume_ratio": 1.4,
        "news_data_available": True,
        "institution_available": market == "TW",
        "credit_available": market == "TW",
        "broker_available": market == "TW",
        "us_live_data_available": market == "US",
        "us_short_volume_available": market == "US",
        "extended_hours_available": market == "US",
        "extended_change_pct": 1.0 if market == "US" else None,
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
    assert summary["methodology_version"] == 6
    assert summary["horizons"]["1"]["samples"] == 1
    assert summary["horizons"]["1"]["win_rate_pct"] == 100.0
    # The deliberately strong complete fixture is independently accepted by
    # all three V6 tracks.
    assert summary["tracks"]["overnight"]["win_rate_pct"] == 100.0
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
    assert load_performance_context(tmp_path)["methodology_version"] == 6


def test_ten_models_and_strict_consensus_are_recorded():
    row = _row(100, "2026-08-18")
    votes = model_predictions(row)
    assert tuple(votes) == MODEL_NAMES
    assert len(votes) == 10
    consensus = consensus_prediction(votes)
    assert consensus["direction"] == "UP"
    assert consensus["up_votes"] >= 7
    assert consensus["signal_level"] == "STRONG"


def test_research_direction_is_separate_from_strong_signal():
    votes = {
        name: {
            "direction": "UP" if index < 6 else "ABSTAIN",
            "confidence": 64,
        }
        for index, name in enumerate(MODEL_NAMES)
    }
    consensus = consensus_prediction(votes)
    assert consensus["direction"] == "UP"
    assert consensus["signal_level"] == "RESEARCH"


def test_three_return_tracks_vote_independently():
    row = _row(100, "2026-08-18")
    predictions = track_predictions(row)
    assert tuple(predictions) == ("overnight", "session", "full_day")
    assert predictions["overnight"]["consensus"]["direction"] == "UP"
    assert predictions["session"]["consensus"]["direction"] == "UP"
    assert predictions["full_day"]["consensus"]["direction"] == "UP"
    assert (
        predictions["overnight"]["models"]["balanced_next"]["score"]
        != predictions["session"]["models"]["balanced_next"]["score"]
    )


def test_overextended_or_weak_market_does_not_become_tomorrow_buy():
    hot = _row(100, "2026-08-18")
    hot.update({"rsi": 82, "change_pct": 7, "daily_volume_ratio": .7})
    assert consensus_prediction(model_predictions(hot))["direction"] == "ABSTAIN"
    weak = _row(100, "2026-08-18")
    weak.update({"macro_score": 25, "market_flow_score": 25, "positioning_score": 25})
    assert consensus_prediction(model_predictions(weak))["direction"] != "UP"


def test_market_specific_profiles_and_missing_evidence_are_explicit():
    tw = _row(100, "2026-08-18")
    us = _row(100, "2026-08-18", "US", "NVDA", "US")
    tw_votes = model_predictions(tw, "overnight")
    us_votes = model_predictions(us, "overnight")
    assert tw_votes["balanced_next"]["score"] != us_votes["balanced_next"]["score"]
    assert us_votes["balanced_next"]["evidence_coverage_pct"] == 90.0

    missing_us = dict(us)
    missing_us.update({
        "us_live_data_available": False,
        "us_short_volume_available": False,
        "extended_hours_available": False,
        "intraday_available": False,
        "us_option_data_available": False,
    })
    missing_votes = model_predictions(missing_us, "overnight")
    assert all(vote["direction"] == "ABSTAIN" for vote in missing_votes.values())
    assert missing_votes["balanced_next"]["evidence_coverage_pct"] < 75


def test_high_volume_upper_wick_never_becomes_next_day_buy():
    row = _row(100, "2026-08-18")
    row.update({
        "technical_score": 95,
        "volume_score": 95,
        "market_flow_score": 90,
        "positioning_score": 90,
        "entry_score": 90,
        "kline_pattern": "上影壓力",
        "kline_score": 35,
        "daily_volume_ratio": 5.5,
        "change_pct": 4.0,
    })
    assert consensus_prediction(model_predictions(row))["direction"] == "ABSTAIN"


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
            "audit_schema_version": AUDIT_SCHEMA_VERSION,
            "market": "TW",
            "session_date": f"2026-09-{day + 1:02d}",
            "predictions": predictions,
        })

    summary = _summary(snapshots)
    calibration = summary["calibration"]
    assert calibration["eligible_one_day_samples"] == 240
    assert calibration["ready_for_model_selection"] is True
    assert calibration["affects_ai_score"] is False
    assert calibration["candidate_upgrade_only"] is True
    assert calibration["automatic_ranking_changes"] is False
    assert calibration["automatic_merge"] is False
    assert calibration["broker_orders"] is False


def test_snapshot_is_hashed_and_same_session_cannot_be_rewritten(tmp_path: Path):
    first = _row(100, "2026-08-18")
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    snapshot = history["snapshots"][0]
    original_hash = snapshot["integrity_sha256"]
    original_price = snapshot["predictions"][0]["official_price"]
    assert _snapshot_integrity(snapshot) == "verified"

    changed = _row(999, "2026-08-18")
    update_performance(tmp_path, [changed], [changed], "2026-08-18 21:00:00", "evening")
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    frozen = history["snapshots"][0]
    assert frozen["integrity_sha256"] == original_hash
    assert frozen["predictions"][0]["official_price"] == original_price
    fixed = load_frozen_forecasts(tmp_path, market="TW")["2330.TW"]
    assert fixed["next_session_source_session_date"] == "2026-08-18"
    assert fixed["next_session_generated_at"] == "2026-08-18 20:00:00"
    assert fixed["next_session_data_mode"] == "固定快照（雜湊驗證通過）"


def test_tampered_snapshot_is_quarantined_from_accuracy(tmp_path: Path):
    first = _row(100, "2026-08-18")
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    history_path = tmp_path / "prediction_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    history["snapshots"][0]["predictions"][0]["official_price"] = 1
    history_path.write_text(json.dumps(history), encoding="utf-8")

    second = _row(110, "2026-08-19")
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening"
    )
    accuracy = json.loads((tmp_path / "accuracy.json").read_text(encoding="utf-8"))
    assert summary["horizons"]["1"]["samples"] == 0
    assert accuracy["integrity"]["mismatch"] == 1


def test_accuracy_groups_top_k_and_abstentions_are_explicit(tmp_path: Path):
    predictions = []
    for rank in range(1, 7):
        row = _row(100, "2026-08-18", symbol=f"T{rank}.TW")
        row["backtest_rank"] = rank
        if rank == 6:
            row.update({"market_data_quality_score": 20, "technical_score": 20})
        predictions.append(row)
    update_performance(
        tmp_path, predictions, predictions, "2026-08-18 20:00:00", "evening"
    )
    current = []
    for rank in range(1, 7):
        row = _row(101, "2026-08-19", symbol=f"T{rank}.TW", open_price=100.5)
        row["backtest_rank"] = rank
        current.append(row)
    summary = update_performance(
        tmp_path, current, current, "2026-08-19 20:00:00", "evening"
    )
    top5 = summary["groups"]["TW_STOCK"]["top_k"]["5"]["tracks"]["full_day"]
    top20 = summary["groups"]["TW_STOCK"]["top_k"]["20"]["tracks"]["full_day"]
    assert top5["eligible_samples"] == 5
    assert top20["eligible_samples"] == 5
    assert summary["data_quality"]["invalid_frozen_rows"] == 1
    assert summary["data_quality"]["invalid_reason_counts"]["market_data_quality"] == 1
    assert top5["sample_status"] == "樣本不足"
    assert "lowest_actual_return_pct" in top5


def test_tw_threshold_calibration_is_shadow_only_and_ignores_us(tmp_path: Path):
    tw = _row(100, "2026-08-18")
    us = _row(200, "2026-08-18", "US", "NVDA", "US")
    update_performance(tmp_path, [tw], [tw], "2026-08-18 20:00:00", "evening")
    update_performance(tmp_path, [us], [us], "2026-08-19 06:00:00", "morning")
    tw2 = _row(102, "2026-08-19", open_price=101)
    us2 = _row(180, "2026-08-19", "US", "NVDA", "US", open_price=190)
    update_performance(tmp_path, [tw2], [tw2], "2026-08-19 20:00:00", "evening")
    update_performance(tmp_path, [us2], [us2], "2026-08-20 06:00:00", "morning")
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    result = _tw_threshold_calibration(history["snapshots"])
    assert result["affects_ai_score"] is False
    assert result["automatic_promotion"] is False
    assert result["cohorts"]["TW_STOCK"][0]["eligible_samples"] == 1
    assert "US_STOCK" not in result["cohorts"]


def test_accuracy_records_actual_high_low_and_target_touch(tmp_path: Path):
    first = _row(100, "2026-08-18")
    first["short_term_stop"] = 95
    first["short_term_target1"] = 108
    first["short_term_target2"] = 115
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    second = _row(106, "2026-08-19", open_price=102)
    second.update({
        "official_high_price": 110,
        "official_low_price": 98,
        "official_adjusted_high_price": 110,
        "official_adjusted_low_price": 98,
    })
    update_performance(tmp_path, [second], [second], "2026-08-19 20:00:00", "evening")
    accuracy = json.loads((tmp_path / "accuracy.json").read_text(encoding="utf-8"))
    record = accuracy["recent"][0]
    assert record["actual_high_price"] == 110.0
    assert record["actual_low_price"] == 98.0
    assert record["target1_touched"] is True
    assert record["target2_touched"] is False
    assert record["stop_touched"] is False


def test_bull_bear_sideways_validation_uses_frozen_source_regime(tmp_path: Path):
    first = _row(100, "2026-08-18")
    regimes = {
        "TW": {
            "2026-08-18": {
                "market": "TW", "benchmark": "加權指數", "session_date": "2026-08-18",
                "regime": "bull", "benchmark_open": 99, "benchmark_close": 100,
            },
            "2026-08-19": {
                "market": "TW", "benchmark": "加權指數", "session_date": "2026-08-19",
                "regime": "sideways", "benchmark_open": 100.5, "benchmark_close": 101,
            },
        }
    }
    update_performance(
        tmp_path, [first], [first], "2026-08-18 20:00:00", "evening",
        market_regimes=regimes,
    )
    second = _row(110, "2026-08-19", open_price=105)
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening",
        market_regimes=regimes,
    )
    validation = summary["regime_validation"]
    bull = validation["markets"]["TW"]["bull"]
    metric = bull["strategies"]["consensus"]["1"]
    assert bull["source_sessions"] == 1
    assert metric["samples"] == 1
    assert metric["win_rate_pct"] == 100.0
    assert metric["benchmark_avg_return_pct"] == 1.0
    assert metric["avg_excess_return_pct"] == 9.0
    assert validation["markets"]["TW"]["sideways"]["strategies"]["consensus"]["1"]["samples"] == 0
    assert validation["affects_ai_score"] is False

    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    assert history["snapshots"][0]["market_regime"]["regime"] == "bull"
    assert _snapshot_integrity(history["snapshots"][0]) == "verified"


def test_regime_history_can_classify_old_snapshot_without_rewriting_it(tmp_path: Path):
    first = _row(100, "2026-08-18")
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    before = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    original_hash = before["snapshots"][0]["integrity_sha256"]
    regimes = {
        "TW": {
            "2026-08-18": {"benchmark": "加權指數", "regime": "bear", "benchmark_close": 100},
            "2026-08-19": {"benchmark": "加權指數", "regime": "sideways", "benchmark_close": 98},
        }
    }
    second = _row(95, "2026-08-19", open_price=97)
    summary = update_performance(
        tmp_path, [second], [second], "2026-08-19 20:00:00", "evening",
        market_regimes=regimes,
    )
    bear = summary["regime_validation"]["markets"]["TW"]["bear"]
    assert bear["strategies"]["consensus"]["1"]["samples"] == 1
    after = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    assert after["snapshots"][0]["integrity_sha256"] == original_hash
    assert after["snapshots"][0].get("market_regime") is None
    assert _snapshot_integrity(after["snapshots"][0]) == "verified"
    accuracy = json.loads((tmp_path / "accuracy.json").read_text(encoding="utf-8"))
    assert accuracy["integrity"]["verified"] == 2
    assert accuracy["integrity"]["mismatch"] == 0


def test_automatic_validation_tracks_day_two_and_collects_errors(tmp_path: Path):
    first = _row(100, "2026-08-18")
    update_performance(tmp_path, [first], [first], "2026-08-18 20:00:00", "evening")
    second = _row(98, "2026-08-19", open_price=99)
    update_performance(tmp_path, [second], [second], "2026-08-19 20:00:00", "evening")
    third = _row(95, "2026-08-20", open_price=97)
    summary = update_performance(
        tmp_path, [third], [third], "2026-08-20 20:00:00", "evening"
    )
    assert summary["horizons"]["2"]["samples"] == 1
    assert summary["portfolio_statistics"]["TW"]["current_consensus"]["2"]["samples"] == 1
    assert summary["error_cases"]["count"] >= 2

    accuracy = json.loads((tmp_path / "accuracy.json").read_text(encoding="utf-8"))
    automation = accuracy["automation"]
    assert automation["mode"] == "fully_automatic_validation"
    assert automation["tracked_horizons"] == [1, 2, 3, 5]
    assert automation["missing_data_is_valid_sample"] is False
    assert automation["promotion_gate"]["automatic_merge"] is False
    assert automation["promotion_gate"]["broker_orders"] is False


def test_missing_source_data_is_frozen_but_never_counted_as_valid_sample(tmp_path: Path):
    incomplete = _row(100, "2026-08-18")
    incomplete["market_data_quality_score"] = None
    update_performance(
        tmp_path, [incomplete], [incomplete], "2026-08-18 20:00:00", "evening"
    )
    complete = _row(110, "2026-08-19")
    summary = update_performance(
        tmp_path, [complete], [complete], "2026-08-19 20:00:00", "evening"
    )
    assert summary["horizons"]["1"]["samples"] == 0
    assert summary["data_quality"]["invalid_frozen_rows"] == 1
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    frozen = history["snapshots"][0]["predictions"][0]
    assert frozen["validation_eligible"] is False
    assert "market_data_quality" in frozen["validation_missing_fields"]
