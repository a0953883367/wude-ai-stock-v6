#!/usr/bin/env python3
"""唯讀檢查商業化前安全條件；不讀取或輸出任何祕密值。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SAFE_DEFAULTS = {
    "DEPLOYMENT_ENVIRONMENT": "test",
    "TRADING_MODE": "paper",
    "LIVE_TRADING_ENABLED": "false",
}

REQUIRED_FILES = {
    "敏感資料掃描": "tools/security_preflight.py",
    "預測證據備份": "tools/prediction_evidence_backup.py",
    "歷史資料封存": "history_archive.py",
    "還原安全機制": "prediction_engine/evidence_backup.py",
}


def read_example_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def inspect_repository(root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    env_path = root / ".env.example"
    env_values = read_example_env(env_path) if env_path.exists() else {}
    for key, expected in SAFE_DEFAULTS.items():
        checks.append({
            "name": f"{key} 安全預設",
            "passed": env_values.get(key, "") == expected,
            "detail": f"應為 {expected}",
        })
    for name, relative in REQUIRED_FILES.items():
        checks.append({
            "name": name,
            "passed": (root / relative).is_file(),
            "detail": relative,
        })
    return {
        "status": "ready_for_testing" if all(item["passed"] for item in checks) else "blocked",
        "scope": "安全模式、備份與還原能力；不檢查或變更選股分數",
        "checks": checks,
    }


def main() -> int:
    result = inspect_repository(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready_for_testing" else 1


if __name__ == "__main__":
    raise SystemExit(main())
