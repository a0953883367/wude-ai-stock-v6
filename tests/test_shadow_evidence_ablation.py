"""Synthetic methodology fixtures, never evidence of production efficacy."""
from copy import deepcopy

import pytest

from shadow_evidence_ablation import build_evidence_ablation_report as build

REGISTER = "2026-10-01T00:00:00+00:00"
ENROLL = "2026-10-02T21:00:00+00:00"
EVALUATE = "2026-10-04T22:00:00+00:00"


def pair(**changes):
    result = dict(id="one", experiment_id="events-v1", added_evidence="events",
                  baseline_rule_id="baseline-v1", candidate_rule_id="events-gate-v1",
                  rule_registered_at=REGISTER, market="US", asset_type="STOCK", symbol="A",
                  signal_session_date="2026-10-02", entry_session_date="2026-10-03",
                  decision_at="2026-10-02T20:00:00+00:00", entry_at="2026-10-03T13:30:00+00:00",
                  evidence_available_at="2026-10-02T19:00:00+00:00", source_snapshot_id="source-1",
                  evidence_snapshot_id="event-1", horizon_sessions=1, baseline="LONG", candidate="ABSTAIN")
    result.update(changes)
    return result


def result(value=-4):
    return dict(status="valid", source_ledger_id="forward:US:A:1", evaluated_at=EVALUATE,
                market="US", symbol="A", signal_session_date="2026-10-02",
                entry_session_date="2026-10-03", outcome_session_date="2026-10-03",
                horizon_sessions=1, close_return_pct=value, max_drawdown_pct=-6)


def initial(cost=0.2, records=None):
    return build(records, generated_at=REGISTER, round_trip_cost_pct=cost)["state"]


def enroll(rows=None, cost=0.2):
    return build(rows or [pair()], initial(cost), generated_at=ENROLL, round_trip_cost_pct=cost)["state"]


def evaluate(rows, state=None, cost=0.2):
    return build(rows, state or enroll(cost=cost), generated_at=EVALUATE, round_trip_cost_pct=cost)


def test_empty_production_report_is_explicitly_insufficient():
    report = build(generated_at=REGISTER)
    assert report["status"] == "insufficient"
    assert report["matched_completed_pairs"] == 0
    assert "missing_baseline_candidate_ledger" in report["blocked_reasons"]
    assert report["accuracy_improvement"] is None
    assert not report["affects_formal_v6"] and not report["automatic_promotion"]


def test_first_run_excludes_existing_pairs_and_late_results():
    row = pair(outcome=result())
    report = evaluate([row], initial(records=[row]))
    assert report["registered_pairs"] == 0
    assert report["excluded_counts"]["pre_registration_record"] == 1
    report = build([row], initial(), generated_at=EVALUATE, round_trip_cost_pct=0.2)
    assert report["excluded_counts"]["late_pair_registration"] == 1


def test_paired_abstention_cost_and_coverage_are_on_same_opportunity_set():
    row = pair(outcome=result())
    untouched = deepcopy(row)
    report = evaluate([row])
    cohort = next(iter(report["cohorts"].values()))
    assert cohort["completed_pairs"] == 1
    assert cohort["baseline"]["mean_net_return_per_opportunity_pct"] == -4.2
    assert cohort["candidate"]["mean_net_return_per_opportunity_pct"] == 0
    assert cohort["candidate"]["abstentions"] == 1
    assert cohort["candidate"]["gross_win_rate_pct"] is None
    assert cohort["paired_mean_net_delta_pct"] == 4.2
    assert cohort["baseline"]["max_drawdown_pct"] is None
    assert cohort["baseline"]["worst_entry_relative_excursion_pct"] == -6
    assert row == untouched
    assert report["status"] == "descriptive_only"


def test_cost_is_not_fabricated_and_cannot_change_after_registration():
    report = evaluate([pair(outcome=result())], cost=None)
    cohort = next(iter(report["cohorts"].values()))
    assert cohort["baseline"]["mean_net_return_per_opportunity_pct"] is None
    assert report["status"] == "insufficient"
    assert "cost_assumption_not_configured" in report["blocked_reasons"]
    changed = evaluate([pair()], enroll(), cost=0.3)
    assert changed["blocked_reasons"] == ["invalid_registration_or_changed_cost_policy"]


@pytest.mark.parametrize("changes", [
    {"evidence_available_at": "2026-10-03T00:00:00+00:00"},
    {"decision_at": "2026-10-02T20:00:00"},
    {"horizon_sessions": 0}, {"baseline": "DOWN"},
    {"source_snapshot_id": ""}, {"entry_session_date": "2026-10-02"},
    {"rule_registered_at": ENROLL}, {"signal_session_date": "bad"},
])
def test_lookahead_and_invalid_inputs_never_enroll(changes):
    report = build([pair(**changes)], initial(), generated_at=ENROLL, round_trip_cost_pct=0.2)
    assert report["registered_pairs"] == 0


def test_mutated_frozen_decisions_and_duplicate_pairs_fail_closed():
    report = evaluate([pair(candidate="LONG", outcome=result())])
    assert report["excluded_counts"]["changed_frozen_pair"] == 1
    report = evaluate([pair(), pair(id="duplicate")])
    assert report["matched_completed_pairs"] == 0
    assert report["excluded_counts"]["duplicate_or_missing_pair_identity"] == 2


@pytest.mark.parametrize("changes", [
    {"status": "data_incomplete"}, {"horizon_sessions": 5},
    {"symbol": "WRONG"}, {"market": "TW"}, {"signal_session_date": "2026-10-01"},
    {"close_return_pct": float("nan")}, {"max_drawdown_pct": None},
    {"evaluated_at": "2026-10-05T00:00:00+00:00"},
    {"outcome_session_date": "2026-10-05"}, {"source_ledger_id": ""},
])
def test_invalid_outcomes_do_not_become_matched_results(changes):
    outcome = result()
    outcome.update(changes)
    report = evaluate([pair(outcome=outcome)])
    assert report["matched_completed_pairs"] == 0
    assert report["excluded_counts"]["invalid_forward_outcome"] == 1


def test_repeated_runs_are_idempotent_and_outcome_revision_is_explicit():
    row = pair(outcome=result())
    first = evaluate([row])
    second = evaluate([row], first["state"])
    assert first["cohorts"] == second["cohorts"]
    row["outcome"]["close_return_pct"] = 8
    third = evaluate([row], first["state"])
    assert third["excluded_counts"]["changed_forward_outcome"] == 1


def test_markets_assets_and_horizons_never_pool_and_missing_outcomes_reduce_coverage():
    rows = [pair(), pair(id="two", symbol="B"), pair(id="tw", market="TW"),
            pair(id="etf", asset_type="ETF"), pair(id="five", horizon_sessions=5)]
    state = enroll(rows)
    rows[0]["outcome"] = result()
    rows[2]["outcome"] = result()
    rows[2]["outcome"]["market"] = "TW"
    report = evaluate(rows, state)
    assert len(report["cohorts"]) == 4
    us = report["cohorts"]["events-v1|events|US|STOCK|1"]
    assert us["outcome_coverage_pct"] == 50
    assert us["completed_sessions"] == 1
    assert report["matched_completed_pairs"] == 2


def test_rule_changes_cannot_mix_in_same_experiment():
    state = enroll()
    row = pair(id="two", symbol="B", candidate_rule_id="changed")
    report = build([row], state, generated_at=ENROLL, round_trip_cost_pct=0.2)
    assert report["excluded_counts"]["changed_experiment_rules"] == 1


def test_invalid_costs_and_naive_now_raise():
    with pytest.raises(ValueError):
        build(generated_at="2026-10-01")
    for cost in (-1, float("nan"), True):
        with pytest.raises(ValueError):
            initial(cost)


def test_disappearing_or_invalidated_pairs_cannot_inflate_coverage():
    state = enroll([pair(), pair(id="two", symbol="B")])
    report = evaluate([pair(outcome=result())], state)
    cohort = next(iter(report["cohorts"].values()))
    assert cohort["registered_pairs"] == 2
    assert cohort["outcome_coverage_pct"] == 50
    assert report["excluded_counts"]["missing_frozen_records"] == 1


def test_real_trade_plan_report_exposes_insufficient_summary_without_private_state(tmp_path):
    from datetime import datetime
    from trade_plan_shadow import build_trade_plan_report
    report = build_trade_plan_report(tmp_path, now=datetime.fromisoformat(REGISTER))
    audit = report["evidence_ablation"]
    assert audit["status"] == "insufficient"
    assert audit["matched_completed_pairs"] == 0
    assert "state" not in audit
    assert "missing_baseline_candidate_ledger" in audit["blocked_reasons"]


def test_bad_record_identity_does_not_crash_report():
    report = build([pair(id=[]), None], initial(), generated_at=ENROLL, round_trip_cost_pct=0.2)
    assert report["excluded_counts"]["invalid_record"] == 2
