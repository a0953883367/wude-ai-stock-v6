from copy import deepcopy
import json

import pytest

from market_calendar import OfficialMarketCalendar
from performance import _snapshot_hash
from shadow_coach_bridge import TEMPLATE, build_bridge, update_bridge, digest
from weekly_shadow_coach import LOCKED_POLICY, _validate_candidate_proposals, CoachBlocked


def coach(template=TEMPLATE, cohort="TW_STOCK", rule="direction_calibration"):
    p = dict(rule_id=rule, cohort=cohort, hypothesis="fixed abstention", measurement="paired one day", risk_control="shadow only")
    if template is not None:
        p["execution_template"] = template
    return {"schema": "wude.weekly_shadow_coach.v1", "status": "ok", "mode": "openai",
            "generated_at": "2026-10-24T21:21:25+00:00", "api": {"called": True, "response_id": "resp_test"},
            "policy": deepcopy(LOCKED_POLICY), "coach": {"candidate_proposals": [p]}}


def snapshot(day="2026-10-26", price=100, strength=63, market="TW"):
    captured = day + " 14:00:00" if market == "TW" else "2026-10-27 05:00:00"
    s = {"id": market + ":" + day, "session_date": day, "market": market,
         "captured_at": captured, "audit_schema_version": 4,
         "predictions": [{"symbol": "TEST", "cohort": market + "_STOCK", "official_session_date": day,
                          "official_price": price, "validation_eligible": True,
                          "track_predictions": {"full_day": {"consensus": {"direction": "UP", "confidence": strength}}}}]}
    s["predictions"][0]["coach_price_evidence"] = {
        "source_daily_ohlcv_complete": True, "source_daily_ohlcv_session_date": day,
        "official_open_price": price, "official_high_price": price, "official_low_price": price,
        "official_close_price": price, "official_volume": 100,
        "tw_official_price_available": True, "tw_official_session_date": day,
        "tw_price_source": "TWSE OpenAPI", "tw_price_unit": "TWD/shares",
        "us_daily_price_available": True, "us_daily_session_date": day,
        "us_daily_source": "Yahoo Finance daily bars", "us_daily_price_unit": "USD/shares",
    }
    s["integrity_sha256"] = _snapshot_hash(s)
    return s


@pytest.fixture
def calendar(tmp_path):
    sessions = ["2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
    data = {"version": 1, "markets": {m: {"years": {"2026": {"status": "verified", "sessions": sessions,
            "session_details": {d: {"open": "09:30", "close": "16:00"} for d in sessions}}}} for m in ("TW", "US")}}
    path = tmp_path / "official_market_calendar.json"
    path.write_text(json.dumps(data))
    return OfficialMarketCalendar(path, auto_refresh=False, allow_network=False)


def initial(calendar, report=None, history=None):
    return build_bridge(report or coach(), {}, history or {}, calendar, "2026-10-25 12:00:00")


def only(state):
    return next(iter(state["experiments"].values()))


def enroll(calendar, report=None):
    report = report or coach()
    h = {"snapshots": [snapshot()]}
    state = build_bridge(report, initial(calendar, report), h, calendar, "2026-10-26 15:00:00")
    return report, h, state


def settled(calendar):
    report, h, state = enroll(calendar)
    h["snapshots"][0]["predictions"][0]["outcomes"] = {"1": {"evaluated_session_date": "2026-10-27", "close_to_close_return_pct": -4}}
    h["snapshots"].append(snapshot("2026-10-27", price=96))
    return report, h, build_bridge(report, state, h, calendar, "2026-10-27 15:00:00")


def test_prospective_pair_idempotency_and_immutable_inputs(calendar):
    report, h, state = enroll(calendar)
    assert only(state)["registered_rows"] == 1
    assert only(state)["completed_rows"] == 0
    before = deepcopy((report, h, state))
    again = build_bridge(report, state, h, calendar, "2026-10-26 15:00:00")
    assert again == state
    assert (report, h, state) == before
    report, h, state = settled(calendar)
    e = only(state)
    assert e["completed_rows"] == 1
    assert e["cohorts"]["TW_STOCK"]["baseline"]["avg_return_pct"] == -4
    assert e["cohorts"]["TW_STOCK"]["candidate"]["samples"] == 0
    assert e["outcome_verified"] is True
    assert state["effectiveness"] == "unproven"
    assert state["policy"]["automatic_promotion"] is False
    assert build_bridge(report, state, h, calendar, "2026-10-27 15:00:00") == state


@pytest.mark.parametrize("template,rule,status", [
    (None, "direction_calibration", "blocked_missing_execution_template"),
    ("unsupported", "missed_strength_rotation", "unsupported_execution_template"),
    (TEMPLATE, "event_gap_risk", "unsupported_execution_template"),
    ("__import__('os').system('bad')", "direction_calibration", "unsupported_execution_template"),
])
def test_no_prose_execution_or_false_candidate_mapping(calendar, template, rule, status):
    state = initial(calendar, coach(template=template, rule=rule))
    assert only(state)["status"] == status
    assert not state["all_candidates_execution_verified"]
    assert only(state)["completed_rows"] == 0


@pytest.mark.parametrize("change", ["dry_run", "future", "lock", "no_api", "oversize", "bad_cohort"])
def test_invalid_report_never_registers(calendar, change):
    r = coach()
    if change == "dry_run": r["mode"] = "dry_run"
    if change == "future": r["generated_at"] = "2027-01-01T00:00:00Z"
    if change == "lock": r["policy"]["automatic_orders"] = True
    if change == "no_api": r["api"]["called"] = False
    if change == "oversize": r["coach"]["candidate_proposals"] *= 4
    if change == "bad_cohort": r["coach"]["candidate_proposals"][0]["cohort"] = "UNKNOWN"
    assert initial(calendar, r)["experiments"] == {}


def test_changed_proposal_creates_new_version_without_rewriting(calendar):
    r, h, state = enroll(calendar)
    old = deepcopy(only(state))
    r["coach"]["candidate_proposals"][0]["hypothesis"] = "new rationale"
    result = build_bridge(r, state, h, calendar, "2026-10-26 15:00:00")
    assert len(result["experiments"]) == 2
    assert result["experiments"][next(iter(state["experiments"]))] == old
    assert sum(e["registered_rows"] for e in result["experiments"].values()) == 1


def test_repeated_weekly_proposal_does_not_restart(calendar):
    r, h, state = enroll(calendar)
    r["generated_at"] = "2026-10-26T06:00:00+00:00"
    r["api"]["response_id"] = "resp_new"
    result = build_bridge(r, state, h, calendar, "2026-10-26 15:00:00")
    assert len(result["experiments"]) == 1
    assert only(result)["provenance"] == only(state)["provenance"]


@pytest.mark.parametrize("change", ["late", "outcome", "integrity", "duplicate", "nan", "bool", "source_date", "ineligible", "holiday", "future", "duplicate_symbol"])
def test_bad_inputs_cannot_register(calendar, change):
    s = snapshot()
    now = "2026-10-26 15:00:00"
    if change == "late": now = "2026-10-27 09:00:00"
    if change == "outcome": s["predictions"][0]["outcomes"] = {"3": {}}
    if change == "nan": s["predictions"][0]["track_predictions"]["full_day"]["consensus"]["confidence"] = float("nan")
    if change == "bool": s["predictions"][0]["track_predictions"]["full_day"]["consensus"]["confidence"] = True
    if change == "source_date": s["predictions"][0]["official_session_date"] = "2026-10-23"
    if change == "ineligible": s["predictions"][0]["validation_eligible"] = False
    if change == "holiday": s = snapshot("2026-10-25")
    if change == "future": s["captured_at"] = "2026-10-26 16:00:00"
    if change == "duplicate_symbol": s["predictions"] *= 2
    s["integrity_sha256"] = _snapshot_hash(s)
    if change == "integrity": s["integrity_sha256"] = "bad"
    h = {"snapshots": [s, s] if change == "duplicate" else [s]}
    result = build_bridge(coach(), initial(calendar), h, calendar, now)
    assert only(result)["registered_rows"] == 0


def test_existing_history_excluded_forever(calendar):
    h = {"snapshots": [snapshot()]}
    state = initial(calendar, history=h)
    assert only(build_bridge(coach(), state, h, calendar, "2026-10-26 15:00:00"))["registered_rows"] == 0


@pytest.mark.parametrize("change", ["missing_price", "wrong_price", "wrong_day", "incomplete", "duplicate_price", "changed_forecast", "changed_receipt"])
def test_settlement_gates(calendar, change):
    r, h, state = enroll(calendar)
    s = h["snapshots"][0]
    s["predictions"][0]["outcomes"] = {"1": {"evaluated_session_date": "2026-10-27", "close_to_close_return_pct": -4}}
    h["snapshots"].append(snapshot("2026-10-27", price=96))
    now = "2026-10-27 15:00:00"
    if change == "missing_price": h["snapshots"].pop()
    if change == "wrong_price": s["predictions"][0]["outcomes"]["1"]["close_to_close_return_pct"] = 7
    if change == "wrong_day": s["predictions"][0]["outcomes"]["1"]["evaluated_session_date"] = "2026-10-28"
    if change == "incomplete": now = "2026-10-27 12:00:00"
    if change == "duplicate_price": h["snapshots"].append(deepcopy(h["snapshots"][-1])); h["snapshots"][-1]["id"] += "-duplicate"
    if change == "changed_forecast": s["predictions"][0]["official_price"] = 200; s["integrity_sha256"] = _snapshot_hash(s)
    if change == "changed_receipt": next(iter(only(state)["decisions"].values()))["receipt"]["candidate"] = "DOWN"
    if change == "duplicate_price": h["snapshots"][-1]["integrity_sha256"] = _snapshot_hash(h["snapshots"][-1])
    result = build_bridge(r, state, h, calendar, now)
    assert only(result)["completed_rows"] == 0
    assert only(result)["gate_counts"]


def test_frozen_parameters_block(calendar):
    r, h, state = enroll(calendar)
    only(state)["contract"]["rule"]["minimum_strength"] = 1
    e = only(build_bridge(r, state, h, calendar, "2026-10-26 16:00:00"))
    assert e["status"] == "blocked_frozen_contract_changed"
    assert not e["execution_verified"]


def test_clock_and_corrupt_state_preserved(calendar, tmp_path):
    state = initial(calendar)
    with pytest.raises(ValueError, match="clock_regressed"):
        build_bridge(coach(), state, {}, calendar, "2026-10-24 12:00:00")
    path = tmp_path / "shadow_coach_experiments.json"
    path.write_text("broken")
    with pytest.raises(ValueError): update_bridge(tmp_path, {}, "2026-10-25 12:00:00")
    assert path.read_text() == "broken"


def test_missing_calendar_fails_closed(tmp_path):
    calendar = OfficialMarketCalendar(tmp_path / "missing", auto_refresh=False, allow_network=False)
    _, _, state = enroll(calendar)
    assert only(state)["registered_rows"] == 0


def test_us_timezone_and_cohort_gate(calendar):
    r = coach(cohort="US_STOCK")
    h = {"snapshots": [snapshot(market="US"), snapshot()]}
    state = build_bridge(r, initial(calendar, r), h, calendar, "2026-10-27 06:00:00")
    assert only(state)["registered_rows"] == 1
    receipt = next(iter(only(state)["decisions"].values()))["receipt"]
    assert receipt["target_open"] == "2026-10-27T09:30:00-04:00"
    late = build_bridge(r, initial(calendar, r), h, calendar, "2026-10-27 22:00:00")
    assert only(late)["registered_rows"] == 0


def test_schema_preserves_template_and_rejects_mismatch():
    r = coach()["coach"]
    assert _validate_candidate_proposals(r)[0]["execution_template"] == TEMPLATE
    with pytest.raises(CoachBlocked):
        _validate_candidate_proposals(coach(rule="event_gap_risk")["coach"])


def test_daily_pipeline_link_and_corrupt_state_is_visible(calendar, tmp_path):
    from model_learning import update_model_learning
    (tmp_path / "weekly_shadow_coach.json").write_text(json.dumps(coach()))
    first = update_model_learning(tmp_path, updated_at="2026-10-25 12:00:00")
    assert first["coach_execution_bridge"]["status"] == "accepted"
    (tmp_path / "prediction_history.json").write_text(json.dumps({"snapshots": [snapshot()]}))
    result = update_model_learning(tmp_path, updated_at="2026-10-26 15:00:00")
    assert next(e for e in result["coach_execution_bridge"]["experiments"].values() if e["template"] == TEMPLATE)["registered_rows"] == 1
    (tmp_path / "shadow_coach_experiments.json").write_text("corrupt")
    result = update_model_learning(tmp_path, updated_at="2026-10-26 16:00:00")
    assert result["coach_execution_bridge"]["status"] == "blocked"
    assert (tmp_path / "shadow_coach_experiments.json").read_text() == "corrupt"


@pytest.mark.parametrize("field,value", [
    ("source_daily_ohlcv_complete", False),
    ("source_daily_ohlcv_session_date", "2026-10-23"),
    ("tw_official_session_date", "2026-10-23"),
    ("tw_official_price_available", False),
    ("tw_price_source", "TWSE Open Data"),
    ("tw_price_unit", "unknown"),
    ("official_volume", None),
    ("official_adjusted_close_price", 50),
    ("official_high_price", 1),
    ("official_close_price", 90),
])
def test_source_provenance_rejects_stale_partial_or_unapproved(calendar, field, value):
    s = snapshot()
    s["predictions"][0]["coach_price_evidence"][field] = value
    s["integrity_sha256"] = _snapshot_hash(s)
    result = build_bridge(coach(), initial(calendar), {"snapshots": [s]}, calendar, "2026-10-26 15:00:00")
    assert only(result)["registered_rows"] == 0
    assert only(result)["gate_counts"]["frozen_source_contract_invalid"] == 1


def test_outcome_revision_is_quarantined_permanently(calendar):
    r, h, state = settled(calendar)
    # A new field cannot silently replace an accepted outcome receipt.
    h["snapshots"][0]["predictions"][0]["outcomes"]["1"]["revision"] = True
    state = build_bridge(r, state, h, calendar, "2026-10-27 15:00:00")
    assert only(state)["completed_rows"] == 0
    del h["snapshots"][0]["predictions"][0]["outcomes"]["1"]["revision"]
    state = build_bridge(r, state, h, calendar, "2026-10-27 15:00:00")
    assert only(state)["completed_rows"] == 0
    assert only(state)["gate_counts"]["outcome_revision_quarantined"] == 1


def test_invalid_source_does_not_interrupt_existing_execution(calendar):
    _, h, state = enroll(calendar)
    state = build_bridge({}, state, h, calendar, "2026-10-26 16:00:00")
    assert state["source_status"] == "no_successful_openai_coach_report"
    assert only(state)["execution_verified"]


def test_new_snapshot_freezes_provenance_without_rewriting_source():
    from performance import _new_snapshot
    row = {"market": "TW", "symbol": "TEST", "official_session_date": "2026-10-26", "official_close_price": 100,
           **snapshot()["predictions"][0]["coach_price_evidence"]}
    before = deepcopy(row)
    frozen = _new_snapshot("TW", "2026-10-26", [row], "2026-10-26 14:00:00", "evening")
    assert row == before
    proof = frozen["predictions"][0]["coach_price_evidence"]
    assert proof["tw_price_source"] == "TWSE OpenAPI"
    assert proof["source_daily_ohlcv_session_date"] == "2026-10-26"
    original_hash = frozen["integrity_sha256"]
    proof["tw_price_source"] = "changed"
    assert _snapshot_hash(frozen) != original_hash


def test_coach_boundary_accepts_explicit_template_without_real_api(tmp_path):
    from test_weekly_shadow_coach import _Response, _Session, _learning, _backup, _archive, _write
    from weekly_shadow_coach import build_weekly_coach

    class Response(_Response):
        def json(self):
            result = super().json()
            content = result["output"][0]["content"][0]
            payload = json.loads(content["text"])
            payload["candidate_proposals"][0]["execution_template"] = TEMPLATE
            content["text"] = json.dumps(payload)
            return result

    class Session(_Session):
        def post(self, url, **kwargs):
            return Response()

    _write(tmp_path / "model_learning.json", _learning())
    _write(tmp_path / "prediction_evidence_backup_health.json", _backup())
    _write(tmp_path / "archive.json", _archive())
    report = build_weekly_coach(tmp_path, mode="openai", api_key="fake", session=Session(),
                                archive_result=tmp_path / "archive.json", generated_at="2026-10-25T00:00:00Z")
    assert report["coach"]["candidate_proposals"][0]["execution_template"] == TEMPLATE
    state = update_bridge(tmp_path, {}, "2026-10-25 12:00:00")
    e = next(e for e in state["experiments"].values() if e["contract"]["template"] == TEMPLATE)
    assert e["status"] == "collecting"
    assert e["provenance"]["api"]["response_id"] == "resp_test"


@pytest.mark.parametrize("strength,direction,expected", [(64, "DOWN", "ABSTAIN"), (65, "DOWN", "DOWN"), (70, "UP", "UP")])
def test_exact_fixed_threshold(calendar, strength, direction, expected):
    s = snapshot(strength=strength)
    s["predictions"][0]["track_predictions"]["full_day"]["consensus"]["direction"] = direction
    s["integrity_sha256"] = _snapshot_hash(s)
    state = build_bridge(coach(), initial(calendar), {"snapshots": [s]}, calendar, "2026-10-26 15:00:00")
    assert next(iter(only(state)["decisions"].values()))["receipt"]["candidate"] == expected


def test_capacity_never_prunes_or_restarts(calendar, monkeypatch):
    import shadow_coach_bridge
    monkeypatch.setattr(shadow_coach_bridge, "MAX_EXPERIMENTS", 1)
    r, h, state = enroll(calendar)
    r["coach"]["candidate_proposals"][0]["hypothesis"] = "second version"
    result = build_bridge(r, state, h, calendar, "2026-10-26 15:00:00")
    assert result["source_status"] == "experiment_capacity_reached"
    assert result["experiments"] == state["experiments"]


def test_missing_and_malformed_price_provenance_fail_closed(calendar):
    for proof in (None, [], "bad", {}):
        s = snapshot()
        s["predictions"][0]["coach_price_evidence"] = proof
        s["integrity_sha256"] = _snapshot_hash(s)
        state = build_bridge(coach(), initial(calendar), {"snapshots": [s]}, calendar, "2026-10-26 15:00:00")
        assert only(state)["registered_rows"] == 0
