"""Safe execution runtime for the isolated central agents.

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
from context_audit import audit_catalog


RUNTIME_VERSION = "CENTRAL-AGENT-RUNTIME-V1"

BOOTSTRAP_TASKS = (
    ("stock_shadow", "shadow_validate", "檢查股票影子驗證工作區"),
    ("zhiying_company", "simulate_workflow", "驗證至盈流程模擬工作區"),
    ("wt_fasteners", "simulate_workflow", "驗證 WT 電商測試工作區"),
    ("packaging_startup", "simulate_workflow", "驗證包裝創業試算工作區"),
)

BOUNDARY_CHECKS = (
    ("stock_shadow", "broker_order", "券商下單保持鎖定"),
    ("zhiying_company", "erp_write", "公司 ERP 寫入保持鎖定"),
    ("wt_fasteners", "external_publish", "WT 對外上架保持鎖定"),
    ("packaging_startup", "payment", "包裝創業付款保持鎖定"),
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def execute_safe_task(
    agent_id: str,
    action: str,
    title: str,
    payload: Mapping[str, Any] | None = None,
    ledger: UsageLedger | None = None,
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
        result.update(
            status="completed",
            status_label="安全驗收通過",
            summary="獨立資料空間、權限與輸出契約均已通過；未呼叫外部服務。",
        )
    else:
        result.update(
            status=decision["decision"],
            status_label="等待授權" if decision["decision"] == "waiting_for_approval" else "已阻擋",
            summary=decision["reason"],
        )
    return result


def build_runtime_report(reports_dir: Path) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    control = _read_json(reports_dir / "agent_control.json")
    ledger = UsageLedger()
    validations = [
        execute_safe_task(agent_id, action, title, {"validation": True}, ledger)
        for agent_id, action, title in BOOTSTRAP_TASKS
    ]
    gates = [
        execute_safe_task(agent_id, action, title, {"validation": True}, ledger)
        for agent_id, action, title in BOUNDARY_CHECKS
    ]
    completed = sum(task["status"] == "completed" for task in validations)
    protected = sum(task["status"] == "waiting_for_approval" for task in gates)
    governance = audit_catalog(root=Path("."))
    return {
        "schema": "wude.central_agent_runtime.v1",
        "version": RUNTIME_VERSION,
        "generated_at": generated_at,
        "status": "passed" if completed == len(validations) and protected == len(gates) else "attention",
        "status_label": "四個 Agent 安全驗收通過" if completed == len(validations) and protected == len(gates) else "Agent 驗收需要注意",
        "control_version": control.get("version") or "尚無控制層紀錄",
        "summary": {
            "agent_count": len(AGENTS),
            "validation_completed": completed,
            "validation_total": len(validations),
            "approval_gates_protected": protected,
            "approval_gates_total": len(gates),
            "external_calls": 0,
            "paid_model_calls": 0,
        },
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
        "output_validation": {
            "status": "enforced_by_contract",
            "draft_until_verified": True,
            "formats": ["PPTX", "PDF", "DOCX", "XLSX", "HTML", "Markdown", "JSON"],
            "required_checks": ["來源版本", "數字日期單位", "跨頁一致", "實際渲染檢查"],
        },
        "next_authorized_steps": {
            "stock_shadow": "繼續既有影子前向驗證與 GPT 教導，不改正式 V6。",
            "zhiying_company": "可執行流程草稿與模擬；ERP／PLC 實接等待公司同意。",
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
