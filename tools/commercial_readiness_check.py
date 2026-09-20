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
    "LIVE_PUBLIC_READ": "0",
    "FUBON_AUTO_GIT": "0",
}

SECRET_EXAMPLE_KEYS = (
    "FINMIND_TOKEN",
    "TIINGO_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_LIVE_BOT_TOKEN",
    "TELEGRAM_LIVE_CHAT_ID",
    "TELEGRAM_FRIEND_ALERT_CHAT_ID",
    "FUBON_ID",
    "FUBON_API_KEY",
    "FUBON_PASSWORD",
    "FUBON_CERT_PATH",
    "FUBON_CERT_PASSWORD",
    "FUBON_CERT_BASE64",
    "LIVE_ACCESS_TOKEN",
    "LIVE_TRADING_CONFIRMATION",
    "LIVE_TRADING_CONFIRMATION_INPUT",
    "LIVE_TRUSTED_AUTH_HEADER",
    "ALPACA_API_KEY_ID",
    "ALPACA_API_SECRET_KEY",
)

REQUIRED_GITIGNORE_LINES = (
    ".env",
    "*.p12",
    "*.pfx",
    "*.pem",
    "*.key",
    "fubon_local_config.json",
    "reports/owner_private_holding_simulation.json",
)

REQUIRED_WORKFLOW_COMMANDS = (
    "python tools/security_preflight.py",
    "python tools/commercial_readiness_check.py",
    "python -m unittest tests.test_commercial_readiness_check",
    "python tools/live_trading_safety_check.py",
    "python -m unittest tests.test_live_trading_safety_check",
)

REQUIRED_FILES = {
    "敏感資料掃描": "tools/security_preflight.py",
    "預測證據備份": "tools/prediction_evidence_backup.py",
    "歷史資料封存": "history_archive.py",
    "還原安全機制": "prediction_engine/evidence_backup.py",
    "私密弱點通報流程": "SECURITY.md",
    "依賴套件自動更新監控": ".github/dependabot.yml",
    "真實下單安全鎖驗證": "tools/live_trading_safety_check.py",
    "真實下單安全鎖單元測試": "tests/test_live_trading_safety_check.py",
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
    for key in SECRET_EXAMPLE_KEYS:
        checks.append({
            "name": f"{key} 範例值不得含密鑰",
            "passed": env_values.get(key, "") == "",
            "detail": "範例環境檔必須留空；正式值只能放在祕密管理服務",
        })

    state_path = env_values.get("TRADE_STATE_PATH", "")
    checks.append({
        "name": "正式交易狀態使用永久且獨立的路徑",
        "passed": state_path.startswith("/data/") and "paper" not in Path(state_path).name.lower(),
        "detail": "TRADE_STATE_PATH 必須位於 /data/，且不得與模擬交易共用",
    })

    gitignore_path = root / ".gitignore"
    gitignore_lines = {
        line.strip()
        for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    } if gitignore_path.exists() else set()
    for pattern in REQUIRED_GITIGNORE_LINES:
        checks.append({
            "name": f"敏感檔案忽略規則：{pattern}",
            "passed": pattern in gitignore_lines,
            "detail": ".gitignore 必須明確阻擋此類檔案",
        })

    workflow_path = root / ".github/workflows/security-preflight.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    for command in REQUIRED_WORKFLOW_COMMANDS:
        checks.append({
            "name": f"GitHub 自動安全檢查：{command}",
            "passed": command in workflow_text,
            "detail": "每次推送與合併請求都必須自動執行",
        })
    for name, relative in REQUIRED_FILES.items():
        checks.append({
            "name": name,
            "passed": (root / relative).is_file(),
            "detail": relative,
        })
    return {
        "status": "ready_for_testing" if all(item["passed"] for item in checks) else "blocked",
        "scope": "安全模式、密鑰隔離、敏感檔案阻擋、備份還原與自動驗收；不檢查或變更選股分數",
        "checks": checks,
    }


def main() -> int:
    result = inspect_repository(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready_for_testing" else 1


if __name__ == "__main__":
    raise SystemExit(main())
