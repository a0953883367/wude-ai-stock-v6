"""Prospective, paired evidence audit. No predictions, price fetching or trading.

Consumes frozen decisions and later validated forward-ledger results; it never
reconstructs a candidate from an already-known return. Caller persists `state`.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
from typing import Any

VERSION = "EVIDENCE-ABLATION-V1"
ADDITIONS = {"events", "market", "liquidity"}
DECISIONS = {"LONG", "ABSTAIN"}


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _hash(record: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in record.items() if k != "outcome"},
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()


def build_evidence_ablation_report(records: list[dict] | None = None, previous: dict | None = None,
                                   *, generated_at: str | datetime,
                                   round_trip_cost_pct: float | None = None) -> dict:
    """Audit one evidence addition at a time, matched by stock/session/horizon.

    First invocation registers the experiment and excludes all existing IDs.
    Later invocations freeze new pairs strictly before entry. Timestamp strings
    require timezone offsets. Supply prior returned `state`, not the whole report.
    Existing forward-ledger gross return/entry-relative excursion fields are used
    without repricing; its net return field is deliberately not charged twice.
    """
    now = _time(generated_at)
    if now is None:
        raise ValueError("generated_at must include timezone")
    records = records or []
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    malformed_records = sum(not isinstance(r, dict) or any(
        not isinstance(r.get(k), (str, int, type(None))) for k in
        ("id", "experiment_id", "market", "asset_type", "symbol", "signal_session_date", "horizon_sessions"))
        for r in records)
    records = [r for r in records if isinstance(r, dict) and all(
        isinstance(r.get(k), (str, int, type(None))) for k in
        ("id", "experiment_id", "market", "asset_type", "symbol", "signal_session_date", "horizon_sessions"))]
    cost = _number(round_trip_cost_pct)
    if round_trip_cost_pct is not None and (cost is None or cost < 0):
        raise ValueError("round_trip_cost_pct must be finite and nonnegative or None")
    state = deepcopy(previous) if previous else {
        "version": VERSION, "registered_at": now.isoformat(), "cost_pct": cost,
        "excluded_ids": sorted({str(r.get("id")) for r in records if isinstance(r, dict)}),
        "pairs": {}, "experiments": {},
    }
    report = {
        "version": VERSION, "status": "insufficient", "label": "資料不足：尚無可用配對前向驗證", "shadow_only": True,
        "affects_formal_v6": False, "automatic_promotion": False,
        "accuracy_improvement": None, "probability_pct": None,
        "blocked_reasons": [], "excluded_counts": {}, "cohorts": {},
        "registered_pairs": 0, "matched_completed_pairs": 0, "state": state,
        "methodology": "prospective_paired_long_or_abstain_same_stock_session_horizon",
        "dependence_note": "Stocks and overlapping horizons are correlated; counts are not independent trials.",
        "drawdown_note": "No marked-to-market portfolio path: portfolio maximum drawdown is unavailable. Entry-relative adverse excursion is a separate diagnostic.",
        "verdict": "No efficacy or formal-promotion conclusion; fixtures only test methodology.",
    }
    cutoff = _time(state.get("registered_at"))
    if (state.get("version") != VERSION or cutoff is None or cutoff > now
            or state.get("cost_pct") != cost or not isinstance(state.get("pairs"), dict)
            or not isinstance(state.get("excluded_ids"), list)
            or not isinstance(state.get("experiments"), dict)
            or any(not isinstance(p, dict) or not isinstance(p.get("hash"), str)
                   or not isinstance(p.get("cohort"), str) for p in state.get("pairs", {}).values())):
        report["blocked_reasons"] = ["invalid_registration_or_changed_cost_policy"]
        return report
    excluded = Counter({"invalid_record": malformed_records})
    groups = defaultdict(list)
    counts = Counter(str(r.get("id")) for r in records if isinstance(r, dict))
    frozen = state["pairs"]
    # Do not silently count duplicated IDs or duplicate same-experiment pairs.
    pair_keys = Counter((r.get("experiment_id"), r.get("market"), r.get("asset_type"),
                         r.get("symbol"), r.get("signal_session_date"), r.get("horizon_sessions"))
                        for r in records if isinstance(r, dict))
    for row in records:
        if not isinstance(row, dict):
            excluded["invalid_record"] += 1
            continue
        rid = str(row.get("id") or "")
        pair_key = (row.get("experiment_id"), row.get("market"), row.get("asset_type"),
                    row.get("symbol"), row.get("signal_session_date"), row.get("horizon_sessions"))
        if not rid or counts[rid] != 1 or pair_keys[pair_key] != 1:
            excluded["duplicate_or_missing_pair_identity"] += 1
            continue
        if rid in state["excluded_ids"]:
            excluded["pre_registration_record"] += 1
            continue
        try:
            signal_date = date.fromisoformat(str(row.get("signal_session_date")))
            entry_date = date.fromisoformat(str(row.get("entry_session_date")))
        except ValueError:
            excluded["invalid_session_date"] += 1
            continue
        decision, entry, available = (_time(row.get(k)) for k in
                                      ("decision_at", "entry_at", "evidence_available_at"))
        registered = _time(row.get("rule_registered_at"))
        horizon = row.get("horizon_sessions")
        if (row.get("market") not in {"TW", "US"} or row.get("asset_type") not in {"STOCK", "ETF"}
                or not row.get("symbol") or not row.get("experiment_id")
                or row.get("added_evidence") not in ADDITIONS
                or not row.get("baseline_rule_id") or not row.get("candidate_rule_id")
                or not row.get("source_snapshot_id") or not row.get("evidence_snapshot_id")
                or row.get("baseline") not in DECISIONS or row.get("candidate") not in DECISIONS
                or not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0
                or None in (decision, entry, available, registered)
                or not registered <= cutoff < decision < entry
                or available > decision or decision > now
                or str(row.get("signal_session_date") or "") >= str(row.get("entry_session_date") or "")
                or not row.get("signal_session_date") or not row.get("entry_session_date")
                or entry.date() != entry_date or decision.date() < signal_date):
            excluded["invalid_point_in_time_pair"] += 1
            continue
        try:
            digest = _hash(row)
        except (ValueError, TypeError):
            excluded["invalid_record"] += 1
            continue
        experiment = [row.get(k) for k in ("added_evidence", "baseline_rule_id", "candidate_rule_id", "rule_registered_at")]
        manifest = state.setdefault("experiments", {}).get(row["experiment_id"])
        if manifest is not None and manifest != experiment:
            excluded["changed_experiment_rules"] += 1
            continue
        if rid not in frozen:
            if row.get("outcome") is not None or now >= entry:
                excluded["late_pair_registration"] += 1
                continue
            state["experiments"][row["experiment_id"]] = experiment
            frozen[rid] = {"hash": digest, "enrolled_at": now.isoformat(),
                           "cohort": "|".join(str(row[k]) for k in
                           ("experiment_id", "added_evidence", "market", "asset_type", "horizon_sessions"))}
        if frozen[rid]["hash"] != digest:
            excluded["changed_frozen_pair"] += 1
            continue
        key = "|".join(str(row[k]) for k in
                       ("experiment_id", "added_evidence", "market", "asset_type", "horizon_sessions"))
        outcome = row.get("outcome")
        item = {"row": row, "outcome": None}
        groups[key].append(item)
        if outcome is None:
            excluded["awaiting_forward_outcome"] += 1
            continue
        if not isinstance(outcome, dict):
            excluded["invalid_forward_outcome"] += 1
            continue
        evaluated = _time(outcome.get("evaluated_at"))
        try:
            outcome_date = date.fromisoformat(str(outcome.get("outcome_session_date")))
        except ValueError:
            excluded["invalid_forward_outcome"] += 1
            continue
        value, excursion = _number(outcome.get("close_return_pct")), _number(outcome.get("max_drawdown_pct"))
        if (outcome.get("status") != "valid" or not outcome.get("source_ledger_id")
                or evaluated is None or not entry < evaluated <= now
                or outcome_date > evaluated.date()
                or any(outcome.get(k) != row[k] for k in ("market", "symbol", "signal_session_date"))
                or outcome.get("entry_session_date") != row["entry_session_date"]
                or outcome.get("horizon_sessions") != horizon
                or str(outcome.get("outcome_session_date") or "") < row["entry_session_date"]
                or not outcome.get("outcome_session_date") or value is None or value < -100
                or excursion is None or not -100 <= excursion <= 0):
            excluded["invalid_forward_outcome"] += 1
            continue
        try:
            outcome_hash = hashlib.sha256(json.dumps(outcome, sort_keys=True, allow_nan=False).encode()).hexdigest()
        except (ValueError, TypeError):
            excluded["invalid_forward_outcome"] += 1
            continue
        if frozen[rid].get("outcome_hash", outcome_hash) != outcome_hash:
            excluded["changed_forward_outcome"] += 1
            continue
        frozen[rid]["outcome_hash"] = outcome_hash
        item["outcome"] = outcome
    excluded["missing_frozen_records"] += len(set(frozen) - set(counts))
    registered_by_cohort = Counter(item["cohort"] for item in frozen.values())
    for key in sorted(registered_by_cohort):
        items = groups[key]
        completed = [item for item in items if item["outcome"] is not None]
        n = len(completed)
        cohort = {"registered_pairs": registered_by_cohort[key], "completed_pairs": n,
                  "outcome_coverage_pct": round(100 * n / registered_by_cohort[key], 4),
                  "completed_sessions": len({i["row"]["signal_session_date"] for i in completed})}
        averages = {}
        for arm in ("baseline", "candidate"):
            active = [i for i in completed if i["row"][arm] == "LONG"]
            gross = [float(i["outcome"]["close_return_pct"]) if i["row"][arm] == "LONG" else 0.0 for i in completed]
            net = ([v - cost if i["row"][arm] == "LONG" else 0.0 for v, i in zip(gross, completed)]
                   if cost is not None else None)
            averages[arm] = sum(net) / n if net is not None and n else None
            cohort[arm] = {
                "trades": len(active), "abstentions": n - len(active),
                "trade_coverage_pct": 100 * len(active) / n if n else None,
                "gross_win_rate_pct": 100 * sum(float(i["outcome"]["close_return_pct"]) > 0 for i in active) / len(active) if active else None,
                "net_win_rate_pct": (100 * sum(float(i["outcome"]["close_return_pct"]) - cost > 0 for i in active) / len(active)
                                     if active and cost is not None else None),
                "mean_gross_return_per_opportunity_pct": sum(gross) / n if n else None,
                "mean_net_return_per_opportunity_pct": averages[arm],
                "max_drawdown_pct": None,
                "worst_entry_relative_excursion_pct": min(float(i["outcome"]["max_drawdown_pct"]) for i in active) if active else None,
            }
        cohort["paired_mean_net_delta_pct"] = (averages["candidate"] - averages["baseline"]
                                                 if all(v is not None for v in averages.values()) else None)
        report["cohorts"][key] = cohort
        report["matched_completed_pairs"] += n
    report["registered_pairs"] = len(frozen)
    report["excluded_counts"] = {k: v for k, v in sorted(excluded.items()) if v}
    if not records:
        report["blocked_reasons"].append("missing_baseline_candidate_ledger")
    if not report["matched_completed_pairs"]:
        report["blocked_reasons"].append("no_valid_matched_forward_outcomes")
    if cost is None:
        report["blocked_reasons"].append("cost_assumption_not_configured")
    if report["matched_completed_pairs"] and cost is not None:
        report["status"] = "descriptive_only"
        report["label"] = "前向配對描述統計：尚無改善或晉升結論"
    return report
