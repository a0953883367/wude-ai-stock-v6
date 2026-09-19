import json
from pathlib import Path

from agent_tasks import build_task_report, write_task_report


def test_task_center_preserves_business_separation_and_safety(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "validation_60d.json").write_text(
        json.dumps({"trading_days_collected": 20, "target_trading_days": 60}),
        encoding="utf-8",
    )
    (reports / "validation_progress_monitor.json").write_text(
        json.dumps({"status": "ok"}), encoding="utf-8"
    )
    report = build_task_report(tmp_path, reports)
    assert report["summary"]["total"] == 5
    stock = next(row for row in report["tasks"] if row["id"] == "stock_forward_validation")
    assert stock["status"] == "collecting"
    assert stock["blocked_by"] == ["尚需 40 個有效交易日"]
    assert report["safety"] == {
        "formal_stock_weights_changed": False,
        "orders_placed": False,
        "payments_made": False,
        "external_messages_sent": False,
        "company_devices_connected": False,
    }


def test_task_center_detects_prepared_files(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    targets = [
        "agent_workspaces/zhiying_company/連線模擬與驗收規格.json",
        "agent_workspaces/wt_fasteners/WT商品與毛利輸入表.xlsx",
        "agent_workspaces/packaging_startup/包裝創業產能與損益試算.xlsx",
    ]
    for relative in targets:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    report = build_task_report(tmp_path, reports)
    statuses = {row["id"]: row["status"] for row in report["tasks"]}
    assert statuses["zhiying_connector_pack"] == "prepared"
    assert statuses["wt_product_cost_template"] == "waiting_input"
    assert statuses["packaging_feasibility_model"] == "waiting_input"
    target = write_task_report(tmp_path, reports)
    assert json.loads(target.read_text(encoding="utf-8"))["version"]
