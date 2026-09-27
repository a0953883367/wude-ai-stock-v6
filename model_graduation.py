"""Formal V6 graduation conclusions; shadow promotion is governed separately."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _conclusion(name: str, label: str, current: int, target: int, *,
                formal_days: int, quality_ready: bool = False, extra: str = "") -> dict[str, Any]:
    if current < target:
        status = "collecting"
        reason = f"模型資料進度 {current}/{target}，尚未達初評門檻"
    elif formal_days < 60:
        status = "preliminary_review_only"
        reason = (
            f"模型資料門檻已達 {current}/{target}，但正式共同驗證只有 "
            f"{formal_days}/60；目前只可初評，不可畢業"
        )
    elif not quality_ready:
        status = "review_required"
        reason = f"正式共同驗證已達 {formal_days}/60，但品質或績效條件尚未全部通過"
    else:
        status = "eligible_for_manual_graduation"
        reason = f"正式共同驗證 {formal_days}/60 且品質條件通過，可人工決定是否畢業"
    if extra:
        reason += f"；{extra}"
    return {
        "model_id": name, "label": label, "status": status,
        "current": current, "target": target, "reason": reason,
        "component_gate_passed": current >= target,
        "formal_validation_days": formal_days,
        "formal_validation_target": 60,
        "automatic_promotion": False, "changes_formal_weights": False,
        "promotion_scope": "formal_v6",
        "places_orders": False,
    }


def update_model_graduation(reports_dir: Path, *, updated_at: str) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    validation = _read(reports_dir / "validation_60d.json")
    valuation = _read(reports_dir / "valuation_risk_shadow.json")
    rotation = _read(reports_dir / "market_rotation_shadow.json")
    weights = _read(reports_dir / "tw_weight_experiment.json")
    inverse = _read(reports_dir / "inverse_etf_shadow.json")
    comprehensive = _read(reports_dir / "comprehensive_shadow_history.json")
    days = int(validation.get("trading_days_collected") or 0)
    models = [
        _conclusion(
            "next_session_v7", "隔日方向影子模型", days, 60,
            formal_days=days,
            quality_ready=bool(validation.get("ready_for_model_selection")),
        )
    ]
    valuation_sessions = min(
        int((valuation.get("validation", {}).get(market) or {}).get("effective_sessions") or 0)
        for market in ("TW", "US")
    ) if valuation else 0
    valuation_samples = sum(
        int((valuation.get("validation", {}).get(market) or {}).get("effective_samples") or 0)
        for market in ("TW", "US")
    )
    models.append(_conclusion(
        "valuation_risk", "估值風險雷達", valuation_samples, 100,
        formal_days=days,
        quality_ready=valuation_sessions >= 10,
        extra=f"有效交易日最少 {valuation_sessions}/10",
    ))
    rotation_days = min(
        len((rotation.get("markets", {}).get(market) or {}).get("snapshots", []))
        for market in ("TW", "US")
    ) if rotation else 0
    models.append(_conclusion(
        "market_rotation", "族群輪動影子模型", rotation_days, 60,
        formal_days=days,
        quality_ready=rotation_days >= 60,
    ))
    weight_days = max(
        (len(model.get("days", [])) for model in (weights.get("models") or {}).values()
         if isinstance(model, dict)), default=0,
    )
    models.append(_conclusion(
        "tw_institution_weight", "台股法人權重模型", weight_days, 20,
        formal_days=days,
        quality_ready=weight_days >= 20 and bool(weights.get("winner_model")),
        extra=(
            str((weights.get("preliminary_assessment") or {}).get("verdict") or "")
            or "5日只做初檢，20日才可提出影子權重候選"
        ),
    ))
    inverse_samples = sum(
        int(summary.get("samples") or 0)
        for market in ("TW", "US")
        for summary in (inverse.get("markets", {}).get(market) or {}).get("summary", {}).values()
        if isinstance(summary, dict)
    )
    models.append(_conclusion(
        "inverse_etf", "反向ETF影子模型", inverse_samples, 20,
        formal_days=days,
        quality_ready=inverse_samples >= 20,
    ))
    comprehensive_days = comprehensive.get("valid_trading_days") or {}
    for market, label in (("TW", "台股"), ("US", "美股")):
        current = int(comprehensive_days.get(market) or 0)
        models.append(_conclusion(
            f"comprehensive_shadow_{market.lower()}",
            f"{label}綜合影子排名",
            current,
            60,
            formal_days=days,
            quality_ready=current >= 60,
            extra="20個交易日先初評，60日後才可人工決定是否整合",
        ))
    summary = {
        status: sum(model["status"] == status for model in models)
        for status in (
            "collecting", "preliminary_review_only", "review_required",
            "eligible_for_manual_graduation",
        )
    }
    payload = {
        "schema_version": 1, "updated_at": updated_at,
        "status": "ready", "summary": summary, "models": models,
        "policy": {
            "conclusion_generated_automatically": True,
            "promotion_requires_manual_decision": True,
            "promotion_requires_manual_decision_scope": "formal_v6_only",
            "controlled_shadow_promotion_automatic": True,
            "controlled_shadow_trust_automatic": True,
            "formal_v6_promotion_requires_manual_decision": True,
            "formal_v6_minimum_validation_days": 60,
            "twenty_days_is_preliminary_review_only": True,
            "long_term_tracking_days": 126,
            "formal_ranking_locked": True,
            "automatic_weight_changes": False,
            "automatic_merge": False,
            "automatic_orders": False,
        },
    }
    _write(reports_dir / "model_graduation.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--updated-at", default="")
    args = parser.parse_args()
    payload = update_model_graduation(Path(args.reports_dir), updated_at=args.updated_at)
    print(f"model graduation: {payload['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
