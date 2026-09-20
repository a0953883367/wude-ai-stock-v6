#!/usr/bin/env python3
"""唯讀驗證真實下單安全鎖；不登入券商、不送出委託。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fubon_broker import FubonTradingSession, live_order_unlock_reason


BASE_LIVE_ENV = {
    "DEPLOYMENT_ENVIRONMENT": "production",
    "TRADING_MODE": "live",
    "LIVE_TRADING_ENABLED": "true",
    "TRADE_STATE_PATH": "/data/live_trading_state.json",
    "LIVE_TRADING_CONFIRMATION": "server-only-confirmation",
    "LIVE_TRADING_CONFIRMATION_INPUT": "server-only-confirmation",
}


def inspect_live_trading_gate() -> dict[str, object]:
    locked_cases = {
        "空白設定維持鎖定": {},
        "只有正式環境仍維持鎖定": {"DEPLOYMENT_ENVIRONMENT": "production"},
        "未開啟真單旗標仍維持鎖定": {
            "DEPLOYMENT_ENVIRONMENT": "production",
            "TRADING_MODE": "live",
        },
        "非永久狀態路徑維持鎖定": {
            **BASE_LIVE_ENV,
            "TRADE_STATE_PATH": "/tmp/live_trading_state.json",
        },
        "模擬與真單共用路徑維持鎖定": {
            **BASE_LIVE_ENV,
            "TRADE_STATE_PATH": "/data/paper_trading_state.json",
        },
        "確認碼不一致維持鎖定": {
            **BASE_LIVE_ENV,
            "LIVE_TRADING_CONFIRMATION_INPUT": "different-value",
        },
    }
    checks: list[dict[str, object]] = []
    for name, environ in locked_cases.items():
        reason = live_order_unlock_reason(environ)
        checks.append({"name": name, "passed": bool(reason), "detail": reason or "安全鎖未生效"})

    with patch.dict(os.environ, {}, clear=True):
        session = FubonTradingSession(sdk=object(), account="test-account")
        try:
            session.place_limit_order(symbol="2330", side="BUY", quantity=1, price=100)
        except PermissionError:
            order_path_blocked = True
        else:
            order_path_blocked = False
    checks.append({
        "name": "預設環境在載入券商下單元件前即拒絕",
        "passed": order_path_blocked,
        "detail": "測試不登入券商，也不建立或送出委託",
    })

    checks.append({
        "name": "只有全部人工安全條件齊全才可解除程式鎖",
        "passed": live_order_unlock_reason(BASE_LIVE_ENV) is None,
        "detail": "此項只驗證判斷函式，不套用環境變數、不連線券商",
    })
    return {
        "status": "passed" if all(bool(item["passed"]) for item in checks) else "blocked",
        "scope": "只驗證真單安全鎖；不登入券商、不讀取帳戶、不送出委託",
        "checks": checks,
    }


def main() -> int:
    result = inspect_live_trading_gate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
