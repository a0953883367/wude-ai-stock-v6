#!/usr/bin/env python3
"""唯讀驗證商業化缺口清單，防止沒有證據的項目被誤標完成。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "commercialization_readiness.json"
VALID_STATUSES = {
    "verified",
    "ready_for_testing",
    "collecting_evidence",
    "blocked_external",
}
REQUIRED_IDS = {
    "packaging-offsite-backup-restore",
    "packaging-key-rotation",
    "packaging-account-management",
    "packaging-outage-alert",
    "packaging-terms-contract",
    "packaging-ai-real-data-validation",
    "packaging-erp-device-integration",
    "packaging-offline-retry",
    "packaging-50-day-pilot",
    "packaging-training-support",
    "stock-paper-trading-fake-e2e",
    "stock-paper-trading-human-pilot",
    "stock-multi-user-isolation",
    "stock-market-data-license",
    "stock-subscription-billing-refund",
    "stock-20d-shadow-validation",
    "stock-60d-shadow-validation",
    "stock-gpt-shadow-coaching",
    "stock-key-rotation",
}


def inspect_manifest(path: Path = MANIFEST, root: Path = ROOT) -> dict[str, Any]:
    errors: list[str] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    ids = [str(item.get("id", "")).strip() for item in items]
    if len(ids) != len(set(ids)):
        errors.append("缺口編號不得重複")
    missing = sorted(REQUIRED_IDS - set(ids))
    if missing:
        errors.append("缺少必要項目：" + ", ".join(missing))

    summary = {status: 0 for status in VALID_STATUSES}
    for item in items:
        item_id = str(item.get("id", "未命名"))
        status = item.get("status")
        evidence = item.get("evidence")
        blocker = str(item.get("blocked_by", "")).strip()
        next_action = str(item.get("next_action", "")).strip()
        if status not in VALID_STATUSES:
            errors.append(f"{item_id} 使用未知狀態")
            continue
        summary[status] += 1
        if not next_action:
            errors.append(f"{item_id} 缺少下一步")
        if not isinstance(evidence, list):
            errors.append(f"{item_id} 的 evidence 必須是陣列")
            continue
        if status == "verified":
            if not evidence:
                errors.append(f"{item_id} 標為完成但沒有證據")
            for relative in evidence:
                evidence_path = root / str(relative)
                if evidence_path.is_absolute() and not str(evidence_path).startswith(str(root)):
                    errors.append(f"{item_id} 證據不得指向儲存庫外部")
                elif not evidence_path.is_file():
                    errors.append(f"{item_id} 的證據不存在：{relative}")
        if status == "blocked_external" and not blocker:
            errors.append(f"{item_id} 標為外部阻塞但沒有說明原因")

    return {
        "status": "passed" if not errors else "failed",
        "scope": "只驗證清單、證據檔與阻塞說明；不連外部系統、不讀祕密、不修改正式資料",
        "summary": summary,
        "errors": errors,
    }


def main() -> int:
    result = inspect_manifest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
