from datetime import datetime, time
from zoneinfo import ZoneInfo

from report_schedule import resolve_delivery_target, validate_delivery_window


TAIPEI = ZoneInfo("Asia/Taipei")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TAIPEI)


def test_evening_preparation_uses_same_day_target():
    target = resolve_delivery_target(_dt("2026-09-28T19:30:00"), time(20, 0))

    assert target == _dt("2026-09-28T20:00:00")


def test_cross_midnight_evening_run_resolves_to_previous_day_and_is_stale():
    valid, target, detail = validate_delivery_window(
        _dt("2026-09-29T02:41:00"), time(20, 0)
    )

    assert target == _dt("2026-09-28T20:00:00")
    assert valid is False
    assert detail == "stale by 401 minutes"


def test_morning_preparation_is_valid_before_six():
    valid, target, _ = validate_delivery_window(
        _dt("2026-09-29T05:45:00"), time(6, 0)
    )

    assert target == _dt("2026-09-29T06:00:00")
    assert valid is True


def test_three_hour_delayed_morning_run_is_rejected():
    valid, target, detail = validate_delivery_window(
        _dt("2026-09-29T09:20:00"), time(6, 0)
    )

    assert target == _dt("2026-09-29T06:00:00")
    assert valid is False
    assert detail == "stale by 200 minutes"
