"""Actual saved proposal acceptance, plus explicitly synthetic forward execution."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from shadow_coach_bridge import build_bridge, digest, update_bridge
from shadow_coach_definitions import REVIEWED_REPORT_SHA256, STRATIFIED_TEMPLATE
from test_shadow_coach_bridge import calendar, snapshot, coach
from performance import _snapshot_hash


def saved():
    return json.loads((Path(__file__).resolve().parents[1] / "shadow_coach_reviewed.json").read_text())


def direction(state):
    return next(e for e in state["experiments"].values() if e["contract"]["template"] == STRATIFIED_TEMPLATE)


def complete_calendar(calendar):
    # Synthetic test sessions, never mixed into production calendar or outcome files.
    for market in ("TW", "US"):
        year = calendar._state["markets"][market]["years"]["2026"]
        year["sessions"] += ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
        year["session_details"].update({d: {"open": "09:30", "close": "16:00"} for d in year["sessions"]})
    return calendar


def test_actual_saved_report_is_accepted_without_text_reinterpretation(calendar):
    report = saved()
    assert digest(report) == REVIEWED_REPORT_SHA256
    h = {"snapshots": [snapshot("2026-10-23")]}
    before = deepcopy((report, h))
    state = build_bridge(report, {}, h, calendar, "2026-10-25 12:00:00")
    assert (report, h) == before
    assert len(state["experiments"]) == 3
    e = direction(state)
    assert e["proposal"] == report["coach"]["candidate_proposals"][0]
    assert e["initial_status"] == "ready"
    assert e["contract"]["rule"]["action"] == "observe_frozen_baseline_only"
    assert e["registered_rows"] == e["completed_rows"] == 0
    assert not e["execution_verified"] and not e["outcome_verified"]
    assert e["excluded_snapshot_ids"] == ["TW:2026-10-23"]
    blocked = [e for e in state["experiments"].values() if e["initial_status"] != "ready"]
    assert {e["status"] for e in blocked} == {"blocked_missing_event_definition_and_frozen_signal", "blocked_missing_reversal_signal_definition"}
    assert all(e["contract"]["reviewed_definition"]["missing_fields"] for e in blocked)
    assert not state["all_candidates_execution_verified"]


@pytest.mark.parametrize("change", ["proposal_text", "response_id", "report_timestamp", "rule", "cohort"])
def test_pinned_review_never_generalizes_to_other_prose(calendar, change):
    r = saved()
    if change == "proposal_text": r["coach"]["candidate_proposals"][0]["hypothesis"] += "extra"
    if change == "response_id": r["api"]["response_id"] = "other"
    if change == "report_timestamp": r["generated_at"] = "2026-10-24T21:22:00+00:00"
    if change == "rule": r["coach"]["candidate_proposals"][0]["rule_id"] = "intraday_reversal"
    if change == "cohort": r["coach"]["candidate_proposals"][0]["cohort"] = "US_ETF"
    state = build_bridge(r, {}, {}, calendar, "2026-10-25 12:00:00")
    assert all(e["status"] == "blocked_missing_execution_template" for e in state["experiments"].values())


def test_saved_direction_executes_fixed_strata_and_horizons_on_synthetic_future(calendar):
    cal = complete_calendar(calendar)
    r = saved()
    state = build_bridge(r, {}, {}, cal, "2026-10-25 12:00:00")
    origin = snapshot()
    # Four forecasts on the exact bin boundaries, plus a baseline abstention.
    base = deepcopy(origin["predictions"][0])
    origin["predictions"] = []
    for n, strength in enumerate((49, 50, 65, 80, 100)):
        row = deepcopy(base); row["symbol"] = f"S{n}"
        row["track_predictions"]["full_day"]["consensus"]["confidence"] = strength
        row["track_predictions"]["full_day"]["consensus"]["direction"] = "UP" if n % 2 == 0 else "DOWN"
        origin["predictions"].append(row)
    row = deepcopy(base); row["symbol"] = "ABSTAIN"; row["track_predictions"]["full_day"]["consensus"]["direction"] = "ABSTAIN"
    origin["predictions"].append(row)
    origin["integrity_sha256"] = _snapshot_hash(origin)
    h = {"snapshots": [origin]}
    state = build_bridge(r, state, h, cal, "2026-10-26 15:00:00")
    e = direction(state)
    assert e["registered_forecasts"] == 6
    assert e["registered_rows"] == 24
    assert all(d["receipt"]["candidate"] == d["receipt"]["baseline"] for d in e["decisions"].values())
    # Same stored directions: evaluate four exact official target sessions.
    for horizon, date, price in [(1, "2026-10-27", 96), (2, "2026-10-28", 104), (3, "2026-10-29", 90), (5, "2026-11-02", 110)]:
        target = snapshot(date, price=price)
        proof_row = deepcopy(target["predictions"][0]); target["predictions"] = []
        for row in origin["predictions"]:
            row.setdefault("outcomes", {})[str(horizon)] = {"evaluated_session_date": date, "close_to_close_return_pct": price - 100}
            proof = deepcopy(proof_row); proof["symbol"] = row["symbol"]
            # Prevent target records becoming extra test forecasts at this checkpoint.
            proof["outcomes"] = {"1": {}}
            target["predictions"].append(proof)
        target["integrity_sha256"] = _snapshot_hash(target)
        h["snapshots"].append(target)
    before = deepcopy(h)
    result = build_bridge(r, state, h, cal, "2026-11-02 15:00:00")
    assert h == before
    e = direction(result)
    assert e["completed_rows"] == 24
    assert "baseline" not in e["cohorts"]["TW_STOCK"]  # no pooled horizons
    audit = e["stratified_audit"]["TW_STOCK"]
    assert set(audit) == {"1", "2", "3", "5"}
    assert audit["1"]["baseline"]["samples"] == 5
    assert audit["1"]["baseline"]["win_rate_pct"] == 40
    bins = audit["1"]["strength_bins"]
    assert bins["0..50_exclusive"]["samples"] == 1
    assert bins["50..65_exclusive"]["samples"] == 1
    assert bins["65..80_exclusive"]["samples"] == 1
    assert bins["80..100_inclusive"]["samples"] == 2
    assert bins["50..65_exclusive"]["hit_rate_difference_pp"] == 60
    assert bins["0..50_exclusive"]["worst_return_pct"] == -4
    assert not any(b["review_ready"] for b in bins.values())
    assert audit["5"]["probability_calibration_error"] is None
    assert result["effectiveness"] == "unproven"
    assert build_bridge(r, result, h, cal, "2026-11-02 15:00:00") == result


def test_all_horizons_require_registration_before_first_open(calendar):
    cal = complete_calendar(calendar); r = saved()
    state = build_bridge(r, {}, {}, cal, "2026-10-25 12:00:00")
    h = {"snapshots": [snapshot()]}
    result = build_bridge(r, state, h, cal, "2026-10-27 10:00:00")
    assert direction(result)["registered_rows"] == 0
    assert direction(result)["gate_counts"]["late_decision_or_visible_outcome"] == 4


def test_horizon_not_counted_using_row_count_or_wrong_target(calendar):
    cal = complete_calendar(calendar); r = saved()
    state = build_bridge(r, {}, {}, cal, "2026-10-25 12:00:00")
    h = {"snapshots": [snapshot()]}
    state = build_bridge(r, state, h, cal, "2026-10-26 15:00:00")
    h["snapshots"][0]["predictions"][0]["outcomes"] = {"5": {"evaluated_session_date": "2026-10-30", "close_to_close_return_pct": -4}}
    h["snapshots"].append(snapshot("2026-10-30", price=96))
    result = build_bridge(r, state, h, cal, "2026-10-30 15:00:00")
    assert direction(result)["completed_rows"] == 0
    assert direction(result)["gate_counts"]["outcome_session_invalid_or_incomplete"] == 1


def test_new_weekly_template_does_not_require_pinned_prose(calendar):
    r = coach(template=STRATIFIED_TEMPLATE)
    state = build_bridge(r, {}, {}, calendar, "2026-10-25 12:00:00")
    assert direction(state)["initial_status"] == "ready"
    assert "reviewed_definition" not in direction(state)["contract"]


def test_saved_source_survives_new_weekly_report_and_uses_current_registration(calendar, tmp_path):
    (tmp_path / "weekly_shadow_coach.json").write_text(json.dumps(coach()))
    state = update_bridge(tmp_path, {"snapshots": [snapshot()]}, "2026-10-26 15:00:00")
    e = direction(state)
    assert e["registered_at"] == "2026-10-26T15:00:00+08:00"
    assert e["registered_rows"] == 0
    assert state["reviewed_source_status"] == "accepted"
    assert len(state["experiments"]) == 4
    assert update_bridge(tmp_path, {"snapshots": [snapshot()]}, "2026-10-26 15:00:00") == state


def test_reviewed_contract_edit_is_blocked(calendar):
    state = build_bridge(saved(), {}, {}, calendar, "2026-10-25 12:00:00")
    direction(state)["contract"]["rule"]["strength_bins"][0][1] = 60
    result = build_bridge(saved(), state, {}, calendar, "2026-10-25 12:00:00")
    assert direction(result)["status"] == "blocked_frozen_contract_changed"
    assert direction(result)["stratified_audit"] == {}


def test_prior_blocked_versions_are_preserved_when_reviewed_definition_is_added(calendar):
    legacy = saved(); legacy["api"]["response_id"] += "-unreviewed"
    old = build_bridge(legacy, {}, {}, calendar, "2026-10-25 12:00:00")
    assert all(e["status"] == "blocked_missing_execution_template" for e in old["experiments"].values())
    result = build_bridge(saved(), old, {}, calendar, "2026-10-25 13:00:00")
    assert len(result["experiments"]) == 6
    for key, value in old["experiments"].items():
        assert result["experiments"][key] == value
    assert direction(result)["registered_at"] == "2026-10-25T13:00:00+08:00"


def test_read_only_acceptance_uses_saved_inputs_and_does_not_write_reports(calendar, tmp_path):
    from tools.shadow_coach_bridge_check import check
    report = tmp_path / "weekly_shadow_coach.json"
    report.write_text(json.dumps(saved()))
    history = tmp_path / "prediction_history.json"
    history.write_text(json.dumps({"snapshots": [snapshot("2026-10-23")]}))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = check(tmp_path, "2026-10-25 12:00:00")
    assert result["inputs_unchanged"]
    assert not result["live_market_execution_claimed"]
    assert result["reviewed_report_sha256"] == REVIEWED_REPORT_SHA256
    assert result["historical_snapshots_excluded"] == 1
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert len(result["experiments"]) == 3


def test_corrupted_pinned_source_hash_blocks_without_reset(calendar, tmp_path, monkeypatch):
    import shadow_coach_bridge
    path = tmp_path / "shadow_coach_experiments.json"
    state = build_bridge(saved(), {}, {}, calendar, "2026-10-25 12:00:00")
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    monkeypatch.setattr(shadow_coach_bridge, "REVIEWED_REPORT_SHA256", "bad")
    with pytest.raises(ValueError, match="reviewed_coach_source_changed"):
        update_bridge(tmp_path, {}, "2026-10-25 13:00:00")
    assert path.read_bytes() == before
