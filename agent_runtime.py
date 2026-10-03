"""Safe execution runtime for the three isolated central agents.

Only deterministic and reversible validation tasks run here. The public result
never stores task payloads, contacts recipients, changes stock scores, or writes
to company systems and equipment.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agent_control import AGENTS, UsageLedger, authorize_task
from artifact_validation import verify_artifact
from context_audit import audit_catalog
from context_governance import build_context_plan
from dynamic_capability_loader import build_capability_plan
from context_efficiency import build_efficiency_report
from presentation_delivery import verify_presentation_delivery


RUNTIME_VERSION = "CENTRAL-AGENT-RUNTIME-V3"

BOOTSTRAP_TASKS = (
    ("stock_shadow", "shadow_validate", "檢查股票影子驗證工作區"),
    ("wt_fasteners", "simulate_workflow", "驗證 WT 電商測試工作區"),
    ("packaging_startup", "simulate_workflow", "驗證包裝創業試算工作區"),
)

BOUNDARY_CHECKS = (
    ("stock_shadow", "broker_order", "券商下單保持鎖定"),
    ("wt_fasteners", "external_publish", "WT 對外上架保持鎖定"),
    ("packaging_startup", "payment", "包裝創業付款保持鎖定"),
)

ACTION_CONTEXT_PROFILES = {
    "read_status": "status",
    "analyze": "formal_answer",
    "shadow_validate": "research",
    "draft_report": "output",
    "simulate_workflow": "formal_answer",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _private_inputs(root: Path, agent_id: str) -> dict[str, Any]:
    """Read transient connector input; this directory is never committed."""
    return _read_json(root / ".agent_private_inputs" / f"{agent_id}.json")


def _stock_shadow_work(root: Path) -> dict[str, Any]:
    reports = root / "reports"
    validation = _read_json(reports / "validation_60d.json")
    learning = _read_json(reports / "model_learning.json")
    guard = _read_json(reports / "system_guard.json")
    prediction = _read_json(reports / "prediction_engine.json")
    unit_learning = _read_json(reports / "model_unit_learning.json")
    coach = _read_json(reports / "weekly_shadow_coach.json")
    required = {
        "validation_60d.json": bool(validation),
        "model_learning.json": bool(learning),
        "system_guard.json": bool(guard),
        "prediction_engine.json": bool(prediction),
        "model_unit_learning.json": bool(unit_learning),
        "weekly_shadow_coach.json": bool(coach),
    }
    if not all(required.values()):
        missing = [name for name, exists in required.items() if not exists]
        return {
            "status": "attention",
            "status_label": "巡檢失敗；缺少影子證據",
            "summary": "缺少必要報表，未把影子工作誤判為完成。",
            "evidence": {"checked_files": required, "missing_files": missing},
            "next_action": "等待既有股票報表流程補齊證據後再次巡檢。",
        }
    progress = validation.get("trading_days_collected", 0)
    target = validation.get("target_trading_days", 60)
    error_learning = learning.get("error_learning") or {}
    guard_status = guard.get("status") or "unknown"
    unit_summary = unit_learning.get("summary") or {}
    prediction_policy = prediction.get("policy") or {}
    self_learning_safe = (
        prediction_policy.get("controlled_shadow_auto_promotion") is True
        and prediction_policy.get("formal_v6_auto_promotion") is False
        and prediction_policy.get("automatic_orders") is False
        and (unit_learning.get("policy") or {}).get("formal_v6_unchanged") is True
    )
    safe = guard_status in {"ok", "warning"}
    return {
        "status": "completed" if safe else "attention",
        "status_label": "已實際核對影子進度與安全鎖" if safe else "影子巡檢發現異常",
        "summary": f"已讀取影子證據：向前驗證 {progress}/{target} 日、錯誤事件 {error_learning.get('independent_events', 0)} 筆、成熟學習 {unit_summary.get('matured_rows', 0)} 筆；影子升退版是否啟用以安全政策與有效樣本為準。",
        "evidence": {
            "checked_files": required,
            "validation_days": progress,
            "target_days": target,
            "eligible_samples": validation.get("eligible_samples", 0),
            "independent_error_events": error_learning.get("independent_events", 0),
            "guard_status": guard_status,
            "learning_units": unit_summary.get("registered_units", 0),
            "matured_learning_rows": unit_summary.get("matured_rows", 0),
            "active_shadow_trust_streams": unit_summary.get("active_shadow_trust_streams", 0),
            "self_learning_enabled": self_learning_safe,
            "automatic_shadow_promotion": prediction_policy.get("controlled_shadow_auto_promotion") is True,
            "automatic_shadow_rollback_after_failures": prediction_policy.get("automatic_shadow_rollback_after_failures", 0),
            "weekly_coach_status": coach.get("status") or "unknown",
            "weekly_coach_mode": coach.get("mode") or "unknown",
            "formal_v6_locked": not bool((validation.get("rules") or {}).get("automatic_weight_changes", True)),
            "automatic_orders_blocked": not bool((validation.get("rules") or {}).get("automatic_orders", True)),
        },
        "next_action": "持續累積真實交易日；只做影子驗證，不改正式排名、權重或下單。",
    }


def _wt_work(root: Path) -> dict[str, Any]:
    workspace = root / "agent_workspaces" / "wt_fasteners"
    spec = _read_json(workspace / "商品資料輸入規格.json")
    workbook = workspace / str(spec.get("workbook") or "WT商品與毛利輸入表.xlsx")
    required = list(spec.get("required_inputs") or [])
    private = _private_inputs(root, "wt_fasteners")
    supplied = private.get("known_inputs") if isinstance(private.get("known_inputs"), dict) else private
    missing = [name for name in required if supplied.get(name) in (None, "")]
    return {
        "status": "waiting_input" if missing else "completed",
        "status_label": "已自動讀取成本來源" if not missing else "已自動搜尋；等待真實成本",
        "summary": f"已自動檢查工作區與授權輸入來源；仍缺 {len(missing)} 項必要成本，因此沒有捏造毛利。",
        "evidence": {
            "spec_found": bool(spec),
            "workbook_found": workbook.is_file(),
            "required_input_count": len(required),
            "private_connector_input_found": bool(private),
            "resolved_input_count": len(required) - len(missing),
            "missing_inputs": missing,
            "profit_calculated": False,
        },
        "next_action": "填入售價、螺絲、包材、人工、運費、平台與廣告成本後自動重算。",
    }


def _packaging_work(root: Path) -> dict[str, Any]:
    workspace = root / "agent_workspaces" / "packaging_startup"
    inputs = _read_json(workspace / "包裝創業試算輸入.json")
    known = dict(inputs.get("known_inputs") or {})
    private = _private_inputs(root, "packaging_startup")
    private_known = private.get("known_inputs") if isinstance(private.get("known_inputs"), dict) else private
    known.update({key: value for key, value in private_known.items() if value not in (None, "")})
    missing = [name for name in (inputs.get("missing_inputs") or []) if known.get(name) in (None, "")]
    workbook = workspace / str(inputs.get("workbook") or "包裝創業產能與損益試算.xlsx")
    per_minute = float(known.get("每分鐘桶數") or 0)
    hours = float(known.get("每日工時") or 0)
    utilization = float(known.get("稼動率") or 0)
    workdays = float(known.get("每月工作日") or 0)
    unit_revenue = float(known.get("每桶加工收入") or 0)
    daily_design = per_minute * 60 * hours
    daily_effective = daily_design * utilization
    monthly_capacity = daily_effective * workdays
    order_limit = float(known.get("每月訂單上限") or monthly_capacity)
    monthly_orders = min(monthly_capacity, order_limit)
    monthly_revenue_ceiling = monthly_orders * unit_revenue
    profit = None
    payback_months = None
    if not missing:
        total_cost = (
            float(known.get("人數") or 0) * float(known.get("每人月薪與雇主成本") or 0)
            + float(known.get("每月租金") or 0)
            + float(known.get("每月水電") or 0)
            + monthly_orders * float(known.get("每桶包材") or 0)
        )
        profit = monthly_revenue_ceiling - total_cost
        equipment = float(known.get("設備總價") or 0)
        payback_months = equipment / profit if profit > 0 else None
    summary = (
        f"依現有輸入實算月有效產能 {monthly_capacity:,.0f} 桶、月可接單 {monthly_orders:,.0f} 桶、"
        f"加工收入 {monthly_revenue_ceiling:,.0f} 元；完整試算月損益 {profit:,.0f} 元。"
        if profit is not None else
        f"依現有輸入實算月有效產能 {monthly_capacity:,.0f} 桶、滿載加工收入上限 {monthly_revenue_ceiling:,.0f} 元；缺 {len(missing)} 項資料，暫不顯示獲利。"
    )
    return {
        "status": "waiting_input" if missing else "completed",
        "status_label": "已完成產能試算；等待成本" if missing else "產能與損益試算完成",
        "summary": summary,
        "evidence": {
            "input_file_found": bool(inputs),
            "workbook_found": workbook.is_file(),
            "private_connector_input_found": bool(private),
            "known_input_count": len(known),
            "missing_input_count": len(missing),
            "missing_inputs": missing,
            "daily_design_capacity": round(daily_design, 2),
            "daily_effective_capacity": round(daily_effective, 2),
            "monthly_effective_capacity": round(monthly_capacity, 2),
            "monthly_order_capacity": round(monthly_orders, 2),
            "monthly_revenue_ceiling": round(monthly_revenue_ceiling, 2),
            "profit_calculated": not bool(missing),
            "monthly_profit": round(profit, 2) if profit is not None else None,
            "payback_months": round(payback_months, 2) if payback_months is not None else None,
        },
        "next_action": "補齊重量、人事、水電、包材、設備報價與訂單上限後才計算損益與回收期。",
    }


def _perform_actual_work(agent_id: str, action: str, root: Path) -> dict[str, Any] | None:
    if agent_id == "stock_shadow" and action == "shadow_validate":
        return _stock_shadow_work(root)
    if agent_id == "wt_fasteners" and action == "simulate_workflow":
        return _wt_work(root)
    if agent_id == "packaging_startup" and action == "simulate_workflow":
        return _packaging_work(root)
    return None


def execute_safe_task(
    agent_id: str,
    action: str,
    title: str,
    payload: Mapping[str, Any] | None = None,
    ledger: UsageLedger | None = None,
    *,
    root: Path = Path("."),
) -> dict[str, Any]:
    decision = authorize_task(
        agent_id,
        action,
        payload=payload,
        ledger=ledger,
        calls=0,
        tokens=0,
    )
    result = {
        "agent_id": agent_id,
        "agent_name": AGENTS[agent_id].name,
        "namespace": AGENTS[agent_id].namespace,
        "title": title,
        "action": action,
        "task_key": decision["task_key"],
        "payload_stored": False,
        "external_side_effect": False,
    }
    if decision["executable"]:
        profile = ACTION_CONTEXT_PROFILES.get(action, "formal_answer")
        plan = build_context_plan(
            title,
            profile=profile,
            explicit_agent=agent_id,
            root=root,
        )
        result["context_plan"] = {
            "profile": profile,
            "layers": plan["layers"],
            "files": plan["usage"]["files"],
            "bytes": plan["usage"]["bytes"],
            "truncated": plan["usage"]["truncated"],
            "cross_domain_reads": plan["cross_domain_reads"],
        }
        capabilities = build_capability_plan(
            title,
            action,
            explicit_agent=agent_id,
            root=root,
        )
        result["capability_plan"] = {
            "skills": capabilities["skills"],
            "tools": [item["name"] for item in capabilities["tools"]],
            "schema_bytes": capabilities["usage"]["schema_bytes"],
            "executor_loaded": capabilities["executor_loaded"],
            "cross_domain_tools": capabilities["cross_domain_tools"],
            "connectors": capabilities.get("connectors") or [],
            "auto_connectors": [
                item["name"] for item in capabilities.get("connectors") or []
                if item.get("auto_connect")
            ],
        }
        if action == "draft_report":
            validation_spec = payload.get("artifact_validation") if isinstance(payload, Mapping) else None
            if isinstance(validation_spec, dict):
                artifact_name = str(validation_spec.get("artifact") or "")
                validation = (
                    verify_presentation_delivery(validation_spec, root=root)
                    if Path(artifact_name).suffix.casefold() == ".pptx"
                    else verify_artifact(validation_spec, root=root)
                )
                result["artifact_validation"] = {
                    "status": validation["status"],
                    "verified": validation["verified"],
                    "failure_count": len(validation["failures"]),
                }
                if "slide_count" in validation:
                    result["artifact_validation"].update(
                        slide_count=validation["slide_count"],
                        rendered_slide_count=validation["rendered_slide_count"],
                    )
                if validation["verified"]:
                    result.update(
                        status="completed",
                        status_label="輸出驗收通過",
                        summary="必要資料已依清冊載入，且輸出證據驗收通過。",
                    )
                else:
                    result.update(
                        status="draft",
                        status_label="草稿；等待輸出驗收",
                        summary="輸出尚未通過完整證據驗收，不可標示正式完成。",
                    )
            else:
                result.update(
                    status="draft",
                    status_label="草稿；尚未提供輸出驗收",
                    summary="報告已保留草稿狀態；完成來源、數字、跨頁及渲染檢查後才可交付。",
                )
            return result
        work = _perform_actual_work(agent_id, action, root)
        if work:
            result.update(work)
            result["actual_work_performed"] = True
        else:
            result.update(
                status="completed",
                status_label="安全驗收通過",
                summary="獨立資料空間、動態載入、權限與輸出契約均已通過；未呼叫外部服務。",
                actual_work_performed=False,
            )
    else:
        capabilities = build_capability_plan(title, action, explicit_agent=agent_id, root=root)
        result["capability_plan"] = {
            "skills": capabilities["skills"],
            "tools": [item["name"] for item in capabilities["tools"]],
            "schema_bytes": capabilities["usage"]["schema_bytes"],
            "executor_loaded": capabilities["executor_loaded"],
            "cross_domain_tools": capabilities["cross_domain_tools"],
            "connectors": [],
            "auto_connectors": [],
        }
        result.update(
            status=decision["decision"],
            status_label="等待授權" if decision["decision"] == "waiting_for_approval" else "已阻擋",
            summary=decision["reason"],
        )
    return result


def build_runtime_report(reports_dir: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Build the runtime report from an explicit repository root.

    Keeping ``root`` explicit makes the safety checks reproducible: a CI run
    must not change result merely because a generated report in the checkout
    happens to contain a warning from an earlier monitoring run.
    """
    root = root or reports_dir.parent
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    control = _read_json(reports_dir / "agent_control.json")
    maintenance = _read_json(reports_dir / "agent_recovery.json")
    ledger = UsageLedger()
    validations = [
        execute_safe_task(agent_id, action, title, {"validation": True}, ledger, root=root)
        for agent_id, action, title in BOOTSTRAP_TASKS
    ]
    gates = [
        execute_safe_task(agent_id, action, title, {"validation": True}, ledger, root=root)
        for agent_id, action, title in BOUNDARY_CHECKS
    ]
    completed = sum(task["status"] == "completed" for task in validations)
    waiting_input = sum(task["status"] == "waiting_input" for task in validations)
    actual_work = sum(bool(task.get("actual_work_performed")) for task in validations)
    protected = sum(task["status"] == "waiting_for_approval" for task in gates)
    governance = audit_catalog(root=root)
    efficiency = build_efficiency_report(root=root)
    return {
        "schema": "wude.central_agent_runtime.v1",
        "version": RUNTIME_VERSION,
        "generated_at": generated_at,
        "status": "active" if actual_work == len(validations) and protected == len(gates) else "attention",
        "status_label": f"三個 Agent 已實際工作；{waiting_input} 項等待輸入" if actual_work == len(validations) and protected == len(gates) else "Agent 執行需要注意",
        "control_version": control.get("version") or "尚無控制層紀錄",
        "summary": {
            "agent_count": len(AGENTS),
            "validation_completed": completed,
            "validation_total": len(validations),
            "actual_work_performed": actual_work,
            "waiting_input": waiting_input,
            "approval_gates_protected": protected,
            "approval_gates_total": len(gates),
            "external_calls": 0,
            "paid_model_calls": 0,
        },
        "stock_maintenance": {
            "connected": bool(maintenance),
            "executor": maintenance.get("executor") or "尚未取得維護執行證據",
            "checked_at": maintenance.get("checked_at"),
            "capabilities": maintenance.get("capabilities") or {},
            "limits": maintenance.get("limits") or {},
            "incidents": maintenance.get("incidents") or {},
            "blockers": maintenance.get("blockers") or [],
            "permission_probe": maintenance.get("permission_probe") or {},
            "code_repair_access": maintenance.get("code_repair_access") or {},
            "paid_model_calls": 0,
            "dispatch_accepted_is_recovery": False,
        },
        "counter_scope": "summary.external_calls/paid_model_calls only cover this deterministic validation runtime; stock maintenance has separate action evidence",
        "validations": validations,
        "approval_gate_checks": gates,
        "privacy": {
            "payloads_stored": False,
            "customer_data_stored": False,
            "cross_domain_writes": False,
            "public_output": "只含任務摘要、雜湊、結果與授權狀態",
        },
        "context_governance": {
            "status": governance["status"],
            "status_label": governance["status_label"],
            "classified_files": governance["summary"]["classified_files"],
            "cross_domain_conflicts": len(governance["cross_domain_conflicts"]),
            "cross_layer_conflicts": len(governance["cross_layer_conflicts"]),
            "dynamic_loading": True,
            "archive_requires_explicit_request": True,
        },
        "context_efficiency": {
            "status": efficiency["status"],
            "average_reduction_pct": efficiency["average_reduction_pct"],
            "exact_provider_tokens": efficiency["exact_provider_tokens"],
            "dynamic_skill_loading": True,
            "dynamic_tool_schema_loading": True,
            "memory_compaction_available": True,
        },
        "output_validation": {
            "status": "enforced_by_contract",
            "draft_until_verified": True,
            "formats": ["PPTX", "PDF", "DOCX", "XLSX", "HTML", "Markdown", "JSON"],
            "required_checks": ["來源版本", "數字日期單位", "跨頁一致", "實際渲染檢查"],
        },
        "next_authorized_steps": {
            "stock_shadow": "繼續既有影子前向驗證與 GPT 教導，不改正式 V6。",
            "wt_fasteners": "可執行商品與競品草稿；正式商店資料與上架等待來源及核准。",
            "packaging_startup": "可執行市場、成本與投資評估；付款與聯絡供應商等待核准。",
        },
    }


def write_runtime_report(reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "agent_runtime.json"
    target.write_text(
        json.dumps(build_runtime_report(reports_dir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="執行四個隔離 Agent 的安全驗收")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    print(write_runtime_report(args.reports_dir))


if __name__ == "__main__":
    main()
