from __future__ import annotations

import json
from pathlib import Path

from model_learning import update_model_learning


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_learning_report_builds_candidates_without_touching_v6(tmp_path: Path) -> None:
    groups = {}
    for cohort in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        groups[cohort] = {
            "horizons": {"1": {"samples": 25}},
            "trade_signals": {"1": {"samples": 0}},
        }
    _write(tmp_path / "performance.json", {
        "updated_at": "2026-09-03 20:00:00",
        "calibration": {"trading_days_collected": 9},
        "groups": groups,
        "error_cases": {
            "row_count": 7,
            "unique_event_count": 1,
            "unique_symbol_count": 1,
            "duplicate_row_count": 6,
            "cause_counts": {"event_gap_risk": 1},
            "event_clusters": [{
                "event_id": "US:PCG:2026-08-24:2026-09-02",
                "symbol": "PCG",
                "primary_cause": "event_gap_risk",
            }],
        },
    })

    report = update_model_learning(tmp_path, updated_at="2026-09-03 20:00:00")

    assert report["error_learning"]["raw_error_rows"] == 7
    assert report["error_learning"]["independent_events"] == 1
    assert report["error_learning"]["duplicate_rows_collapsed"] == 6
    candidate_ids = {item["candidate_id"] for item in report["shadow_candidates"]}
    assert "shadow:event_gap_risk:v1" in candidate_ids
    assert "shadow:trade_threshold_diagnostic:v1" in candidate_ids
    assert report["policy"]["formal_v6_frozen"] is True
    assert report["policy"]["automatic_merge"] is False
    assert report["policy"]["broker_orders"] is False
    complete = report["complete_learning"]
    assert complete["inventory_complete"] is True
    assert complete["summary"]["registered_units"] == 52
    assert complete["summary"]["connected_units"] == 52
    assert complete["summary"]["learning_governance_coverage_pct"] == 100.0
    assert complete["summary"]["by_layer"] == {
        "forecast": 23, "evidence": 14, "execution": 9, "governance": 6,
    }
    assert complete["summary"]["controlled_shadow_auto_upgrade_units"] == 7
    assert complete["summary"]["controlled_shadow_trust_units"] == 11
    assert complete["summary"]["dedicated_validation_units"] == 52
    assert complete["shared_rules"]["controlled_shadow_auto_promotion"] is True
    assert complete["shared_rules"]["formal_v6_automatic_promotion"] is False
    assert report["policy"]["controlled_central_trust_auto_update"] is True
    assert (tmp_path / "model_learning.json").exists()


def test_trade_signal_health_separates_direction_questions_from_trades(tmp_path: Path) -> None:
    _write(tmp_path / "performance.json", {
        "calibration": {"trading_days_collected": 20},
        "groups": {
            "TW_STOCK": {
                "horizons": {"1": {"samples": 100}},
                "trade_signals": {"1": {"samples": 3}},
            },
        },
    })
    report = update_model_learning(tmp_path, updated_at="2026-09-03 20:00:00")
    tw = report["signal_health"]["TW_STOCK"]
    assert tw["direction_samples"] == 100
    assert tw["trade_signal_samples"] == 3
    assert tw["status"] == "collecting_trade_outcomes"


def test_legacy_performance_does_not_erase_event_level_learning_history(tmp_path: Path) -> None:
    _write(tmp_path / "model_learning.json", {
        "error_learning": {
            "raw_error_rows": 9,
            "independent_events": 3,
            "unique_symbols": 2,
            "duplicate_rows_collapsed": 6,
            "cause_counts": {"event_gap_risk": 2, "intraday_reversal": 1},
            "recent_events": [{"event_id": "kept-event"}],
        },
        "shadow_candidates": [{
            "candidate_id": "shadow:event_gap_risk:v1",
            "evidence_event_count": 2,
        }],
    })
    _write(tmp_path / "performance.json", {
        "calibration": {"trading_days_collected": 4},
        "error_cases": {"count": 9},
    })

    report = update_model_learning(tmp_path, updated_at="2026-09-03 20:00:00")

    assert report["error_learning"] == {
        "raw_error_rows": 9,
        "independent_events": 3,
        "unique_symbols": 2,
        "duplicate_rows_collapsed": 6,
        "cause_counts": {"event_gap_risk": 2, "intraday_reversal": 1},
        "recent_events": [{"event_id": "kept-event"}],
    }
    assert report["shadow_candidates"] == [{
        "candidate_id": "shadow:event_gap_risk:v1",
        "evidence_event_count": 2,
    }]
