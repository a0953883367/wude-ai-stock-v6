"""Verified delivery for fixed stock reports.

This module records report generation and Telegram delivery separately.  It
never changes rankings, weights, shadow ledgers, or trading configuration.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from config import SETTINGS
from notifier import send_telegram


TAIPEI = ZoneInfo("Asia/Taipei")
LIVE_HEALTH_URL = "https://wude-ai-stock-v6-production.up.railway.app/health"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def record_delivery(
    reports_dir: Path,
    *,
    period: str,
    report_updated_at: str,
    state: str,
    delivered: bool,
    expected_delivery: bool,
    detail: str,
    checked_at: datetime | None = None,
    relay_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = (checked_at or datetime.now(TAIPEI)).astimezone(TAIPEI)
    payload = {
        "checked_at": now.isoformat(timespec="seconds"),
        "period": period,
        "report_updated_at": report_updated_at,
        "channel": "telegram_v6",
        "state": state,
        "delivered": bool(delivered),
        "expected_delivery": bool(expected_delivery),
        "detail": detail,
        "relay_health": relay_health or {},
        "safety": {
            "changes_rankings": False,
            "changes_weights": False,
            "changes_shadow_source_data": False,
            "places_orders": False,
        },
    }
    _atomic_json(reports_dir / "report_delivery_status.json", payload)
    return payload


def _parse_taipei(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=TAIPEI) if parsed.tzinfo is None else parsed.astimezone(TAIPEI)


def validate_fixed_report(
    report: dict[str, Any],
    *,
    expected_period: str,
    now: datetime | None = None,
    max_age_minutes: int = 120,
) -> tuple[bool, list[str]]:
    current = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    reasons: list[str] = []
    updated = _parse_taipei(report.get("updated_at"))
    if report.get("period") != expected_period:
        reasons.append(f"時段不是 {expected_period}")
    if report.get("run_mode") != "scheduled_report":
        reasons.append("不是固定報表")
    if updated is None:
        reasons.append("更新時間無法辨識")
    else:
        age = (current - updated).total_seconds() / 60
        if updated.date() != current.date():
            reasons.append("不是今日報表")
        if age < -10 or age > max_age_minutes:
            reasons.append(f"報表距今 {age:.0f} 分鐘")
    status = report.get("data_status") if isinstance(report.get("data_status"), dict) else {}
    if int(status.get("us_sip_count") or 0) <= 0:
        reasons.append("SIP 資料為 0")
    if int(status.get("us_opra_count") or 0) <= 0:
        reasons.append("OPRA 資料為 0")
    return not reasons, reasons


def _relay_health(get: Callable[..., Any] = requests.get) -> dict[str, Any]:
    try:
        response = get(LIVE_HEALTH_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return {"reachable": False}
    return {
        "reachable": True,
        "ok": payload.get("ok") is True,
        "us_sip_configured": payload.get("us_sip_configured") is True,
        "us_opra_configured": payload.get("us_opra_configured") is True,
    }


def deliver_verified_report(
    reports_dir: Path,
    *,
    period: str,
    now: datetime | None = None,
    max_age_minutes: int = 120,
    sender: Callable[[str], bool] = send_telegram,
    health_get: Callable[..., Any] = requests.get,
) -> bool:
    current = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    report_path = reports_dir / "latest.json"
    markdown_path = reports_dir / "latest.md"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        report, markdown = {}, ""
    valid, reasons = validate_fixed_report(
        report, expected_period=period, now=current,
        max_age_minutes=max_age_minutes,
    )
    if not markdown:
        reasons.append("報表文字為空")
        valid = False
    relay = _relay_health(health_get)
    if relay.get("reachable") and not all(
        relay.get(key) for key in ("ok", "us_sip_configured", "us_opra_configured")
    ):
        reasons.append("Railway SIP／OPRA 健康檢查未通過")
        valid = False
    updated_at = str(report.get("updated_at") or "")
    if not valid:
        record_delivery(
            reports_dir, period=period, report_updated_at=updated_at,
            state="blocked_stale_or_incomplete", delivered=False,
            expected_delivery=True, detail="；".join(reasons),
            checked_at=current, relay_health=relay,
        )
        return False
    delivered = bool(sender(markdown))
    record_delivery(
        reports_dir, period=period, report_updated_at=updated_at,
        state="delivered" if delivered else "delivery_failed",
        delivered=delivered, expected_delivery=True,
        detail="固定報表與行情資料已驗證後送出" if delivered else "Telegram 傳送失敗",
        checked_at=current, relay_health=relay,
    )
    return delivered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["morning", "noon", "evening"], required=True)
    parser.add_argument("--max-age-minutes", type=int, default=120)
    args = parser.parse_args()
    return 0 if deliver_verified_report(
        SETTINGS.reports_dir,
        period=args.period,
        max_age_minutes=args.max_age_minutes,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
