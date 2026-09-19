import json
from pathlib import Path

from artifact_registry import build_artifact_registry, write_artifact_registry


def _task_report(artifact: str, *, status: str = "waiting_input", blocked=None) -> dict:
    return {
        "tasks": [
            {
                "id": "task-1",
                "agent_id": "wt_fasteners",
                "agent_name": "WT 螺絲電商 Agent",
                "title": "商品毛利表",
                "status": status,
                "artifact_url": artifact,
                "artifact_label": "開啟毛利表",
                "next_action": "填入實際成本",
                "blocked_by": blocked or ["實際成本資料"],
            }
        ]
    }


def test_registry_tracks_existing_task_artifact_without_marking_it_verified(tmp_path: Path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "agent_workspaces" / "wt_fasteners").mkdir(parents=True)
    artifact = "agent_workspaces/wt_fasteners/margin.xlsx"
    (tmp_path / artifact).write_bytes(b"spreadsheet")
    (tmp_path / "reports" / "agent_tasks.json").write_text(json.dumps(_task_report(artifact)), encoding="utf-8")

    registry = build_artifact_registry(root=tmp_path)

    assert registry["summary"]["total"] == 1
    assert registry["items"][0]["artifact_exists"] is True
    assert registry["items"][0]["status"] == "waiting_input"
    assert registry["items"][0]["verified"] is False
    assert registry["safety"]["cross_domain_reads"] is False


def test_verified_delivery_record_promotes_matching_artifact(tmp_path: Path):
    (tmp_path / "reports" / "artifact_deliveries").mkdir(parents=True)
    (tmp_path / "agent_workspaces" / "wt_fasteners").mkdir(parents=True)
    artifact = "agent_workspaces/wt_fasteners/margin.xlsx"
    (tmp_path / artifact).write_bytes(b"spreadsheet")
    (tmp_path / "reports" / "agent_tasks.json").write_text(json.dumps(_task_report(artifact)), encoding="utf-8")
    record = {"artifact": artifact, "domain": "wt_fasteners", "verified": True, "status": "verified", "failures": []}
    (tmp_path / "reports" / "artifact_deliveries" / "margin.json").write_text(json.dumps(record), encoding="utf-8")

    registry = build_artifact_registry(root=tmp_path)

    assert registry["items"][0]["status"] == "ready_for_handoff"
    assert registry["items"][0]["status_label"] == "可交付"
    assert registry["items"][0]["validation_record"].endswith("margin.json")


def test_prepared_company_artifact_waits_for_authorization(tmp_path: Path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "agent_workspaces" / "wt_fasteners").mkdir(parents=True)
    artifact = "agent_workspaces/wt_fasteners/spec.json"
    (tmp_path / artifact).write_text("{}", encoding="utf-8")
    tasks = _task_report(artifact, status="prepared", blocked=["公司授權"])
    (tmp_path / "reports" / "agent_tasks.json").write_text(json.dumps(tasks), encoding="utf-8")

    registry = build_artifact_registry(root=tmp_path)

    assert registry["items"][0]["status"] == "waiting_approval"


def test_registry_writer_creates_public_summary(tmp_path: Path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "agent_tasks.json").write_text('{"tasks": []}', encoding="utf-8")

    target = write_artifact_registry(root=tmp_path)
    data = json.loads(target.read_text(encoding="utf-8"))

    assert data["schema"] == "wude.artifact_registry.v1"
    assert data["summary"]["total"] == 0
