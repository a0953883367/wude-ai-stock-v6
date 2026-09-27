"""Owner-facing stock growth workflow and layered monitoring control tower.

This module aggregates existing reports only.  It cannot change formal V6
scores, rankings, weights, model versions, or broker instructions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


LIGHTS = {
    "ok": ("green", "正常"),
    "collecting": ("blue", "累積中"),
    "attention": ("yellow", "注意"),
    "manual_review": ("orange", "待人工決定"),
    "blocked": ("red", "阻擋"),
}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _status(value: str, *, detail: str = "") -> dict[str, Any]:
    light, label = LIGHTS[value]
    return {"status": value, "light": light, "status_label": label, "detail": detail}


def _stage(
    stage_id: str,
    order: int,
    label: str,
    value: str,
    detail: str,
    *,
    current: int | None = None,
    target: int | None = None,
) -> dict[str, Any]:
    result = {"id": stage_id, "order": order, "label": label, **_status(value, detail=detail)}
    if current is not None:
        result["current"] = current
    if target is not None:
        result["target"] = target
    return result


def _normalize_model(row: dict[str, Any]) -> dict[str, Any]:
    current = int(row.get("current") or 0)
    target = max(1, int(row.get("target") or 60))
    source_status = str(row.get("status") or "collecting")
    if source_status == "eligible_for_manual_graduation":
        phase, light, phase_label = "waiting_owner", "orange", "待人工畢業決定"
    elif source_status == "review_required":
        phase, light, phase_label = "performance_review", "yellow", "績效／品質複核"
    elif current >= 60:
        phase, light, phase_label = "validation_60d", "blue", "60日驗證"
    elif current >= 20:
        phase, light, phase_label = "preliminary_review", "blue", "20日初評"
    else:
        phase, light, phase_label = "accumulating", "blue", "樣本累積"
    return {
        "model_id": str(row.get("model_id") or "unknown"),
        "label": str(row.get("label") or "未命名模型"),
        "phase": phase,
        "phase_label": phase_label,
        "light": light,
        "current": current,
        "target": target,
        "progress_pct": round(min(100.0, current / target * 100), 1),
        "reason": str(row.get("reason") or "等待有效樣本"),
        "formal_promotion": "manual_only",
        "automatic_orders": False,
    }


def build_stock_growth_control(reports_dir: Path, *, updated_at: str = "") -> dict[str, Any]:
    """Build the read-only control tower from already-published contracts."""
    reports_dir = Path(reports_dir)
    guard = _read(reports_dir / "system_guard.json")
    graduation = _read(reports_dir / "model_graduation.json")
    validation = _read(reports_dir / "validation_60d.json")
    prediction = _read(reports_dir / "prediction_engine_health.json")
    trade_validation = _read(reports_dir / "trade_plan_validation.json")

    models = [
        _normalize_model(row)
        for row in graduation.get("models", [])
        if isinstance(row, dict)
    ]
    days = int(validation.get("trading_days_collected") or 0)
    guard_status = str(guard.get("status") or "warning").lower()
    critical_count = int((guard.get("counts") or {}).get("critical") or 0)
    warning_count = int((guard.get("counts") or {}).get("warning") or 0)
    prediction_status = str(prediction.get("status") or "warning").lower()
    matured = int((trade_validation.get("summary") or {}).get("matured") or 0)
    eligible = sum(model["phase"] == "waiting_owner" for model in models)

    if critical_count or guard_status == "critical":
        data_state = system_state = "blocked"
    elif warning_count or guard_status == "warning":
        data_state = system_state = "attention"
    else:
        data_state = system_state = "ok"
    model_state = "attention" if prediction_status not in {"ok", "ready", "success"} else "ok"

    stages = [
        _stage("data_ingestion", 1, "資料進站", data_state,
               "台股、美股、ETF 與官方資料先進入獨立資料層。"),
        _stage("quality_gate", 2, "品質檢查", data_state,
               f"系統值班檢查：異常 {critical_count} 項、注意 {warning_count} 項。"),
        _stage("formal_v6", 3, "正式 V6 分析", "ok",
               "正式排名與權重只讀，控制塔沒有修改權限。"),
        _stage("shadow_growth", 4, "影子候選", "collecting" if models else "attention",
               f"目前追蹤 {len(models)} 個正式升級候選；影子帳本與正式 V6 隔離。"),
        _stage("phased_validation", 5, "1／3／5／20／60／126日驗證",
               "collecting" if days < 60 else "manual_review",
               f"60日中央驗證目前 {days}/60；交易計畫已成熟 {matured} 筆。",
               current=days, target=60),
        _stage("graduation_gate", 6, "畢業閘門",
               "manual_review" if eligible else "collecting",
               f"{eligible} 個候選已到人工決定階段；未達門檻者繼續累積。"),
        _stage("formal_release", 7, "正式上線／退回", "ok",
               "只接受人工決定；自動合併、自動改權重與自動下單全部關閉。"),
    ]

    layers = [
        {"id": "data", "label": "資料監控", **_status(data_state, detail="完整度、來源、更新時間與缺值隔離。")},
        {"id": "workflow", "label": "流程監控", **_status("ok" if models else "attention", detail="報表、驗證、畢業結論依序產生。")},
        {"id": "model", "label": "模型監控", **_status(model_state, detail="準確率、報酬、回撤、獲利因子與預判引擎健康。")},
        {"id": "system", "label": "系統監控", **_status(system_state, detail="GitHub Actions、主 App、Railway 與通知分開判斷。")},
        {"id": "safety", "label": "安全監控", **_status("ok", detail="正式 V6、排名、權重、Merge 與券商下單保持鎖定。")},
        {"id": "cost", "label": "成本監控", **_status("attention", detail="只顯示用量治理狀態；帳務與付款資料不寫入公開報表。")},
    ]

    actions: list[dict[str, str]] = []
    if data_state == "blocked":
        actions.append({"priority": "urgent", "title": "先修復資料或系統異常", "detail": "紅燈解除前停止採用新的影子結論。"})
    elif data_state == "attention":
        actions.append({"priority": "review", "title": "查看系統值班注意項", "detail": "正式 V6 維持不變，先確認黃色項目。"})
    if model_state == "attention":
        actions.append({"priority": "review", "title": "檢查預判引擎健康", "detail": "獨立預判有警示；不影響正式 V6，但不可升級。"})
    if days < 60:
        actions.append({"priority": "wait", "title": "繼續累積60日證據", "detail": f"目前 {days}/60，尚差 {60 - days} 個有效交易日。"})
    if eligible:
        actions.append({"priority": "owner", "title": "人工審查畢業候選", "detail": f"已有 {eligible} 個候選等待你的決定；系統不會自動合併。"})

    severities = [layer["status"] for layer in layers]
    overall = "blocked" if "blocked" in severities else "attention" if "attention" in severities else "ok"
    return {
        "schema_version": 1,
        "updated_at": updated_at or str(graduation.get("updated_at") or guard.get("checked_at") or ""),
        "system": "股票成長控制塔 V1",
        **_status(overall, detail="七階段流程與六層監控已統一。"),
        "summary": {
            "workflow_stages": len(stages),
            "monitoring_layers": len(layers),
            "tracked_models": len(models),
            "models_waiting_owner": eligible,
            "validation_days": days,
            "validation_target": 60,
        },
        "workflow_stages": stages,
        "monitoring_layers": layers,
        "models": models,
        "action_queue": actions,
        "rules": {
            "diagnostic_horizons": [1, 3, 5],
            "preliminary_review_day": 20,
            "formal_review_day": 60,
            "long_term_review_day": 126,
            "missing_data_is_not_zero": True,
            "same_session_outcome_forbidden": True,
        },
        "safety": {
            "read_only_aggregator": True,
            "formal_v6_locked": True,
            "formal_rankings_locked": True,
            "formal_weights_locked": True,
            "automatic_merge": False,
            "automatic_orders": False,
            "stores_private_billing_details": False,
            "note": "控制塔只整理既有證據與提醒人工決定，不控制券商、不付款。",
        },
    }


def update_stock_growth_control(reports_dir: Path, *, updated_at: str = "") -> dict[str, Any]:
    payload = build_stock_growth_control(Path(reports_dir), updated_at=updated_at)
    _write(Path(reports_dir) / "stock_growth_control.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--updated-at", default="")
    args = parser.parse_args()
    payload = update_stock_growth_control(Path(args.reports_dir), updated_at=args.updated_at)
    print(f"stock growth control: {payload['status_label']} -> {args.reports_dir}/stock_growth_control.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
