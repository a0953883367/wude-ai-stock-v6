"""Build the central artifact handoff registry without crossing data domains."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY_SCHEMA = "wude.artifact_registry.v1"
ALLOWED_DOMAINS = {"stock_shadow", "zhiying_company", "wt_fasteners", "packaging_startup"}
STATUS_LABELS = {
    "ready_for_handoff": "可交付",
    "draft": "草稿；等待驗收",
    "waiting_approval": "等待授權",
    "waiting_input": "等待資料",
    "in_progress": "製作中",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _task_status(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "")
    blocked = " ".join(str(item) for item in task.get("blocked_by", []) if item)
    if status == "waiting_input":
        return "waiting_input"
    if status in {"collecting", "running", "review_ready"}:
        return "in_progress"
    if status == "prepared" and any(word in blocked for word in ("授權", "同意", "核准")):
        return "waiting_approval"
    return "draft"


def _base_items(root: Path, tasks_report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in tasks_report.get("tasks", []):
        if not isinstance(task, dict) or not task.get("artifact_url"):
            continue
        domain = str(task.get("agent_id") or "")
        artifact = str(task.get("artifact_url") or "")
        artifact_path = root / artifact
        if domain not in ALLOWED_DOMAINS or not _inside(root, artifact_path):
            continue
        status = _task_status(task)
        items.append(
            {
                "id": str(task.get("id") or artifact),
                "agent_id": domain,
                "agent_name": str(task.get("agent_name") or domain),
                "title": str(task.get("title") or artifact_path.name),
                "artifact": artifact,
                "artifact_label": str(task.get("artifact_label") or "開啟檔案"),
                "artifact_exists": artifact_path.is_file(),
                "status": status,
                "status_label": STATUS_LABELS[status],
                "next_action": str(task.get("next_action") or "完成驗收後才可交付"),
                "blocked_by": [str(item) for item in task.get("blocked_by", []) if item],
                "validation_record": None,
                "verified": False,
            }
        )
    return items


def _delivery_records(root: Path, records_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not records_dir.is_dir():
        return records
    for path in sorted(records_dir.glob("*.json")):
        record = _read_json(path)
        artifact = str(record.get("artifact") or "")
        domain = str(record.get("domain") or "")
        artifact_path = root / artifact
        if not artifact or domain not in ALLOWED_DOMAINS or not _inside(root, artifact_path):
            continue
        records[artifact] = {
            "record": str(path.relative_to(root)),
            "verified": record.get("verified") is True,
            "status": str(record.get("status") or "draft"),
            "failures": [str(item) for item in record.get("failures", []) if item],
            "slide_count": record.get("slide_count"),
            "rendered_slide_count": record.get("rendered_slide_count"),
        }
    return records


def build_artifact_registry(*, root: Path = Path(".")) -> dict[str, Any]:
    tasks_report = _read_json(root / "reports" / "agent_tasks.json")
    items = _base_items(root, tasks_report)
    records = _delivery_records(root, root / "reports" / "artifact_deliveries")
    known = {item["artifact"] for item in items}

    for item in items:
        record = records.get(item["artifact"])
        if not record:
            continue
        item["validation_record"] = record["record"]
        item["verified"] = record["verified"]
        item["failures"] = record["failures"]
        item["slide_count"] = record["slide_count"]
        item["rendered_slide_count"] = record["rendered_slide_count"]
        item["status"] = "ready_for_handoff" if record["verified"] else "draft"
        item["status_label"] = STATUS_LABELS[item["status"]]

    for artifact, record in records.items():
        if artifact in known:
            continue
        artifact_path = root / artifact
        status = "ready_for_handoff" if record["verified"] else "draft"
        items.append(
            {
                "id": f"delivery:{artifact}",
                "agent_id": "unknown",
                "agent_name": "尚待任務對應",
                "title": artifact_path.name,
                "artifact": artifact,
                "artifact_label": "開啟檔案",
                "artifact_exists": artifact_path.is_file(),
                "status": status,
                "status_label": STATUS_LABELS[status],
                "next_action": "將交付紀錄對應到正確助理任務",
                "blocked_by": [],
                "validation_record": record["record"],
                "verified": record["verified"],
                "failures": record["failures"],
                "slide_count": record["slide_count"],
                "rendered_slide_count": record["rendered_slide_count"],
            }
        )

    counts = {status: sum(item["status"] == status for item in items) for status in STATUS_LABELS}
    return {
        "schema": REGISTRY_SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "active",
        "status_label": "交付清冊已啟用",
        "summary": {"total": len(items), **counts},
        "items": items,
        "safety": {
            "cross_domain_reads": False,
            "external_publish": False,
            "external_messages_sent": False,
            "formal_stock_system_changed": False,
        },
    }


def write_artifact_registry(*, root: Path = Path("."), output: Path = Path("reports/artifact_registry.json")) -> Path:
    target = root / output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_artifact_registry(root=root), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="產生中央 AI 任務交付清冊")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("reports/artifact_registry.json"))
    args = parser.parse_args()
    print(write_artifact_registry(root=args.root, output=args.output))


if __name__ == "__main__":
    main()
