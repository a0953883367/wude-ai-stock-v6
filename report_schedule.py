"""Time-window guard for fixed stock briefings.

This module only decides whether a scheduled report is still timely and, when
requested, waits until its official delivery time.  It does not read or change
rankings, weights, shadow ledgers, or trading configuration.
"""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
from time import sleep
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


def resolve_delivery_target(
    now: datetime,
    target_time: time,
    *,
    max_early_minutes: int = 60,
) -> datetime:
    """Resolve the intended target without rolling a delayed run to tonight.

    A preparation run may start shortly before its target.  If the same-day
    target is farther in the future than that preparation window, the event
    belongs to the previous day's target instead.  This is the important
    cross-midnight safeguard.
    """

    current = now.astimezone(TAIPEI)
    target = datetime.combine(current.date(), target_time, tzinfo=TAIPEI)
    if target - current > timedelta(minutes=max_early_minutes):
        target -= timedelta(days=1)
    return target


def validate_delivery_window(
    now: datetime,
    target_time: time,
    *,
    max_early_minutes: int = 60,
    max_late_minutes: int = 120,
) -> tuple[bool, datetime, str]:
    current = now.astimezone(TAIPEI)
    target = resolve_delivery_target(
        current, target_time, max_early_minutes=max_early_minutes
    )
    offset_minutes = (current - target).total_seconds() / 60
    if offset_minutes < -max_early_minutes:
        return False, target, f"too early by {-offset_minutes:.0f} minutes"
    if offset_minutes > max_late_minutes:
        return False, target, f"stale by {offset_minutes:.0f} minutes"
    return True, target, f"within window ({offset_minutes:+.0f} minutes)"


def _parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="Taipei HH:MM")
    parser.add_argument("--mode", choices=["check", "wait"], default="check")
    parser.add_argument("--max-early-minutes", type=int, default=60)
    parser.add_argument("--max-late-minutes", type=int, default=120)
    args = parser.parse_args()

    now = datetime.now(TAIPEI)
    valid, target, detail = validate_delivery_window(
        now,
        _parse_clock(args.target),
        max_early_minutes=args.max_early_minutes,
        max_late_minutes=args.max_late_minutes,
    )
    print(
        f"Taipei now={now.isoformat(timespec='seconds')} "
        f"target={target.isoformat(timespec='seconds')} {detail}",
        flush=True,
    )
    if not valid:
        return 1
    if args.mode == "wait":
        seconds = max(0.0, (target - now).total_seconds())
        if seconds:
            print(f"Waiting {seconds:.0f}s for fixed delivery window", flush=True)
            sleep(seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
