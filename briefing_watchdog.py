"""Recovery gate for fixed-time stock briefings.

The watchdog only checks report freshness and decides whether a missed fixed
report should be dispatched.  It never changes rankings, weights, shadow
ledgers, or trading state.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
TARGETS = {
    "morning": time(6, 0),
    "noon": time(12, 0),
    "evening": time(20, 0),
}
SCHEDULE_PERIODS = {
    "35 21 * * *": "morning",
    "45 21 * * *": "morning",
    "55 21 * * *": "morning",
    "45 3 * * *": "noon",
    "30 11 * * *": "evening",
}


def _parse_updated_at(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


def load_report(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def target_datetime(now: datetime, period: str) -> datetime:
    current = now.astimezone(TAIPEI)
    return datetime.combine(current.date(), TARGETS[period], tzinfo=TAIPEI)


def report_is_fresh(
    report: dict[str, Any],
    *,
    period: str,
    target: datetime,
    preparation_minutes: int = 60,
) -> bool:
    updated_at = _parse_updated_at(report.get("updated_at"))
    return bool(
        report.get("period") == period
        and updated_at is not None
        and updated_at >= target - timedelta(minutes=preparation_minutes)
    )


def recovery_decision(
    report: dict[str, Any],
    *,
    now: datetime,
    period: str,
    grace_minutes: int = 10,
    max_late_minutes: int = 120,
) -> tuple[bool, str]:
    current = now.astimezone(TAIPEI)
    target = target_datetime(current, period)
    if report_is_fresh(report, period=period, target=target):
        return False, "current report already exists"
    if current < target + timedelta(minutes=grace_minutes):
        return False, "still inside primary delivery grace period"
    if current > target + timedelta(minutes=max_late_minutes):
        return False, "recovery window expired"
    return True, "fixed report missing inside recovery window"


def scheduled_gate_decision(
    report: dict[str, Any],
    *,
    now: datetime,
    schedule: str,
) -> tuple[bool, str]:
    period = SCHEDULE_PERIODS.get(schedule)
    if not period:
        return True, "non-fixed settlement schedule"
    target = target_datetime(now, period)
    if report_is_fresh(report, period=period, target=target):
        return False, "another run already produced the fixed report"
    return True, "fixed report still required"


def _write_outputs(path: str | None, values: dict[str, str]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["watchdog", "gate"], required=True)
    parser.add_argument("--report", default="reports/latest.json")
    parser.add_argument("--period", choices=sorted(TARGETS))
    parser.add_argument("--schedule", default="")
    parser.add_argument("--recovery", action="store_true")
    parser.add_argument("--now", help="Test override in ISO-8601 format")
    parser.add_argument("--github-output")
    args = parser.parse_args()

    now = (
        datetime.fromisoformat(args.now).astimezone(TAIPEI)
        if args.now
        else datetime.now(TAIPEI)
    )
    report = load_report(args.report)

    if args.mode == "watchdog":
        selected = next(
            (
                period
                for period in ("morning", "noon", "evening")
                if recovery_decision(report, now=now, period=period)[0]
            ),
            "",
        )
        should_dispatch = bool(selected)
        reason = (
            recovery_decision(report, now=now, period=selected)[1]
            if selected
            else "no fixed report requires recovery"
        )
        values = {
            "should_dispatch": str(should_dispatch).lower(),
            "period": selected,
            "reason": reason,
        }
    elif args.recovery:
        if not args.period:
            parser.error("--period is required for a recovery gate")
        should_run, reason = recovery_decision(
            report, now=now, period=args.period, grace_minutes=0
        )
        values = {"should_run": str(should_run).lower(), "reason": reason}
    else:
        should_run, reason = scheduled_gate_decision(
            report, now=now, schedule=args.schedule
        )
        values = {"should_run": str(should_run).lower(), "reason": reason}

    _write_outputs(args.github_output, values)
    print(json.dumps({"taipei_now": now.isoformat(), **values}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
