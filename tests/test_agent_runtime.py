import json
import shutil
import zipfile
from pathlib import Path

from agent_control import UsageLedger
from agent_runtime import BOOTSTRAP_TASKS, build_runtime_report, execute_safe_task


def _runtime_root(tmp_path: Path) -> Path:
    """Create stable evidence so runtime tests never depend on live reports."""
    shutil.copytree("agent_workspaces", tmp_path / "agent_workspaces")
    reports = tmp_path / "reports"
    reports.mkdir()
    evidence = {
        "validation_60d.json": {
            "trading_days_collected": 23,
            "target_trading_days": 60,
            "eligible_samples": 120,
            "rules": {"automatic_weight_changes": False, "automatic_orders": False},
        },
        "model_learning.json": {"error_learning": {"independent_events": 7}},
        "system_guard.json": {"status": "ok"},
        "prediction_engine.json": {
            "policy": {
                "controlled_shadow_auto_promotion": True,
                "formal_v6_auto_promotion": False,
                "automatic_orders": False,
                "automatic_shadow_rollback_after_failures": 3,
            }
        },
        "model_unit_learning.json": {
            "summary": {
                "registered_units": 11,
                "matured_rows": 54132,
                "active_shadow_trust_streams": 0,
            },
            "policy": {"formal_v6_unchanged": True},
        },
        "weekly_shadow_coach.json": {"status": "ok", "mode": "shadow_only"},
    }
    for name, payload in evidence.items():
        (reports / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_all_three_agents_execute_one_safe_validation(tmp_path: Path):
    root = _runtime_root(tmp_path)
    report = build_runtime_report(root / "reports", root=root)
    assert report["status"] == "active"
    assert report["summary"]["agent_count"] == 3
    assert report["summary"]["actual_work_performed"] == 3
    assert report["summary"]["validation_completed"] == 1
    assert report["summary"]["waiting_input"] == 2
    assert report["summary"]["approval_gates_protected"] == 3
    assert len(report["validations"]) == len(BOOTSTRAP_TASKS)
    assert len({row["namespace"] for row in report["validations"]}) == 3
    assert all(row["external_side_effect"] is False for row in report["validations"])
    assert report["context_governance"]["status"] == "passed"
    assert report["context_governance"]["cross_domain_conflicts"] == 0
    assert report["context_governance"]["dynamic_loading"] is True
    assert report["output_validation"]["draft_until_verified"] is True


def test_runtime_never_persists_payload_content():
    secret = "PRIVATE-CUSTOMER-PRICE"
    row = execute_safe_task(
        "wt_fasteners",
        "simulate_workflow",
        "驗證流程",
        {"customer_price": secret},
        UsageLedger(),
    )
    assert row["status"] == "waiting_input"
    assert row["payload_stored"] is False
    assert secret not in json.dumps(row, ensure_ascii=False)


def test_duplicate_task_is_not_executed_twice():
    ledger = UsageLedger()
    first = execute_safe_task("wt_fasteners", "simulate_workflow", "測試", {"same": True}, ledger)
    second = execute_safe_task("wt_fasteners", "simulate_workflow", "測試", {"same": True}, ledger)
    assert first["status"] == "waiting_input"
    assert second["status"] == "duplicate"


def test_external_side_effect_waits_for_approval():
    row = execute_safe_task("packaging_startup", "payment", "付款測試", {"amount": 1}, UsageLedger())
    assert row["status"] == "waiting_for_approval"
    assert row["external_side_effect"] is False
    assert row["status_label"] == "等待授權"
    assert row["capability_plan"]["executor_loaded"] is False
    assert row["capability_plan"]["tools"] == []


def test_every_safe_task_gets_a_bounded_context_plan(tmp_path: Path):
    root = _runtime_root(tmp_path)
    row = execute_safe_task("stock_shadow", "shadow_validate", "股票影子驗證", {"validation": True}, UsageLedger(), root=root)
    assert row["status"] == "completed"
    assert row["context_plan"]["profile"] == "research"
    assert row["context_plan"]["cross_domain_reads"] is False
    assert row["context_plan"]["files"] <= 12
    assert row["capability_plan"]["cross_domain_tools"] is False


def test_actual_work_reports_evidence_instead_of_fake_completion(tmp_path: Path):
    root = _runtime_root(tmp_path)
    stock = execute_safe_task("stock_shadow", "shadow_validate", "股票影子驗證", {"validation": True}, UsageLedger(), root=root)
    packaging = execute_safe_task("packaging_startup", "simulate_workflow", "包裝試算", {"validation": True}, UsageLedger(), root=root)
    assert stock["actual_work_performed"] is True
    assert stock["evidence"]["validation_days"] > 0
    assert stock["evidence"]["formal_v6_locked"] is True
    assert stock["evidence"]["self_learning_enabled"] is True
    assert stock["evidence"]["matured_learning_rows"] > 0
    assert "railway_market_data" in stock["capability_plan"]["auto_connectors"]
    assert packaging["actual_work_performed"] is True
    assert packaging["status"] == "waiting_input"
    assert packaging["evidence"]["monthly_effective_capacity"] == 29568
    assert packaging["evidence"]["profit_calculated"] is False


def test_critical_report_freshness_stays_attention_and_recovers(tmp_path: Path):
    """Regression for run 36970903458: a stale guard is not completed work."""
    root = _runtime_root(tmp_path)
    guard_path = root / "reports" / "system_guard.json"
    guard_path.write_text(json.dumps({
        "status": "critical",
        "checks": [{"code": "report_freshness", "level": "critical",
                    "detail": "已 15.2 小時沒有完成新報表"}],
    }, ensure_ascii=False), encoding="utf-8")
    report = build_runtime_report(root / "reports", root=root)
    stock = next(row for row in report["validations"] if row["agent_id"] == "stock_shadow")
    assert stock["status"] == "attention"
    assert stock["evidence"]["guard_status"] == "critical"
    assert stock["evidence"]["formal_v6_locked"] is True
    assert stock["evidence"]["automatic_orders_blocked"] is True
    assert report["summary"]["validation_completed"] == 0
    assert report["summary"]["actual_work_performed"] == 3
    assert report["summary"]["waiting_input"] == 2
    assert report["summary"]["approval_gates_protected"] == 3
    assert report["summary"]["external_calls"] == 0
    assert report["summary"]["paid_model_calls"] == 0
    assert all(row["external_side_effect"] is False for row in report["validations"])

    guard_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    recovered = build_runtime_report(root / "reports", root=root)
    assert recovered["summary"]["validation_completed"] == 1


def test_private_connector_inputs_are_consumed_without_being_exposed(tmp_path: Path):
    import shutil
    shutil.copytree("agent_workspaces", tmp_path / "agent_workspaces")
    private = tmp_path / ".agent_private_inputs"
    private.mkdir()
    secret_values = {
        "known_inputs": {
            "每桶平均重量": 12.5,
            "每人月薪與雇主成本": 48000,
            "每月水電": 20000,
            "每桶包材": 0.5,
            "設備總價": 200000,
            "每月訂單上限": 10000,
        }
    }
    (private / "packaging_startup.json").write_text(json.dumps(secret_values, ensure_ascii=False), encoding="utf-8")
    row = execute_safe_task("packaging_startup", "simulate_workflow", "包裝試算", {}, UsageLedger(), root=tmp_path)
    assert row["status"] == "completed"
    assert row["evidence"]["private_connector_input_found"] is True
    assert row["evidence"]["missing_input_count"] == 0
    assert "48000" not in json.dumps(row, ensure_ascii=False)


def test_report_without_artifact_evidence_stays_draft():
    row = execute_safe_task("packaging_startup", "draft_report", "製作包裝創業簡報", {}, UsageLedger())
    assert row["status"] == "draft"
    assert row["status_label"] == "草稿；尚未提供輸出驗收"


def test_report_becomes_complete_only_after_artifact_validation(tmp_path: Path):
    (tmp_path / "agent_workspaces").mkdir()
    catalog = {
        "schema": "wude.context_catalog.v1",
        "policy": {"default_max_files": 12, "default_max_bytes": 200000},
        "layers": {},
        "domains": {
            key: {layer: [] for layer in ("canonical", "reference", "research", "temporary", "archive")}
            for key in ("stock_shadow", "wt_fasteners", "packaging_startup")
        },
        "task_profiles": {"output": ["canonical", "reference", "temporary"]},
    }
    (tmp_path / "agent_workspaces" / "context_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    (tmp_path / "report.md").write_text("# 已渲染並核對", encoding="utf-8")
    checks = {
        key: {"passed": True, "evidence": "測試證據"}
        for key in ("source_version", "numbers_dates_units", "cross_page_consistency", "visual_render_review")
    }
    row = execute_safe_task(
        "packaging_startup",
        "draft_report",
        "製作包裝創業簡報",
        {"artifact_validation": {"artifact": "report.md", "domain": "packaging_startup", "sources": ["source.json"], "checks": checks}},
        UsageLedger(),
        root=tmp_path,
    )
    assert row["status"] == "completed"
    assert row["artifact_validation"]["verified"] is True
    assert row["payload_stored"] is False


def test_ppt_report_requires_every_slide_render_before_completion(tmp_path: Path):
    (tmp_path / "agent_workspaces").mkdir()
    catalog = {
        "schema": "wude.context_catalog.v1",
        "policy": {"default_max_files": 12, "default_max_bytes": 200000},
        "layers": {},
        "domains": {
            key: {layer: [] for layer in ("canonical", "reference", "research", "temporary", "archive")}
            for key in ("stock_shadow", "wt_fasteners", "packaging_startup")
        },
        "task_profiles": {"output": ["canonical", "reference", "temporary"]},
    }
    (tmp_path / "agent_workspaces" / "context_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "report.pptx", "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("ppt/slides/slide1.xml", "<slide/>")
    (tmp_path / "slide-1.png").write_bytes(b"\x89PNG\r\n\x1a\nrender-evidence")
    checks = {
        key: {"passed": True, "evidence": "測試證據"}
        for key in ("source_version", "numbers_dates_units", "cross_page_consistency", "visual_render_review")
    }
    spec = {
        "artifact": "report.pptx",
        "domain": "packaging_startup",
        "sources": ["source.json"],
        "checks": checks,
        "presentation": {"expected_slide_count": 1, "rendered_slides": ["slide-1.png"]},
    }

    row = execute_safe_task(
        "packaging_startup",
        "draft_report",
        "製作包裝創業簡報",
        {"artifact_validation": spec},
        UsageLedger(),
        root=tmp_path,
    )

    assert row["status"] == "completed"
    assert row["artifact_validation"]["slide_count"] == 1
    assert row["artifact_validation"]["rendered_slide_count"] == 1
