import json
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
