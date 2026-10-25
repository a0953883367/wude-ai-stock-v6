"""Deterministic, prospective execution of explicitly selected coach templates.

No model client, dynamic code, trading interface, or promotion path lives here.
Prose is retained as provenance only. Existing experiment results are never imported.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from direction_shadow_validation import RULE
from market_calendar import MARKET_ZONES, OfficialMarketCalendar
from performance import AUDIT_SCHEMA_VERSION, _metric, _snapshot_integrity
from weekly_shadow_coach import COHORTS, LOCKED_POLICY, RULE_CATALOG

SCHEMA = "wude.shadow_coach_bridge.v1"
TEMPLATE = "direction_strength_abstention_65_v1"
MAX_EXPERIMENTS = 312  # 104 weekly reports, at most three proposals each; never prune.
TAIPEI = ZoneInfo("Asia/Taipei")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def stamp(value):
    dt = datetime.fromisoformat(str(value))
    return dt.replace(tzinfo=TAIPEI) if dt.tzinfo is None else dt


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _contract(proposal):
    if proposal.get("execution_template") != TEMPLATE or proposal.get("rule_id") != "direction_calibration":
        return {"template": proposal.get("execution_template"), "version": 1,
                "cohort": proposal["cohort"], "executable": False}
    return {"template": TEMPLATE, "version": 1, "rule": deepcopy(RULE),
            "cohort": proposal["cohort"], "admission": "before_next_official_open",
            "settlement": "verified_matching_next_session_snapshot_v1"}


def _source_error(report, now):
    if (not isinstance(report, dict) or not isinstance(report.get("api", {}), dict)
            or not isinstance(report.get("policy", {}), dict)
            or not isinstance(report.get("coach", {}), dict)):
        return "invalid_coach_report_shape"
    if (report.get("schema") != "wude.weekly_shadow_coach.v1"
            or report.get("status") != "ok" or report.get("mode") != "openai"
            or (report.get("api") or {}).get("called") is not True
            or not (report.get("api") or {}).get("response_id")):
        return "no_successful_openai_coach_report"
    if any((report.get("policy") or {}).get(k) is not v for k, v in LOCKED_POLICY.items()):
        return "coach_policy_invalid"
    try:
        if stamp(report["generated_at"]) > now:
            return "future_coach_report"
    except (KeyError, TypeError, ValueError):
        return "invalid_coach_timestamp"
    proposals = (report.get("coach") or {}).get("candidate_proposals")
    if not isinstance(proposals, list) or len(proposals) > 3:
        return "invalid_proposal_batch"
    for p in proposals:
        if (not isinstance(p, dict) or p.get("rule_id") not in RULE_CATALOG
                or p.get("cohort") not in COHORTS
                or any(not isinstance(p.get(k), str) or not p[k].strip()
                       for k in ("hypothesis", "measurement", "risk_control"))):
            return "invalid_structured_proposal"
    return None


def _next_session(calendar, market, origin):
    end = (datetime.fromisoformat(origin) + timedelta(days=32)).date().isoformat()
    window = calendar.lookup(market, origin, end)
    if window.get("status") != "verified" or not window.get("sessions"):
        raise ValueError("verified_calendar_unavailable")
    target = window["sessions"][0]
    info = calendar.session_status(market, target)
    opening = info.get("open") if market == "US" else "09:00"
    if not opening:
        raise ValueError("official_open_missing")
    opens = datetime.fromisoformat(f"{target}T{opening}").replace(tzinfo=MARKET_ZONES[market])
    return target, opens


def _valid_snapshot(snapshot, calendar, now):
    if (snapshot.get("audit_schema_version") != AUDIT_SCHEMA_VERSION
            or _snapshot_integrity(snapshot) != "verified"):
        raise ValueError("snapshot_integrity_invalid")
    market, origin = snapshot.get("market"), snapshot.get("session_date")
    if market not in MARKET_ZONES:
        raise ValueError("invalid_market")
    captured = stamp(snapshot.get("captured_at"))
    info = calendar.session_status(market, origin)
    if (not info.get("is_session") or not str(info.get("status")).startswith("verified")
            or captured > now or not calendar.session_complete(market, origin, at_epoch=captured.timestamp())):
        raise ValueError("source_session_not_verified_complete")
    rows = snapshot.get("predictions")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("invalid_prediction_rows")
    symbols = [r.get("symbol") for r in rows]
    if any(not s for s in symbols) or len(symbols) != len(set(symbols)):
        raise ValueError("duplicate_or_missing_symbol")
    return market, origin, captured


def _valid_row(row, market, session):
    proof = row.get("coach_price_evidence") or {}
    if not isinstance(proof, dict):
        return False
    prefix = "tw_official" if market == "TW" else "us_daily"
    source = proof.get("tw_price_source" if market == "TW" else "us_daily_source")
    unit = proof.get("tw_price_unit" if market == "TW" else "us_daily_price_unit")
    prices = [proof.get(f"official_{key}_price") for key in ("open", "high", "low", "close")]
    if not all(finite(v) and v > 0 for v in prices):
        return False
    opening, high, low, close = prices
    adjusted = proof.get("official_adjusted_close_price")
    # No corporate-action adjustment inferred from unrelated sources.
    if adjusted is not None and (not finite(adjusted) or adjusted != close):
        return False
    return (row.get("validation_eligible") is True
            and row.get("official_session_date") == session
            and row.get("cohort") in (f"{market}_STOCK", f"{market}_ETF")
            and finite(row.get("official_price")) and row["official_price"] > 0
            and abs(row["official_price"] - close) < 0.00011
            and high >= max(opening, low, close) and low <= min(opening, high, close)
            and finite(proof.get("official_volume")) and proof["official_volume"] >= 0
            and proof.get("source_daily_ohlcv_complete") is True
            and proof.get("source_daily_ohlcv_session_date") == session
            and proof.get(prefix + "_price_available") is True
            and proof.get(prefix + "_session_date") == session
            and source in ({"TWSE OpenAPI", "TPEx OpenAPI"} if market == "TW" else {"Yahoo Finance daily bars"})
            and unit == ("TWD/shares" if market == "TW" else "USD/shares"))


def _execute(experiment, snapshots, calendar, now):
    """Register only unseen future decisions; settle only with a matching frozen close."""
    counts = Counter()
    decisions = experiment["decisions"]
    verified_decisions = 0
    registered = stamp(experiment["registered_at"])
    ids = Counter(str(s.get("id") or "") for s in snapshots if isinstance(s, dict))
    valid = []
    for s in snapshots:
        try:
            if not isinstance(s, dict) or not s.get("id") or ids[str(s["id"])] != 1:
                raise ValueError("duplicate_or_missing_snapshot_id")
            market, origin, captured = _valid_snapshot(s, calendar, now)
            valid.append((s, market, origin, captured))
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as exc:
            counts[str(exc)] += 1
    paired = {}
    for s, market, origin, captured in valid:
        for row in s["predictions"]:
            if _valid_row(row, market, origin):
                paired.setdefault((market, origin, row["symbol"]), []).append((s, row, captured))
    completed = {c: [] for c in COHORTS if c != "ALL"}
    sessions = {c: set() for c in completed}
    for s, market, origin, captured in valid:
        if s["id"] in experiment["excluded_snapshot_ids"] or captured <= registered:
            continue
        for row in s["predictions"]:
            if experiment["proposal"]["cohort"] not in ("ALL", row.get("cohort")):
                continue
            key = f'{s["id"]}|{row["symbol"]}'
            try:
                if not _valid_row(row, market, origin):
                    raise ValueError("frozen_source_contract_invalid")
                target, opens = _next_session(calendar, market, origin)
                if key not in decisions:
                    if row.get("outcomes") or now >= opens or captured >= opens:
                        raise ValueError("late_decision_or_visible_outcome")
                    consensus = ((row.get("track_predictions") or {}).get("full_day") or {}).get("consensus") or {}
                    strength, baseline = consensus.get("confidence"), consensus.get("direction")
                    if not finite(strength) or not 0 <= strength <= 100 or baseline not in ("UP", "DOWN", "ABSTAIN"):
                        raise ValueError("invalid_frozen_direction")
                    decision = {"registered_at": now.isoformat(), "snapshot_hash": s["integrity_sha256"],
                                "target_session": target, "target_open": opens.isoformat(),
                                "cohort": row["cohort"], "strength": strength, "baseline": baseline,
                                "candidate": baseline if strength >= RULE["minimum_strength"] else "ABSTAIN"}
                    decisions[key] = {"receipt": decision, "receipt_sha256": digest(decision)}
                record = decisions[key]
                if record.get("quarantined"):
                    raise ValueError(record["quarantined"])
                d = record["receipt"]
                if (record["receipt_sha256"] != digest(d) or d["snapshot_hash"] != s["integrity_sha256"]
                        or d["target_session"] != target or d["target_open"] != opens.isoformat()
                        or stamp(d["registered_at"]) >= opens):
                    record["quarantined"] = "frozen_decision_or_calendar_changed"
                    raise ValueError(record["quarantined"])
                verified_decisions += 1
                outcome = (row.get("outcomes") or {}).get("1")
                if not outcome:
                    continue
                if (outcome.get("evaluated_session_date") != target
                        or not calendar.session_complete(market, target, at_epoch=now.timestamp())):
                    raise ValueError("outcome_session_invalid_or_incomplete")
                proofs = paired.get((market, target, row["symbol"]), [])
                if len(proofs) != 1:
                    raise ValueError("missing_or_ambiguous_frozen_target_price")
                ps, pr, pc = proofs[0]
                if pc.astimezone(MARKET_ZONES[market]).date().isoformat() != target:
                    raise ValueError("late_target_price_capture")
                expected = round((pr["official_price"] / row["official_price"] - 1) * 100, 4)
                actual = outcome.get("close_to_close_return_pct")
                if not finite(actual) or abs(actual - expected) > 0.00011:
                    raise ValueError("outcome_price_mismatch")
                receipt = {"outcome": deepcopy(outcome), "target_snapshot_hash": ps["integrity_sha256"]}
                if "outcome_receipt" in record and record["outcome_receipt"] != receipt:
                    record["quarantined"] = "outcome_revision_quarantined"
                    raise ValueError(record["quarantined"])
                record.setdefault("outcome_receipt", receipt)
                completed[d["cohort"]].append((actual, d))
                sessions[d["cohort"]].add(target)
            except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as exc:
                counts[str(exc)] += 1
    experiment["registered_rows"] = len(decisions)
    experiment["completed_rows"] = sum(map(len, completed.values()))
    experiment["cohorts"] = {}
    for cohort, rows in completed.items():
        result = {"completed_rows": len(rows), "completed_sessions": len(sessions[cohort])}
        for arm in ("baseline", "candidate"):
            values = [(v, d[arm]) for v, d in rows if d[arm] != "ABSTAIN"]
            result[arm] = _metric(values, len(rows))
            if not values:
                for name in ("win_rate_pct", "avg_return_pct", "worst_return_pct", "lowest_actual_return_pct", "highest_actual_return_pct"):
                    result[arm][name] = None
        result["newly_abstained_rows"] = sum(d["baseline"] != "ABSTAIN" and d["candidate"] == "ABSTAIN" for _, d in rows)
        experiment["cohorts"][cohort] = result
    experiment["gate_counts"] = dict(sorted(counts.items()))
    experiment["status"] = "collecting_with_exclusions" if counts else "collecting"
    experiment["execution_verified"] = bool(verified_decisions)
    experiment["outcome_verified"] = bool(experiment["completed_rows"])


def build_bridge(report, previous, history, calendar, generated_at):
    now = stamp(generated_at)
    if not isinstance(previous, dict) or (previous and (previous.get("schema") != SCHEMA or not isinstance(previous.get("experiments"), dict))):
        raise ValueError("invalid_bridge_state_preserve_existing")
    if not isinstance(history, dict) or not isinstance(history.get("snapshots", []), list):
        raise ValueError("invalid_prediction_history_shape")
    for entry in previous.get("experiments", {}).values():
        if not isinstance(entry, dict) or not isinstance(entry.get("decisions"), dict):
            raise ValueError("invalid_experiment_state_preserve_existing")
    state = deepcopy(previous) if previous else {"schema": SCHEMA, "experiments": {}}
    if state.get("updated_at") and now < stamp(state["updated_at"]):
        raise ValueError("bridge_clock_regressed")
    state.update({"updated_at": now.isoformat(), "policy": dict(LOCKED_POLICY, automatic_promotion=False),
                  "effectiveness": "unproven", "integration": "deterministic_template_bridge_v1",
                  "metric_limitations": "One-session directional comparison, not probability calibration or trading P&L; rows are correlated. No automatic promotion."})
    error = _source_error(report, now)
    state["source_status"] = error or "accepted"
    if not error:
        report_hash = digest(report)
        for p in report["coach"]["candidate_proposals"]:
            contract = _contract(p)
            identity = digest({"proposal": p, "contract": contract})
            experiments = state["experiments"]
            if identity in experiments:
                continue
            if len(experiments) >= MAX_EXPERIMENTS:
                state["source_status"] = "experiment_capacity_reached"
                break
            template = p.get("execution_template")
            status = ("blocked_missing_execution_template" if not template else
                      "ready" if template == TEMPLATE and p["rule_id"] == "direction_calibration" else
                      "unsupported_execution_template")
            frozen = {"proposal": deepcopy(p), "contract": contract,
                      "registered_at": now.isoformat(),
                      "provenance": {"report": "weekly_shadow_coach.json", "report_sha256": report_hash,
                                     "generated_at": report["generated_at"], "api": deepcopy(report["api"])},
                      "excluded_snapshot_ids": sorted({str(s.get("id")) for s in history.get("snapshots", []) if isinstance(s, dict)}),
                      "initial_status": status}
            experiments[identity] = {**frozen, "frozen_sha256": digest(frozen), "decisions": {},
                                     "registered_rows": 0, "completed_rows": 0, "status": status,
                                     "execution_verified": False, "outcome_verified": False}
    for identity, e in state["experiments"].items():
        if not isinstance(e.get("proposal"), dict) or not isinstance(e.get("contract"), dict):
            raise ValueError("invalid_frozen_contract_preserve_existing")
        frozen = {k: e[k] for k in ("proposal", "contract", "registered_at", "provenance", "excluded_snapshot_ids", "initial_status")}
        if (digest(frozen) != e["frozen_sha256"] or e["contract"] != _contract(e["proposal"])
                or identity != digest({"proposal": e["proposal"], "contract": e["contract"]})):
            e.update(status="blocked_frozen_contract_changed", execution_verified=False, outcome_verified=False, completed_rows=0, cohorts={})
            continue
        if e["initial_status"] == "ready":
            _execute(e, history.get("snapshots", []), calendar, now)
    state["all_candidates_execution_verified"] = bool(state["experiments"]) and all(e["execution_verified"] for e in state["experiments"].values())
    return state


def update_bridge(reports_dir, history, generated_at):
    """Single atomic shadow artifact, called by the existing daily learning producer."""
    path = reports_dir / "shadow_coach_experiments.json"
    # A corrupt file is an error, never permission to reset frozen state.
    previous = json.loads(path.read_text()) if path.exists() else {}
    coach_path = reports_dir / "weekly_shadow_coach.json"
    report = json.loads(coach_path.read_text()) if coach_path.exists() else {}
    calendar = OfficialMarketCalendar(reports_dir / "official_market_calendar.json", auto_refresh=False, allow_network=False)
    result = build_bridge(report, previous, history, calendar, generated_at)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)
    return result
