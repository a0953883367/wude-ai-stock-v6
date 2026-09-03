from __future__ import annotations

import copy
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from prediction_engine.engine import (
    MAX_PUBLIC_CHUNK_BYTES,
    MAX_PUBLIC_REPORT_BYTES,
    run_prediction_engine,
)
from prediction_engine.features import extract_features
from prediction_engine.models import HORIZONS, fit_challenger, forecast
from prediction_engine.storage import PredictionStore, STABLE_MODEL_VERSION


def _row(symbol: str, market: str = "TW", asset_type: str = "個股") -> dict:
    suffix = ".TW" if market == "TW" and not symbol.endswith(".TW") else ""
    return {
        "symbol": symbol + suffix,
        "name": symbol,
        "market": market,
        "type": asset_type,
        "price": 100.0,
        "official_close_price": 100.0,
        "official_session_date": "2026-01-02",
        "market_contract_valid": True,
        "market_data_quality_score": 95.0,
        "technical_score": 82.0,
        "volume_score": 80.0,
        "positioning_score": 72.0,
        "group_score": 75.0,
        "macro_score": 70.0,
        "fundamental_score": 76.0,
        "valuation_score": 70.0,
        "entry_score": 73.0,
        "news_penalty": 0.0,
        "news_data_available": True,
        "news_summary": "當時已取得新聞",
        "change_pct": 2.0,
        "rsi": 58.0,
        "next_session_direction": "📈 看漲",
        "next_session_confidence": 68.0,
    }


def test_future_fields_cannot_change_point_in_time_forecast() -> None:
    base = _row("2330")
    leaked = copy.deepcopy(base)
    leaked.update({
        "future_price": 99999,
        "actual_return": 800,
        "realized_return_pct": -90,
        "prediction_correct": True,
        "day45_return": 300,
    })
    first = extract_features(base)
    second = extract_features(leaked)
    assert first == second
    assert forecast(first["features"], "UP_5D", chase_risk_points=0, data_quality_pct=90, trade_blocked=False) == forecast(
        second["features"], "UP_5D", chase_risk_points=0, data_quality_pct=90, trade_blocked=False
    )


def test_same_session_gain_is_not_a_directional_bonus_but_continuation_is_allowed() -> None:
    quiet = _row("A")
    quiet.update({"technical_score": 50.0, "change_pct": 0.0})
    gainer = _row("B")
    gainer.update({"technical_score": 70.0, "change_pct": 5.0})
    quiet_features = extract_features(quiet)
    gainer_features = extract_features(gainer)
    assert quiet_features["features"]["trend"] == gainer_features["features"]["trend"]
    quiet_result = forecast(quiet_features["features"], "NEXT_1D", chase_risk_points=0, data_quality_pct=90, trade_blocked=False)
    gainer_result = forecast(gainer_features["features"], "NEXT_1D", chase_risk_points=0, data_quality_pct=90, trade_blocked=False)
    assert quiet_result["probability_pct"] == gainer_result["probability_pct"]
    assert gainer_features["evidence"]["same_session_change_prediction_bonus"] == 0
    assert gainer_features["evidence"]["chase_risk_points"] > 0


def test_engine_is_immutable_compact_and_preserves_old_ledgers(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    protected = {
        "validation_60d.json": b'{"days":7,"owner":"old"}',
        "million_simulation.json": b'{"legacy":true}',
        "prediction_history.json": b'{"legacy":"unchanged"}',
    }
    for name, content in protected.items():
        (reports / name).write_bytes(content)
    rows = [_row(f"T{i}") for i in range(8)] + [_row(f"U{i}", "US") for i in range(8)]
    database = tmp_path / "private" / "engine.sqlite3"
    first = run_prediction_engine(
        reports, rows, period="test", updated_at="2026-01-02T12:00:00Z",
        intraday=False, db_path=database,
    )
    second = run_prediction_engine(
        reports, rows, period="test", updated_at="2026-01-02T13:00:00Z",
        intraday=False, db_path=database,
    )
    assert first["run_summary"]["inserted_predictions"] == len(rows) * len(HORIZONS)
    assert second["run_summary"]["inserted_predictions"] == 0
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == len(rows) * len(HORIZONS)
        assert db.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 6
    assert (reports / "prediction_engine.json").stat().st_size < MAX_PUBLIC_REPORT_BYTES
    assert "rankings" not in second
    assert "symbols" not in second
    assert second["delivery"]["mode"] == "lazy_group_horizon_chunks"
    assert second["delivery"]["combined_file_limit_removed"] is True
    chunk_files = list(reports.glob("prediction_engine_data_*.json"))
    assert len(chunk_files) == 4 * len(HORIZONS)
    assert max(path.stat().st_size for path in chunk_files) < MAX_PUBLIC_CHUNK_BYTES
    assert not (reports / "prediction_engine.sqlite3").exists()
    assert second["policy"]["network_requests"] == 0
    assert second["policy"]["challenger_auto_promotion"] is False
    assert second["policy"]["promotion_requires_manual_approval"] is True
    assert all(
        item["automatic_promotion"] is False
        and item["manual_approval_required"] is True
        for group in second["learning"].values()
        for item in group.values()
    )
    assert second["database"]["public_database_exposed"] is False
    for name, content in protected.items():
        assert (reports / name).read_bytes() == content


def test_market_checkpoints_do_not_freeze_intraday_or_wrong_market(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    rows = [_row("TW1"), _row("US1", "US")]
    database = tmp_path / "engine.sqlite3"
    intraday = run_prediction_engine(
        reports, rows, period="evening", updated_at="2026-01-02T04:00:00Z",
        intraday=True, db_path=database,
    )
    assert intraday["run_summary"]["inserted_predictions"] == 0
    evening = run_prediction_engine(
        reports, rows, period="evening", updated_at="2026-01-02T12:00:00Z",
        intraday=False, db_path=database,
    )
    assert evening["market_status"]["TW"]["ready_for_checkpoint"] is True
    assert evening["market_status"]["US"]["ready_for_checkpoint"] is False
    assert evening["run_summary"]["inserted_predictions"] == len(HORIZONS)


def test_forward_settlement_uses_later_session_only(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    database = tmp_path / "engine.sqlite3"
    start = date(2026, 1, 5)
    for offset in range(6):
        row = _row("2330")
        row["official_session_date"] = (start + timedelta(days=offset)).isoformat()
        row["official_close_price"] = 100.0 + offset
        row["price"] = 100.0 + offset
        run_prediction_engine(
            reports, [row], period="test",
            updated_at=f"{row['official_session_date']}T12:00:00Z",
            intraday=False, db_path=database,
        )
    with sqlite3.connect(database) as db:
        next_day = db.execute(
            "SELECT outcome_session_date,realized_return_pct FROM predictions "
            "WHERE session_date=? AND horizon_code='NEXT_1D'",
            (start.isoformat(),),
        ).fetchone()
        five_day = db.execute(
            "SELECT outcome_session_date,realized_return_pct FROM predictions "
            "WHERE session_date=? AND horizon_code='UP_5D'",
            (start.isoformat(),),
        ).fetchone()
    assert next_day[0] == (start + timedelta(days=1)).isoformat()
    assert round(next_day[1], 2) == 1.0
    assert five_day[0] == (start + timedelta(days=5)).isoformat()
    assert round(five_day[1], 2) == 5.0


def test_historical_outcomes_train_out_of_sample_challenger() -> None:
    rows = []
    for session in range(20):
        for symbol in range(10):
            rows.append({
                "session_date": f"2026-01-{session + 1:02d}",
                "features": {name: 0.45 + ((session + symbol) % 10) / 100 for name in (
                    "trend", "volume", "capital_flow", "positioning", "sector",
                    "market_regime", "fundamental", "valuation", "news", "entry",
                    "shadow_consensus", "quality",
                )},
                "realized_return_pct": ((session + symbol) % 7) - 3,
            })
    model = fit_challenger(
        rows, market="TW", asset_group="TW_STOCK", horizon_code="UP_5D",
        created_at="2026-02-01T00:00:00Z",
    )
    assert model is not None
    assert model["status"] == "candidate_manual_review"
    assert model["metrics"]["promotion_metric"] == "walk_forward_holdout"
    assert model["metrics"]["holdout_session_count"] == 4
    assert "champion_holdout_direction_hit_pct" in model["metrics"]
    assert "_intercept" in model["weights"]


def _candidate(version: str, through: str, *, qualified: bool) -> dict:
    return {
        "model_version": version,
        "market": "TW",
        "asset_group": "TW_STOCK",
        "horizon_code": "NEXT_1D",
        "role": "challenger",
        "status": "candidate_manual_review",
        "trained_through": through,
        "sample_count": 300,
        "session_count": 30,
        "weights": {"_intercept": 0.1, "trend": 0.2},
        "metrics": {
            "walk_forward_holdout_direction_hit_pct": 60.0 if qualified else 48.0,
            "champion_holdout_direction_hit_pct": 55.0,
            "walk_forward_holdout_mae_pct": 0.8 if qualified else 1.2,
            "champion_holdout_mae_pct": 1.0,
        },
        "created_at": f"{through}T12:00:00Z",
    }


def test_model_controller_requires_explicit_approval_then_can_roll_back(tmp_path: Path) -> None:
    store = PredictionStore(tmp_path / "engine.sqlite3")
    for index, through in enumerate(("2026-01-20", "2026-01-21", "2026-01-22"), 1):
        payload = _candidate(f"challenger-{index}", through, qualified=True)
        store.save_model(payload)
        state = store.evaluate_candidate(payload, qualified=True, reasons=[], manual_approval=True)
        assert store.evaluate_candidate(payload, qualified=True, reasons=[], manual_approval=True) == state
    assert state["active_model_version"] == "challenger-3"
    assert state["status"] == "challenger_active"
    assert store.selected_model("TW", "TW_STOCK", "NEXT_1D")["weights"]["trend"] == 0.2

    for index, through in enumerate(("2026-01-23", "2026-01-24"), 4):
        payload = _candidate(f"challenger-{index}", through, qualified=False)
        store.save_model(payload)
        state = store.evaluate_candidate(payload, qualified=False, reasons=["樣本外未勝出"], manual_approval=True)
    assert state["active_model_version"] == STABLE_MODEL_VERSION
    assert state["status"] == "rolled_back_to_stable"
    assert store.selected_model("TW", "TW_STOCK", "NEXT_1D")["weights"] is None
    events = store.recent_control_events()
    assert [item["event"] for item in events[:2]] == ["rolled_back", "rejected_or_warning"]
    assert any(item["event"] == "promoted" for item in events)


def test_automatic_candidate_evaluation_never_changes_active_model(tmp_path: Path) -> None:
    store = PredictionStore(tmp_path / "engine.sqlite3")
    for index, through in enumerate(("2026-01-20", "2026-01-21", "2026-01-22"), 1):
        payload = _candidate(f"candidate-{index}", through, qualified=True)
        store.save_model(payload)
        state = store.evaluate_candidate(payload, qualified=True, reasons=[])
    assert state["active_model_version"] == STABLE_MODEL_VERSION
    assert state["status"] == "eligible_for_manual_review"
    assert state["candidate_model_version"] == "candidate-3"
    assert all(item["event"] != "promoted" for item in store.recent_control_events())


def test_promoted_model_cannot_rewrite_an_already_frozen_answer(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    database = tmp_path / "engine.sqlite3"
    first = run_prediction_engine(
        reports, [_row("2330")], period="test",
        updated_at="2026-01-22T12:00:00Z", intraday=False, db_path=database,
    )
    assert first["run_summary"]["inserted_predictions"] == len(HORIZONS)
    store = PredictionStore(database)
    for index, through in enumerate(("2026-01-20", "2026-01-21", "2026-01-22"), 1):
        payload = _candidate(f"rewrite-guard-{index}", through, qualified=True)
        store.save_model(payload)
        store.evaluate_candidate(payload, qualified=True, reasons=[], manual_approval=True)

    second = run_prediction_engine(
        reports, [_row("2330")], period="test",
        updated_at="2026-01-22T13:00:00Z", intraday=False, db_path=database,
    )
    assert second["run_summary"]["inserted_predictions"] == 0
    with sqlite3.connect(database) as db:
        version = db.execute(
            "SELECT model_version FROM predictions WHERE horizon_code='NEXT_1D'"
        ).fetchone()[0]
    assert version == STABLE_MODEL_VERSION


def test_archive_bootstrap_uses_only_then_current_checkpoint_rows(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    archive = reports / "archive"
    archive.mkdir(parents=True)
    row = _row("2330")
    archived = {
        "period": "evening",
        "updated_at": "2026-01-02T12:00:00Z",
        "watchlist": [row],
    }
    (archive / "2026-01-02-evening.json").write_text(json.dumps(archived), encoding="utf-8")
    current = _row("2330")
    current["official_session_date"] = "2026-01-03"
    result = run_prediction_engine(
        reports, [current], period="test", updated_at="2026-01-03T12:00:00Z",
        intraday=False, db_path=tmp_path / "engine.sqlite3",
    )
    bootstrap = result["run_summary"]["archive_bootstrap"]
    assert bootstrap["sessions"] == 1
    assert bootstrap["predictions"] == len(HORIZONS)
    assert bootstrap["historical_lab_proxy_used_for_training"] is False
