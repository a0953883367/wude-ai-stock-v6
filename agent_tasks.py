"""Build a human-readable task centre for the four isolated agents.

The task centre reports progress and blockers only.  It does not execute paid
calls, publish externally, change stock weights, or write to company systems.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_VERSION = "CENTRAL-AGENT-TASKS-V1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _exists(root: Path, relative: str) -> bool:
    return (root / relative).is_file()


def build_task_report(root: Path, reports_dir: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    validation = _read_json(reports_dir / "validation_60d.json")
    progress = _read_json(reports_dir / "validation_progress_monitor.json")
    coach = _read_json(reports_dir / "weekly_shadow_coach.json")
    days = int(validation.get("trading_days_collected") or 0)
    target = int(validation.get("target_trading_days") or 60)
    monitor_status = str(progress.get("status") or "尚無紀錄")
    coach_ready = bool(coach.get("generated_at"))

    tasks = [
        {
            "id": "central_task_center",
            "agent_id": "central_control",
            "agent_name": "中央 AI",
            "title": "建立任務中心",
            "status": "completed",
            "status_label": "已完成",
            "progress_label": "可查看負責助理、進度、下一步與阻塞原因",
            "next_action": "持續由每日排程更新狀態",
            "blocked_by": [],
        },
        {
            "id": "stock_forward_validation",
            "agent_id": "stock_shadow",
            "agent_name": "武得股票影子驗證 Agent",
            "title": "股票3／5日結算與60日影子驗證",
            "status": "collecting" if days < target else "review_ready",
            "status_label": "累積中" if days < target else "可進行60日審查",
            "progress_label": f"已完成 {days}／{target} 個交易日；監控狀態：{monitor_status}",
            "next_action": "依官方完成交易日自動結算；成熟前不改正式權重",
            "blocked_by": [] if days >= target else [f"尚需 {max(target - days, 0)} 個有效交易日"],
            "checks": {
                "gpt_shadow_teaching": "已有紀錄" if coach_ready else "等待首次週教導",
                "formal_v6_locked": True,
            },
        },
        {
            "id": "zhiying_connector_pack",
            "agent_id": "zhiying_company",
            "agent_name": "至盈公司 Agent",
            "title": "ERP／PLC／掃描／列印模擬串接包",
            "status": "prepared" if _exists(root, "agent_workspaces/zhiying_company/連線模擬與驗收規格.json") else "not_started",
            "status_label": "模擬規格已備妥" if _exists(root, "agent_workspaces/zhiying_company/連線模擬與驗收規格.json") else "尚未建立",
            "progress_label": "可先做欄位映射、唯讀測試與驗收，不接正式設備",
            "next_action": "取得公司同意及測試環境後，才進行實際連線",
            "blocked_by": ["公司授權", "ERP／PLC測試環境"],
            "artifact_url": "agent_workspaces/zhiying_company/連線模擬與驗收規格.json",
            "artifact_label": "查看模擬串接規格",
        },
        {
            "id": "wt_product_cost_template",
            "agent_id": "wt_fasteners",
            "agent_name": "WT 螺絲電商 Agent",
            "title": "商品、成本、售價與毛利輸入表",
            "status": "waiting_input" if _exists(root, "agent_workspaces/wt_fasteners/WT商品與毛利輸入表.xlsx") else "not_started",
            "status_label": "表格已建立，等待資料" if _exists(root, "agent_workspaces/wt_fasteners/WT商品與毛利輸入表.xlsx") else "尚未建立",
            "progress_label": "商品規格已預填；成本空白不會被當成0元",
            "next_action": "填入螺絲、包材、人工、運費與平台費用",
            "blocked_by": ["實際成本資料", "商店帳號連線"] ,
            "artifact_url": "agent_workspaces/wt_fasteners/WT商品與毛利輸入表.xlsx",
            "artifact_label": "下載商品與毛利輸入表",
        },
        {
            "id": "packaging_feasibility_model",
            "agent_id": "packaging_startup",
            "agent_name": "包裝創業 Agent",
            "title": "機器產能與損益試算",
            "status": "waiting_input" if _exists(root, "agent_workspaces/packaging_startup/包裝創業產能與損益試算.xlsx") else "not_started",
            "status_label": "試算表已建立，等待成本" if _exists(root, "agent_workspaces/packaging_startup/包裝創業產能與損益試算.xlsx") else "尚未建立",
            "progress_label": "已放入4桶／分鐘、8小時、稼動70%、6人與月租8萬元",
            "next_action": "補上工資、水電、包材、設備與每桶平均重量",
            "blocked_by": ["設備正式報價", "工資與水電", "預估訂單量"],
            "artifact_url": "agent_workspaces/packaging_startup/包裝創業產能與損益試算.xlsx",
            "artifact_label": "下載產能與損益試算",
        },
    ]
    counts = {
        "total": len(tasks),
        "completed": sum(row["status"] == "completed" for row in tasks),
        "running": sum(row["status"] in {"collecting", "prepared", "review_ready"} for row in tasks),
        "waiting_input": sum(row["status"] == "waiting_input" for row in tasks),
    }
    return {
        "schema": "wude.central_agent_tasks.v1",
        "version": TASK_VERSION,
        "generated_at": now,
        "status": "active",
        "status_label": "未完成工作已納入任務中心",
        "summary": counts,
        "tasks": tasks,
        "safety": {
            "formal_stock_weights_changed": False,
            "orders_placed": False,
            "payments_made": False,
            "external_messages_sent": False,
            "company_devices_connected": False,
        },
    }


def write_task_report(root: Path, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "agent_tasks.json"
    target.write_text(
        json.dumps(build_task_report(root, reports_dir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="更新中央 AI 任務中心")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    print(write_task_report(args.root, args.reports_dir))


if __name__ == "__main__":
    main()
