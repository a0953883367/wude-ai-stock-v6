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
    # Keep period-specific evidence: a later noon/evening or silent update must
    # not erase proof of an earlier successful delivery. Failed retries retain
    # the success as well as the actual latest attempt.
    if period in {"morning", "noon", "evening"}:
        receipt_path = reports_dir / "delivery_receipts" / f"{now.date().isoformat()}-{period}.json"
        try:
            previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        success = payload if delivered and state == "delivered" else previous.get("last_success", {})
        generation = previous.get("last_generation", {})
        if state == "suppressed" and not delivered and not expected_delivery:
            try:
                report = json.loads((reports_dir / "latest.json").read_text(encoding="utf-8"))
                markdown = (reports_dir / "latest.md").read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                report, markdown = {}, ""
            valid, _ = validate_fixed_report(report, expected_period=period, now=now)
            if valid and markdown and report.get("updated_at") == report_updated_at:
                generation = {**payload, "generation_validated": True}
        _atomic_json(receipt_path, {"last_attempt": payload, "last_success": success,
                                    "last_generation": generation})
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
    sender: Callable[[str], bool] | None = None,
    health_get: Callable[..., Any] = requests.get,
) -> bool:
    # This stock-only boundary is permanently silent, even with credentials or
    # an injected sender. Keep validation available independently, but never
    # contact Telegram or imply that a ChatGPT task delivered a report.
    current = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    try:
        report = json.loads((reports_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    record_delivery(
        reports_dir, period=period,
        report_updated_at=str(report.get("updated_at") or ""),
        state="suppressed", delivered=False, expected_delivery=False,
        detail="股票報告 Telegram 已停用；ChatGPT 原對話送達狀態由原任務管理",
        checked_at=current,
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["morning", "noon", "evening"], required=True)
    parser.add_argument("--max-age-minutes", type=int, default=120)
    args = parser.parse_args()
    deliver_verified_report(
        SETTINGS.reports_dir,
        period=args.period,
        max_age_minutes=args.max_age_minutes,
    )
    return 0  # Successful suppression is not successful delivery.


if __name__ == "__main__":
    raise SystemExit(main())
