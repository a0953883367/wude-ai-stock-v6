import json
import zipfile
from pathlib import Path

from agent_control import UsageLedger
from agent_runtime import BOOTSTRAP_TASKS, build_runtime_report, execute_safe_task


def test_all_four_agents_execute_one_safe_validation(tmp_path: Path):
    report = build_runtime_report(tmp_path)
    assert report["status"] == "passed"
    assert report["summary"]["agent_count"] == 4
    assert report["summary"]["validation_completed"] == 4
    assert report["summary"]["approval_gates_protected"] == 4
    assert len(report["validations"]) == len(BOOTSTRAP_TASKS)
    assert len({row["namespace"] for row in report["validations"]}) == 4
    assert all(row["external_side_effect"] is False for row in report["validations"])
    assert report["context_governance"]["status"] == "passed"
    assert report["context_governance"]["cross_domain_conflicts"] == 0
    assert report["context_governance"]["dynamic_loading"] is True
    assert report["output_validation"]["draft_until_verified"] is True


def test_runtime_never_persists_payload_content():
    secret = "PRIVATE-CUSTOMER-PRICE"
    row = execute_safe_task(
        "zhiying_company",
        "simulate_workflow",
        "驗證流程",
        {"customer_price": secret},
        UsageLedger(),
    )
    assert row["status"] == "completed"
    assert row["payload_stored"] is False
    assert secret not in json.dumps(row, ensure_ascii=False)


def test_duplicate_task_is_not_executed_twice():
    ledger = UsageLedger()
    first = execute_safe_task("wt_fasteners", "simulate_workflow", "測試", {"same": True}, ledger)
    second = execute_safe_task("wt_fasteners", "simulate_workflow", "測試", {"same": True}, ledger)
    assert first["status"] == "completed"
    assert second["status"] == "duplicate"


def test_external_side_effect_waits_for_approval():
    row = execute_safe_task("packaging_startup", "payment", "付款測試", {"amount": 1}, UsageLedger())
    assert row["status"] == "waiting_for_approval"
    assert row["external_side_effect"] is False
    assert row["status_label"] == "等待授權"
    assert row["capability_plan"]["executor_loaded"] is False
    assert row["capability_plan"]["tools"] == []


def test_every_safe_task_gets_a_bounded_context_plan():
    row = execute_safe_task("stock_shadow", "shadow_validate", "股票影子驗證", {"validation": True}, UsageLedger())
    assert row["status"] == "completed"
    assert row["context_plan"]["profile"] == "research"
    assert row["context_plan"]["cross_domain_reads"] is False
    assert row["context_plan"]["files"] <= 12
    assert row["capability_plan"]["cross_domain_tools"] is False


def test_report_without_artifact_evidence_stays_draft():
    row = execute_safe_task("zhiying_company", "draft_report", "製作至盈簡報", {}, UsageLedger())
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
            for key in ("stock_shadow", "zhiying_company", "wt_fasteners", "packaging_startup")
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
        "zhiying_company",
        "draft_report",
        "製作至盈簡報",
        {"artifact_validation": {"artifact": "report.md", "domain": "zhiying_company", "sources": ["source.json"], "checks": checks}},
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
            for key in ("stock_shadow", "zhiying_company", "wt_fasteners", "packaging_startup")
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
        "domain": "zhiying_company",
        "sources": ["source.json"],
        "checks": checks,
        "presentation": {"expected_slide_count": 1, "rendered_slides": ["slide-1.png"]},
    }

    row = execute_safe_task(
        "zhiying_company",
        "draft_report",
        "製作至盈簡報",
        {"artifact_validation": spec},
        UsageLedger(),
        root=tmp_path,
    )

    assert row["status"] == "completed"
    assert row["artifact_validation"]["slide_count"] == 1
    assert row["artifact_validation"]["rendered_slide_count"] == 1
