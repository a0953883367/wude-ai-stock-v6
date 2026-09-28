#!/usr/bin/env python3
"""Fetch private Agent inputs from an already-authorized Google Drive folder.

The downloader is read-only, accepts two fixed JSON filenames, keeps their
contents out of git, and emits only connection/completeness metadata publicly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from google_drive_archive import DRIVE_FILES_URL, FOLDER_MIME_TYPE, DriveArchiveClient


ROOT_FOLDER = "武得AI Agent輸入"
FILES = {
    "WT自動輸入.json": "wt_fasteners.json",
    "包裝創業自動輸入.json": "packaging_startup.json",
}
MAX_BYTES = 256_000


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_inputs(output_dir: Path, status_file: Path) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    credentials = {
        "client_id": os.getenv("GOOGLE_DRIVE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", ""),
        "refresh_token": os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", ""),
    }
    status: dict[str, Any] = {
        "schema": "wude.agent_input_connector.v1",
        "generated_at": generated_at,
        "connector": "google_drive_evidence",
        "access": "read_only",
        "configured": all(credentials.values()),
        "downloaded": [],
        "missing": [],
        "errors": [],
        "private_values_published": False,
    }
    if not status["configured"]:
        status.update(status="not_configured", status_label="Google Drive 尚未授權給此流程")
        _write(status_file, status)
        return status

    try:
        client = DriveArchiveClient(**credentials)
        client.authenticate()
        folder = client.find_file(ROOT_FOLDER, "root", mime_type=FOLDER_MIME_TYPE)
        if not folder:
            status.update(status="waiting_source", status_label=f"等待雲端資料夾：{ROOT_FOLDER}")
            status["missing"] = list(FILES)
            _write(status_file, status)
            return status
        for drive_name, local_name in FILES.items():
            item = client.find_file(drive_name, str(folder["id"]), mime_type="application/json")
            if not item:
                status["missing"].append(drive_name)
                continue
            response = client.session.get(
                f"{DRIVE_FILES_URL}/{item['id']}",
                headers=client._headers(),  # authenticated, read-only request
                params={"alt": "media"},
                timeout=client.timeout,
            )
            response.raise_for_status()
            payload = response.content
            if len(payload) > MAX_BYTES:
                raise ValueError(f"{drive_name} exceeds {MAX_BYTES} bytes")
            value = json.loads(payload.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"{drive_name} must contain one JSON object")
            _write(output_dir / local_name, value)
            status["downloaded"].append(drive_name)
        status.update(
            status="ok" if not status["missing"] else "partial",
            status_label="自動輸入已下載" if not status["missing"] else "已自動搜尋；部分來源尚未出現",
        )
    except Exception as exc:  # isolated connector failure must not stop other agents
        status.update(status="error", status_label="Google Drive 自動讀取失敗")
        status["errors"].append(type(exc).__name__)
    _write(status_file, status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(".agent_private_inputs"))
    parser.add_argument("--status-file", type=Path, default=Path("reports/agent_input_connectors.json"))
    args = parser.parse_args()
    result = fetch_inputs(args.output_dir, args.status_file)
    print(result["status_label"])


if __name__ == "__main__":
    main()
