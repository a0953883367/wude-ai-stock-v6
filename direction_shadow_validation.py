"""One preregistered abstention comparison; never modifies forecast history."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from typing import Any

from performance import _metric, _snapshot_integrity, AUDIT_SCHEMA_VERSION

RULE = {"id": "direction_strength_abstention_65_v1", "horizon": "1",
        "minimum_strength": 65.0, "action": "abstain_only", "track": "full_day"}


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value))
        # Existing report timestamps use Taiwan local wall time without offset.
        return result if result.tzinfo is None else None
    except (ValueError, TypeError):
        return None


def build_direction_validation(history: dict, previous: dict, generated_at: str) -> dict:
    snapshots = history.get("snapshots") or []
    state = deepcopy(previous) if previous else {
        "registered_at": generated_at, "rule": deepcopy(RULE), "decisions": {},
        "excluded_snapshot_ids": sorted({str(s.get("id")) for s in snapshots}),
    }
    state.update({"mode": "prospective_shadow_only", "affects_formal_v6": False,
                  "automatic_promotion": False, "broker_orders": False,
                  "score_semantics": "heuristic_strength_not_probability",
                  "brier_score": None,
                  "classification_note": "direction_calibration 是未能進一步歸因的方向錯誤，並非已證實門檻失準。",
                  "metric_note": "一日方向結果按市場／資產分開；個股列彼此可能相關，交易日數不代表獨立事件數。方向報酬不是實際交易損益，未扣交易成本。",
                  "status": "collecting", "blocked_reasons": []})
    cutoff = _time(state.get("registered_at"))
    now = _time(generated_at)
    if state.get("rule") != RULE or cutoff is None or now is None or now < cutoff:
        state["status"] = "blocked"
        state["blocked_reasons"] = ["frozen_rule_or_registration_timestamp_invalid"]
        return state
    decisions = state["decisions"]
    excluded = set(state["excluded_snapshot_ids"])
    results: dict[str, list] = {}
    sessions: dict[str, set] = {}
    seen = set()
    for snapshot in snapshots:
        sid = str(snapshot.get("id") or "")
        if not sid or sid in seen:
            state["blocked_reasons"].append("duplicate_or_missing_snapshot_id")
            continue
        seen.add(sid)
        if sid in excluded:
            continue
        if snapshot.get("audit_schema_version") != AUDIT_SCHEMA_VERSION or _snapshot_integrity(snapshot) != "verified":
            state["blocked_reasons"].append(f"invalid_snapshot:{sid}")
            continue
        captured = _time(snapshot.get("captured_at"))
        if captured is None or captured <= cutoff or captured > now or sid in excluded:
            continue
        frozen_hash = snapshot["integrity_sha256"]
        for row in snapshot.get("predictions", []):
            cohort = row.get("cohort")
            symbol = row.get("symbol")
            if cohort not in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF") or not symbol or row.get("validation_eligible") is not True:
                continue
            key = f"{sid}|{symbol}"
            outcome = (row.get("outcomes") or {}).get("1")
            if key not in decisions:
                # No backfilled decision after ANY result is already visible.
                if row.get("outcomes"):
                    continue
                consensus = ((row.get("track_predictions") or {}).get("full_day") or {}).get("consensus") or {}
                direction = consensus.get("direction")
                try:
                    strength = float(consensus.get("confidence"))
                except (TypeError, ValueError):
                    continue
                if direction not in ("UP", "DOWN", "ABSTAIN") or not math.isfinite(strength) or not 0 <= strength <= 100:
                    continue
                decisions[key] = {"registered_at": generated_at, "snapshot_hash": frozen_hash,
                                  "baseline": direction, "candidate": direction if strength >= 65 else "ABSTAIN",
                                  "strength": strength, "cohort": cohort}
            decision = decisions[key]
            if decision["snapshot_hash"] != frozen_hash:
                state["blocked_reasons"].append(f"forecast_changed:{key}")
                continue
            if not isinstance(outcome, dict):
                continue
            evaluated = str(outcome.get("evaluated_session_date") or "")
            if not evaluated or evaluated <= str(snapshot.get("session_date") or "") or evaluated > generated_at[:10]:
                continue
            try:
                value = float(outcome["close_to_close_return_pct"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            results.setdefault(cohort, []).append((value, decision))
            sessions.setdefault(cohort, set()).add(evaluated)
    def metric(records, eligible):
        result = _metric(records, eligible)
        if not records:
            for key in ("win_rate_pct", "avg_return_pct", "worst_return_pct", "lowest_actual_return_pct", "highest_actual_return_pct"):
                result[key] = None
        return result

    state["cohorts"] = {}
    for cohort in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        rows = results.get(cohort, [])
        state["cohorts"][cohort] = {
            "completed_rows": len(rows), "completed_sessions": len(sessions.get(cohort, set())),
            "baseline": metric([(v, d["baseline"]) for v, d in rows if d["baseline"] != "ABSTAIN"], len(rows)),
            "candidate": metric([(v, d["candidate"]) for v, d in rows if d["candidate"] != "ABSTAIN"], len(rows)),
            "newly_abstained_rows": sum(d["baseline"] != "ABSTAIN" and d["candidate"] == "ABSTAIN" for _, d in rows),
        }
    state["registered_rows"] = len(decisions)
    state["verdict"] = "尚無晉升或改善結論；維持20／60日門檻，結果只供人工審查。"
    if state["blocked_reasons"]:
        state["status"] = "collecting_with_exclusions"
    return state
