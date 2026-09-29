import json
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from briefing_watchdog import recovery_decision, report_is_fresh, scheduled_gate_decision


TAIPEI = ZoneInfo("Asia/Taipei")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TAIPEI)


def _report(period: str, updated_at: str) -> dict[str, object]:
    return {"period": period, "updated_at": updated_at}


def test_evening_watchdog_recovers_missing_report_after_grace_period():
    needed, reason = recovery_decision(
        _report("evening", "2026-09-29 02:41:22"),
        now=_dt("2026-09-29T20:23:00"),
        period="evening",
    )

    assert needed is True
    assert "missing" in reason


def test_watchdog_does_not_duplicate_a_fresh_prepared_report():
    target = _dt("2026-09-29T20:00:00")
    report = _report("evening", "2026-09-29 19:48:00")

    assert report_is_fresh(report, period="evening", target=target) is True
    assert recovery_decision(
        report, now=_dt("2026-09-29T20:23:00"), period="evening"
    )[0] is False


def test_watchdog_never_recovers_after_fixed_window_expires():
    needed, reason = recovery_decision(
        _report("evening", "2026-09-29 02:41:22"),
        now=_dt("2026-09-29T22:01:00"),
        period="evening",
    )

    assert needed is False
    assert reason == "recovery window expired"


def test_delayed_primary_skips_when_recovery_already_finished():
    should_run, reason = scheduled_gate_decision(
        _report("evening", "2026-09-29 20:35:00"),
        now=_dt("2026-09-29T20:45:00"),
        schedule="30 11 * * *",
    )

    assert should_run is False
    assert "already produced" in reason


def test_silent_close_settlement_is_not_suppressed_by_fixed_report_gate():
    should_run, _ = scheduled_gate_decision(
        _report("noon", "2026-09-29 12:05:00"),
        now=_dt("2026-09-29T17:15:00"),
        schedule="15 9 * * 1-5",
    )

    assert should_run is True


@pytest.mark.parametrize("schedule", ["35 21 * * *", "45 21 * * *", "55 21 * * *"])
def test_all_morning_triggers_allow_missing_report_and_suppress_completed_duplicate(schedule):
    now = _dt("2026-09-30T05:55:00")
    assert scheduled_gate_decision(
        _report("evening", "2026-09-29 20:00:00"), now=now, schedule=schedule
    )[0] is True
    assert scheduled_gate_decision(
        _report("morning", "2026-09-30 05:36:00"),
        now=_dt("2026-09-30T06:01:00"), schedule=schedule,
    )[0] is False


def test_morning_recovery_pulses_respect_grace_freshness_and_expiry():
    stale = _report("evening", "2026-09-29 20:00:00")
    assert not recovery_decision(stale, now=_dt("2026-09-30T06:09:00"), period="morning")[0]
    for clock in ["06:10", "06:25", "06:40", "06:55"]:
        now = _dt(f"2026-09-30T{clock}:00")
        assert recovery_decision(stale, now=now, period="morning")[0]
        assert not recovery_decision(
            _report("morning", "2026-09-30 05:36:00"), now=now, period="morning"
        )[0]
    assert not recovery_decision(stale, now=_dt("2026-09-30T08:01:00"), period="morning")[0]
