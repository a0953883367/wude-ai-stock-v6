"""Build a read-only shadow trade plan from the Central AI decision hub.

This module does not change formal V6 scores, rankings, weights, or broker state.
It converts already-published decision-hub levels into a compact execution plan:
entry range, signal validity window, stop, staged exits, and holding horizon.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_calendar import OfficialMarketCalendar
from shadow_stock_conclusion import build_conclusion, observed_by
from shadow_evidence_ablation import build_evidence_ablation_report

SCHEMA_VERSION = 4
MODEL_VERSION = "TRADE-PLAN-SHADOW-V4"

HORIZON_POLICY = {
    "short": {
        "label": "1～5日",
        "buy_window_base": 2,
        "buy_window_min": 1,
        "buy_window_max": 3,
        "max_hold_sessions": 5,
        "min_stop_pct": 1.0,
        "downside_stop_factor": 0.35,
        "max_stop_floor_pct": 5.0,
    },
    "medium": {
        "label": "45日",
        "buy_window_base": 5,
        "buy_window_min": 3,
        "buy_window_max": 7,
        "max_hold_sessions": 45,
        "min_stop_pct": 2.0,
        "downside_stop_factor": 0.30,
        "max_stop_floor_pct": 10.0,
    },
    "long": {
        "label": "約6個月",
        "buy_window_base": 8,
        "buy_window_min": 5,
        "buy_window_max": 10,
        "max_hold_sessions": 126,
        "min_stop_pct": 4.0,
        "downside_stop_factor": 0.30,
        "max_stop_floor_pct": 15.0,
    },
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _ratio_quality(value: Any) -> float | None:
    text = str(value or "")
    if "/" not in text:
        return None
    try:
        have, total = text.split("/", 1)
        have_n, total_n = float(have), float(total)
    except (TypeError, ValueError):
        return None
    if total_n <= 0:
        return None
    return max(0.0, min(100.0, have_n / total_n * 100.0))


def _round_price(value: Any) -> float | None:
    n = _number(value)
    if n is None:
        return None
    if abs(n) >= 1000:
        digits = 1
    elif abs(n) >= 100:
        digits = 2
    elif abs(n) >= 10:
        digits = 2
    else:
        digits = 3
    return round(n, digits)


def _rr(entry: float | None, stop: float | None, target: float | None) -> float | None:
    if entry is None or stop is None or target is None:
        return None
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 2)


def _stop_floor_pct(horizon: str, downside_risk_pct: float | None) -> float:
    policy = HORIZON_POLICY[horizon]
    base = float(policy["min_stop_pct"])
    downside = max(0.0, float(downside_risk_pct or 0.0))
    dynamic = downside * float(policy["downside_stop_factor"])
    return round(min(float(policy["max_stop_floor_pct"]), max(base, dynamic)), 2)


def _stop_geometry(
    horizon: str,
    entry_mid: float | None,
    source_stop: float | None,
    downside_risk_pct: float | None,
) -> tuple[float | None, float | None, bool, float | None]:
    """Validate stop distance without silently widening the user's loss limit."""
    if entry_mid is None or entry_mid <= 0 or source_stop is None:
        return source_stop, None, False, None
    floor_pct = _stop_floor_pct(horizon, downside_risk_pct)
    reference_floor = entry_mid * (1.0 - floor_pct / 100.0)
    if source_stop >= entry_mid:
        return source_stop, floor_pct, True, reference_floor
    actual_distance_pct = (entry_mid - source_stop) / entry_mid * 100.0
    too_tight = actual_distance_pct + 1e-9 < floor_pct
    return source_stop, floor_pct, too_tight, reference_floor


def _plan_quality(
    *,
    entry_mid: float | None,
    stop: float | None,
    target1: float | None,
    reward_risk_1: float | None,
    data_quality_pct: float,
    score: float,
    confidence: float,
    stop_too_tight: bool = False,
) -> dict[str, Any]:
    if entry_mid is None or stop is None or target1 is None:
        return {"code": "incomplete", "label": "計畫資料不足", "entry_eligible": False}
    if stop >= entry_mid or target1 <= entry_mid:
        return {"code": "invalid_geometry", "label": "價位結構異常", "entry_eligible": False}
    if stop_too_tight:
        return {"code": "stop_too_tight", "label": "停損距離過窄，先重算", "entry_eligible": False}
    if reward_risk_1 is None or reward_risk_1 < 1.2:
        return {"code": "poor_rr", "label": "報酬風險比不足", "entry_eligible": False}
    if data_quality_pct < 50:
        return {"code": "low_data_quality", "label": "資料品質不足", "entry_eligible": False}
    if reward_risk_1 >= 2.0 and data_quality_pct >= 70 and score >= 65 and confidence >= 65:
        return {"code": "strong", "label": "計畫品質較佳", "entry_eligible": True}
    if reward_risk_1 >= 1.5 and data_quality_pct >= 60:
        return {"code": "acceptable", "label": "計畫品質合格", "entry_eligible": True}
    return {"code": "caution", "label": "計畫可觀察，仍需確認", "entry_eligible": False}


def _sell_split(score: float, confidence: float, target2: float | None) -> dict[str, int]:
    if target2 is None:
        return {"target1_pct": 50, "target2_pct": 0, "runner_pct": 50}
    if score >= 72 and confidence >= 70:
        return {"target1_pct": 25, "target2_pct": 25, "runner_pct": 50}
    if score >= 60 and confidence >= 60:
        return {"target1_pct": 30, "target2_pct": 30, "runner_pct": 40}
    return {"target1_pct": 40, "target2_pct": 40, "runner_pct": 20}


def _buy_window(
    horizon: str,
    *,
    recommendation: str,
    score: float,
    confidence: float,
    data_quality_pct: float,
    chase_risk_points: float,
    unresolved_conflicts: int,
) -> int:
    if recommendation not in {"can_scale", "wait_pullback"}:
        return 0
    policy = HORIZON_POLICY[horizon]
    sessions = int(policy["buy_window_base"])
    if score >= 72 and confidence >= 70 and data_quality_pct >= 70:
        sessions += 1
    if confidence < 55 or data_quality_pct < 55 or chase_risk_points >= 5 or unresolved_conflicts > 0:
        sessions -= 1
    return max(int(policy["buy_window_min"]), min(int(policy["buy_window_max"]), sessions))


def _no_buy_reason(row: dict[str, Any], plan: dict[str, Any], price: float | None) -> str | None:
    recommendation = str(plan.get("recommendation") or "")
    execution = plan.get("execution") if isinstance(plan.get("execution"), dict) else {}
    code = str(execution.get("code") or "")
    if recommendation == "data_insufficient":
        return "資料不足，先不建立買進計畫"
    if recommendation == "avoid":
        return "中央風險判斷不合格，暫不進場"
    if row.get("risk_blocks"):
        return "存在風險阻擋，等待解除後重算"
    if code == "no_chase":
        return str(execution.get("reason") or "現價高於買進區，不追價")
    if code == "wait_stabilize":
        return str(execution.get("reason") or "現價低於買進區，等待止穩")
    if recommendation == "wait_pullback":
        return str(execution.get("reason") or "中央結論仍是等待買點／確認，尚未直接可買")
    if recommendation == "watch":
        return "目前僅列觀察，尚未達到可買條件"
    if price is None:
        return "缺少可驗證現價"
    return None


def _plan_for_horizon(row: dict[str, Any], horizon: str) -> dict[str, Any]:
    plan = ((row.get("horizons") or {}).get(horizon) or {})
    prediction = ((row.get("prediction_engine") or {}).get("horizons") or {})
    prediction_key = {"short": "UP_5D", "medium": "UP_45D", "long": "UP_126D"}[horizon]
    forecast = prediction.get(prediction_key) if isinstance(prediction.get(prediction_key), dict) else {}

    price = _number(row.get("price"))
    entry_low = _number(plan.get("entry_low"))
    entry_high = _number(plan.get("entry_high"))
    stop = _number(plan.get("stop"))
    target1 = _number(plan.get("target1"))
    target2 = _number(plan.get("target2"))
    score = _number(plan.get("score"), 0.0) or 0.0
    confidence = _number(plan.get("confidence"), 0.0) or 0.0

    quality_candidates = [
        _ratio_quality(row.get("data_quality")),
        _number(forecast.get("data_quality_pct")),
        _number((row.get("next_session_prediction") or {}).get("data_quality_pct")),
    ]
    quality_values = [v for v in quality_candidates if v is not None]
    data_quality_pct = min(quality_values) if quality_values else 0.0
    chase_risk = _number(forecast.get("chase_risk_points"), 0.0) or 0.0
    unresolved = int(row.get("unresolved_conflict_count") or 0)
    recommendation = str(plan.get("recommendation") or "data_insufficient")

    buy_window = _buy_window(
        horizon,
        recommendation=recommendation,
        score=score,
        confidence=confidence,
        data_quality_pct=data_quality_pct,
        chase_risk_points=chase_risk,
        unresolved_conflicts=unresolved,
    )
    entry_mid = None
    if entry_low is not None and entry_high is not None:
        entry_mid = (entry_low + entry_high) / 2.0
    downside_risk = _number(forecast.get("downside_risk_pct"))
    effective_stop, stop_floor_pct, stop_too_tight, reference_stop_floor = _stop_geometry(
        horizon, entry_mid, stop, downside_risk
    )
    rr1 = None if stop_too_tight else _rr(entry_mid, effective_stop, target1)
    rr2 = None if stop_too_tight else _rr(entry_mid, effective_stop, target2)
    quality = _plan_quality(
        entry_mid=entry_mid,
        stop=effective_stop,
        target1=target1,
        reward_risk_1=rr1,
        data_quality_pct=data_quality_pct,
        score=score,
        confidence=confidence,
        stop_too_tight=stop_too_tight,
    )
    split = _sell_split(score, confidence, target2)
    no_buy = _no_buy_reason(row, plan, price)
    if recommendation == "can_scale" and not quality["entry_eligible"] and no_buy is None:
        no_buy = quality["label"]

    active_entry = recommendation in {"can_scale", "wait_pullback"} and entry_low is not None and entry_high is not None
    if buy_window <= 0:
        validity_label = "目前無有效買進期限"
    else:
        validity_label = f"未來 {buy_window} 個有效交易日；逾期必須重算"

    return {
        "horizon": horizon,
        "label": HORIZON_POLICY[horizon]["label"],
        "recommendation": recommendation,
        "action": plan.get("action"),
        "score": round(score, 1),
        "confidence": round(confidence, 1),
        "data_quality_pct": round(data_quality_pct, 1),
        "active_entry_plan": active_entry,
        "buy_window_sessions": buy_window,
        "buy_window_label": validity_label,
        "max_hold_sessions": int(HORIZON_POLICY[horizon]["max_hold_sessions"]),
        "entry_low": _round_price(entry_low),
        "entry_high": _round_price(entry_high),
        "do_not_chase_above": _round_price(entry_high),
        "source_stop": _round_price(stop),
        "stop": _round_price(effective_stop),
        "stop_floor_pct": stop_floor_pct,
        "reference_stop_floor": _round_price(reference_stop_floor),
        "stop_too_tight": stop_too_tight,
        "stop_sell_pct": 100 if effective_stop is not None else 0,
        "target1": _round_price(target1),
        "target2": _round_price(target2),
        **split,
        "entry_mid": _round_price(entry_mid),
        "reward_risk_1": rr1,
        "reward_risk_2": rr2,
        "plan_quality": quality,
        "forecast_probability_pct": _number(forecast.get("probability_pct")),
        "forecast_expected_return_pct": _number(forecast.get("expected_return_pct")),
        "forecast_downside_risk_pct": downside_risk,
        "chase_risk_points": round(chase_risk, 1),
        "execution": plan.get("execution") or {},
        "no_buy_reason": no_buy,
        "expiry_rule": "買進期限到期、正式收盤資料更新、風險阻擋新增或買進區失效任一發生，即重新計算。",
        "exit_rule": "跌破停損視為原計畫失效；目標價分批獲利，剩餘部位只在趨勢仍有效時續抱。",
    }


def _preferred_horizon(plans: dict[str, dict[str, Any]]) -> str:
    candidates = []
    priority = {"short": 3, "medium": 2, "long": 1}
    recommendation_points = {"can_scale": 3, "wait_pullback": 2, "watch": 1}
    for horizon, plan in plans.items():
        rec = str(plan.get("recommendation") or "")
        candidates.append((
            recommendation_points.get(rec, 0),
            float(plan.get("score") or 0),
            float(plan.get("confidence") or 0),
            priority[horizon],
            horizon,
        ))
    candidates.sort(reverse=True)
    return candidates[0][-1] if candidates else "short"


def _compact_row(row: dict[str, Any], *, calendar: OfficialMarketCalendar,
                 now: datetime, source_coherent: bool = True) -> dict[str, Any]:
    plans = {h: _plan_for_horizon(row, h) for h in ("short", "medium", "long")}
    for plan in plans.values():
        conclusion = build_conclusion(row, plan, calendar=calendar, now=now,
                                      source_coherent=source_coherent)
        plan["conclusion"] = conclusion
        if conclusion["code"] != "eligible":
            if conclusion["code"] in {"insufficient", "avoid"}:
                plan["active_entry_plan"] = False
            plan["no_buy_reason"] = "；".join(conclusion["reasons"])
    preferred = _preferred_horizon(plans)
    preferred_plan = plans[preferred]
    price = _round_price(row.get("price"))
    status = {"eligible": "candidate", "wait": "wait", "avoid": "blocked",
              "insufficient": "insufficient"}[preferred_plan["conclusion"]["code"]]

    return {
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "market": row.get("market"),
        "asset_type": row.get("asset_type"),
        "industry": row.get("industry"),
        "session_date": row.get("session_date"),
        "input_evidence_categories": row.get("input_evidence_categories") or [],
        "shadow_events": row.get("shadow_events") or {},
        "price": price,
        "formal_rank": row.get("formal_rank"),
        "formal_score": row.get("formal_score"),
        "final_recommendation": (row.get("final") or {}).get("recommendation"),
        "final_confidence": (row.get("final") or {}).get("confidence"),
        "unresolved_conflicts": int(row.get("unresolved_conflict_count") or 0),
        "risk_blocks": row.get("risk_blocks") or [],
        "preferred_horizon": preferred,
        "status": status,
        "summary": {
            "buy_valid_sessions": preferred_plan["buy_window_sessions"],
            "entry_low": preferred_plan["entry_low"],
            "entry_high": preferred_plan["entry_high"],
            "do_not_chase_above": preferred_plan["do_not_chase_above"],
            "stop": preferred_plan["stop"],
            "source_stop": preferred_plan["source_stop"],
            "reference_stop_floor": preferred_plan["reference_stop_floor"],
            "stop_too_tight": preferred_plan["stop_too_tight"],
            "plan_quality": preferred_plan["plan_quality"],
            "target1": preferred_plan["target1"],
            "target2": preferred_plan["target2"],
            "target1_sell_pct": preferred_plan["target1_pct"],
            "target2_sell_pct": preferred_plan["target2_pct"],
            "runner_pct": preferred_plan["runner_pct"],
            "max_hold_sessions": preferred_plan["max_hold_sessions"],
            "no_buy_reason": preferred_plan["no_buy_reason"],
        },
        "plans": plans,
    }


def build_trade_plan_report(reports_dir: Path, *, now: datetime | None = None,
                            research_state_sink: dict[str, Any] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    calendar = OfficialMarketCalendar(reports_dir / "official_market_calendar.json",
                                     auto_refresh=False, allow_network=False)
    hub = _read_json(reports_dir / "decision_hub.json")
    rows: list[dict[str, Any]] = []
    observed_files = []
    chunk_no = 1
    while True:
        chunk_path = reports_dir / f"decision_hub_{chunk_no:02d}.json"
        if not chunk_path.is_file():
            break
        chunk = _read_json(chunk_path)
        observed_files.append(chunk_path.name)
        decisions = chunk.get("decisions")
        if isinstance(decisions, list):
            for item in decisions:
                if isinstance(item, dict):
                    rows.append({**item, "_source_updated_at": hub.get("updated_at"), "_source_coherent": bool(hub.get("updated_at"))
                                 and chunk.get("updated_at") == hub.get("updated_at")})
        chunk_no += 1

    complete_manifest = (isinstance(hub.get("decision_files"), list)
                         and hub["decision_files"] == observed_files
                         and observed_by(hub.get("updated_at"), now))
    # Duplicate symbols across stale/overlapping chunks cannot create two conclusions.
    keyed = {}
    for row in rows:
        key = (row.get("market"), row.get("symbol"))
        if row.get("symbol"):
            if key in keyed:
                row["_source_coherent"] = False
            keyed[key] = row
    plans = [_compact_row(row, calendar=calendar, now=now,
                          source_coherent=complete_manifest and row["_source_coherent"])
             for row in keyed.values()]
    plans.sort(
        key=lambda row: (
            {"candidate": 0, "wait": 1, "ready": 2, "blocked": 3}.get(str(row.get("status")), 9),
            int(row.get("formal_rank") or 999999),
            str(row.get("symbol") or ""),
        )
    )
    counts = {
        "total": len(plans),
        "candidate": None,
        "entry_not_evaluated": len(plans),
        "wait": sum(row["status"] == "wait" for row in plans),
        "blocked": sum(row["status"] == "blocked" for row in plans),
        "insufficient": sum(row["status"] == "insufficient" for row in plans),
    }
    validation = (hub.get("readiness") or {}).get("validation_60d") or {}
    private_dir = reports_dir.parent / ".prediction_engine"
    observations = _read_json(private_dir / "shadow_evidence_ablation_records.json")
    state_path = private_dir / "shadow_evidence_ablation_state.json"
    previous_state = _read_json(state_path)
    private_state = None
    try:
        if state_path.exists() and not previous_state:
            raise ValueError("unreadable existing ablation state")
        if observations.get("records") is not None and not isinstance(observations["records"], list):
            raise ValueError("ablation records must be a list")
        ablation = build_evidence_ablation_report(
            observations.get("records"), previous_state or None, generated_at=now,
            round_trip_cost_pct=observations.get("round_trip_cost_pct"),
        )
        private_state = ablation.pop("state")
    except (TypeError, ValueError, KeyError):
        ablation = {"status": "insufficient", "label": "配對驗證資料契約異常，保留原始資料待檢查",
                    "shadow_only": True, "affects_formal_v6": False, "automatic_promotion": False,
                    "accuracy_improvement": None, "blocked_reasons": ["invalid_private_ablation_input"],
                    "registered_pairs": 0, "matched_completed_pairs": 0}
    if research_state_sink is not None and private_state is not None:
        research_state_sink.update(private_state)
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": "shadow_plan_only",
        "entry_evaluation": {"status": "not_evaluated", "label": "尚未評估後續進場",
                             "same_snapshot_never_used_as_entry_quote": True,
                             "candidate_count": None},
        "updated_at": hub.get("updated_at"),
        "evaluated_at": now.isoformat(),
        "status": "ready" if plans else "waiting_source",
        "source_model_version": hub.get("model_version"),
        "source_decision_hub_updated_at": hub.get("updated_at"),
        "validation": {
            "trading_days_collected": int(validation.get("collected_trading_days") or 0),
            "target_trading_days": int(validation.get("target_trading_days") or 60),
            "formal_adoption_ready": False,
            "source_model_readiness_only": bool(validation.get("ready")),
            "conclusion_validation_status": "pending_out_of_sample_forward_cost_drawdown",
        },
        "evidence_ablation": ablation,
        "policy": {
            "formal_v6_unchanged": True,
            "formal_rankings_unchanged": True,
            "formal_weights_unchanged": True,
            "automatic_orders": False,
            "broker_connection_used": False,
            "shadow_only": True,
            "buy_window_uses_valid_trading_sessions": True,
            "expired_plan_must_recalculate": True,
            "stop_loss_exits_full_shadow_position": True,
            "stop_never_auto_widened": True,
            "tight_stop_blocks_entry_until_recalculated": True,
            "sell_percentages_are_shadow_execution_rules": True,
        },
        "summary": counts,
        "plans": plans,
    }


def write_trade_plan_report(reports_dir: Path, *, update_registry: bool = True) -> Path:
    research_state = {}
    report = build_trade_plan_report(reports_dir, research_state_sink=research_state)
    state_path = reports_dir.parent / ".prediction_engine" / "shadow_evidence_ablation_state.json"
    try:
        if research_state:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(research_state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(state_path)
    except OSError:
        report["evidence_ablation"]["status"] = "insufficient"
        report["evidence_ablation"]["label"] = "私有驗證狀態未保存，不列為有效驗證"
    try:
        from tw_prospective_runtime import update_tw_prospective_report, attach_registry_summary, REGISTRY_FILE
        if update_registry:
            update_tw_prospective_report(reports_dir, report)
        elif (reports_dir / REGISTRY_FILE).exists():
            # Briefing's legacy report rebase must never replay a stale ledger.
            # The fresh-main publisher advances it after the formal batch commits.
            previous_registry = json.loads((reports_dir / REGISTRY_FILE).read_text(encoding="utf-8"))
            from tw_prospective_registry import validate_tw_prospective_registry
            attach_registry_summary(report, validate_tw_prospective_registry(previous_registry))
        else:
            report["tw_prospective_registry"] = {"status": "unavailable",
                "label": "等待安全保存程序建立前瞻登錄", "automatic_orders": False}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report["tw_prospective_registry"] = {
            "status": "unavailable", "label": "前瞻登錄暫停；既有登錄保留待核對",
            "reason": type(exc).__name__, "automatic_orders": False,
            "predictive_efficacy_validated": False,
        }
    target = reports_dir / "trade_plan_shadow.json"
    temp = reports_dir / "trade_plan_shadow.tmp"
    temp.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(target)
    health = {
        "status": "ok" if report["status"] == "ready" else "warning",
        "updated_at": report.get("updated_at"),
        "plan_count": report["summary"]["total"],
        "formal_v6_unchanged": True,
        "formal_rankings_unchanged": True,
        "automatic_orders": False,
    }
    health_path = reports_dir / "trade_plan_shadow_health.json"
    health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="建立只讀影子交易計畫")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    print(write_trade_plan_report(args.reports_dir))


if __name__ == "__main__":
    main()
