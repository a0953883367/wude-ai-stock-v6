"""Reviewed deterministic definitions; never infer executable rules from prose.

The pinned legacy mappings were reviewed for PR #373. A changed report or proposal
cannot acquire this mapping. Future coaching can explicitly select the templates.
"""
from copy import deepcopy

STRATIFIED_TEMPLATE = "direction_strength_horizon_audit_v1"
REVIEWED_REPORT_SHA256 = "42601eada3e3d45a9d38b026bebf1bcc10603b2f97cbf9b704803d0eddc2b3ff"
REVIEWED_SOURCE_COMMIT = "8366f9ee4d3e07415eee50d52245656a6d7297a6"
REVIEW_VERSION = "saved-coach-20261024-v1"

REVIEWED_PROPOSALS = {
    "938950ff9a997b5d1b8edd560b8b5efd44143a4717bb0f143a3d70583cceab0f": {
        "execution_template": STRATIFIED_TEMPLATE,
        "readiness": "supported_prospective_stratification",
        "rationale": "The saved proposal explicitly requests strength/horizon strata against the current baseline, not a changed trading rule. Fixed bins and existing 1/2/3/5-session horizons operationalize that diagnostic prospectively.",
        "missing_fields": [],
        "unavailable_measurements": ["probability_calibration_error: strength is not a calibrated probability"],
    },
    "a5d965d3395ba26922141fedf65ce84831173984419f225af0d31a41253b5e1e": {
        "execution_template": "unsupported",
        "readiness": "blocked_missing_event_definition_and_frozen_signal",
        "rationale": "US premarket event/abnormal-volatility abstention is not the existing Taiwan gap-entry experiment. Neither an exact trigger nor its timestamped frozen input is defined.",
        "missing_fields": ["event_source_and_event_types", "event_available_at_before_forecast", "abnormal_volatility_measure_and_threshold", "confidence_reduction_or_abstention_action", "maximum_abstention_fraction", "frozen_source_signal"],
        "unavailable_measurements": [],
    },
    "cf4340da61cfd6305b35eec620bdb5cb18835d22ea4e00f0c8e50ebbbe4f906d": {
        "execution_template": "unsupported",
        "readiness": "blocked_missing_reversal_signal_definition",
        "rationale": "The saved hypothesis requires simultaneous breakout volume, sector breadth and attack volume. Existing rotation rankings and missed-strength diagnostics do not implement that conjunction.",
        "missing_fields": ["breakout_lookback_and_volume_threshold", "sector_universe_and_breadth_threshold", "attack_volume_definition_and_threshold", "simultaneity_window", "frozen_signal_availability_timestamps", "reversal_label_and_delay_reference"],
        "unavailable_measurements": [],
    },
}


def reviewed_definition(report_hash, proposal_hash):
    if report_hash != REVIEWED_REPORT_SHA256 or proposal_hash not in REVIEWED_PROPOSALS:
        return None
    return {**deepcopy(REVIEWED_PROPOSALS[proposal_hash]), "review_version": REVIEW_VERSION,
            "source_commit": REVIEWED_SOURCE_COMMIT, "report_sha256": report_hash,
            "proposal_sha256": proposal_hash}


def stratified_contract(cohort):
    return {"template": STRATIFIED_TEMPLATE, "version": 1, "cohort": cohort,
            "rule": {"track": "full_day", "horizons": [1, 2, 3, 5],
                     "strength_bins": [[0, 50], [50, 65], [65, 80], [80, 101]],
                     "action": "observe_frozen_baseline_only"},
            "admission": "before_next_official_open",
            "settlement": "verified_matching_exact_horizon_session_snapshot_v1",
            "minimum_review_sessions": 20, "minimum_rows_per_stratum": 30,
            "probability_calibration_error": "unavailable_heuristic_strength",
            "automatic_promotion": False}
