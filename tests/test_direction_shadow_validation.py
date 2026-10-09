from copy import deepcopy

from direction_shadow_validation import build_direction_validation
from performance import _snapshot_hash


def snapshot(strength=63, direction="UP"):
    s = {"id": "US:2026-10-12", "session_date": "2026-10-12", "market": "US",
         "captured_at": "2026-10-13 06:00:00", "audit_schema_version": 4,
         "predictions": [{"symbol": "TEST", "cohort": "US_STOCK", "validation_eligible": True,
                          "track_predictions": {"full_day": {"consensus": {"direction": direction, "confidence": strength}}}}]}
    s["integrity_sha256"] = _snapshot_hash(s)
    return s


def initial(history=None):
    return build_direction_validation(history or {}, {}, "2026-10-09 21:00:00")


def test_existing_history_is_excluded_and_not_rewritten():
    h = {"snapshots": [snapshot()]}
    before = deepcopy(h)
    state = initial(h)
    assert state["registered_rows"] == 0
    assert state["excluded_snapshot_ids"] == ["US:2026-10-12"]
    assert h == before
    assert state["brier_score"] is None


def test_freezes_decisions_before_results_and_compares_abstention():
    h = {"snapshots": [snapshot()]}
    state = build_direction_validation(h, initial(), "2026-10-13 07:00:00")
    assert state["registered_rows"] == 1
    h["snapshots"][0]["predictions"][0]["outcomes"] = {"1": {"close_to_close_return_pct": -4, "evaluated_session_date": "2026-10-13"}}
    state = build_direction_validation(h, state, "2026-10-14 07:00:00")
    result = state
    c = result["cohorts"]["US_STOCK"]
    assert c["baseline"]["samples"] == 1
    assert c["candidate"]["samples"] == 0
    assert c["newly_abstained_rows"] == 1
    assert c["baseline"]["avg_return_pct"] == -4
    again = build_direction_validation(h, result, "2026-10-16 07:00:00")
    assert again["cohorts"] == result["cohorts"]


def test_late_registration_does_not_rescore_known_outcomes():
    s = snapshot()
    s["predictions"][0]["outcomes"] = {"1": {"close_to_close_return_pct": 5, "evaluated_session_date": "2026-10-14"}}
    result = build_direction_validation({"snapshots": [s]}, initial(), "2026-10-15 07:00:00")
    assert result["registered_rows"] == 0


def test_invalid_contract_and_changed_forecast_are_excluded():
    s = snapshot(70)
    h = {"snapshots": [s]}
    state = build_direction_validation(h, initial(), "2026-10-13 07:00:00")
    s["predictions"][0]["track_predictions"]["full_day"]["consensus"]["confidence"] = 80
    s["integrity_sha256"] = _snapshot_hash(s)
    result = build_direction_validation(h, state, "2026-10-15 07:00:00")
    assert any(x.startswith("forecast_changed:") for x in result["blocked_reasons"])
    s["predictions"][0]["validation_eligible"] = False
    s["integrity_sha256"] = _snapshot_hash(s)
    assert build_direction_validation(h, initial(), "2026-10-15 07:00:00")["registered_rows"] == 0


def test_rule_change_blocks_and_cannot_promote():
    state = initial()
    state["rule"]["minimum_strength"] = 60
    result = build_direction_validation({}, state, "2026-10-10 07:00:00")
    assert result["status"] == "blocked"
    assert not result["automatic_promotion"]
    assert not result["affects_formal_v6"]


def test_invalid_integrity_future_timestamp_and_nan_are_not_enrolled():
    s = snapshot()
    s["integrity_sha256"] = "bad"
    assert build_direction_validation({"snapshots": [s]}, initial(), "2026-10-13 07:00:00")["registered_rows"] == 0
    s = snapshot(float("nan"))
    assert build_direction_validation({"snapshots": [s]}, initial(), "2026-10-13 07:00:00")["registered_rows"] == 0
    assert build_direction_validation({"snapshots": [snapshot()]}, initial(), "2026-10-12 07:00:00")["registered_rows"] == 0


def test_high_strength_direction_is_retained_and_empty_metrics_are_unknown():
    state = initial()
    assert state["cohorts"]["US_STOCK"]["candidate"]["win_rate_pct"] is None
    state = build_direction_validation({"snapshots": [snapshot(70, "DOWN")]}, state, "2026-10-13 07:00:00")
    decision = next(iter(state["decisions"].values()))
    assert decision["candidate"] == decision["baseline"] == "DOWN"


def test_existing_report_pipeline_persists_registration(tmp_path):
    import json
    from model_learning import update_model_learning
    first = update_model_learning(tmp_path, updated_at="2026-10-09 21:00:00")
    (tmp_path / "prediction_history.json").write_text(json.dumps({"snapshots": [snapshot()]}))
    second = update_model_learning(tmp_path, updated_at="2026-10-13 07:00:00")
    state = second["direction_calibration_validation"]
    assert state["registered_at"] == first["direction_calibration_validation"]["registered_at"]
    assert state["registered_rows"] == 1
    assert second["policy"] == first["policy"]
