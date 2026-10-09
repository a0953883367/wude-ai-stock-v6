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


def _receipt(period, updated, checked):
    return dict(period=period, report_updated_at=updated, checked_at=checked,
                channel="telegram_v6", state="delivered", delivered=True)


def test_watchdog_does_not_duplicate_a_delivered_report():
    target = _dt("2026-09-29T20:00:00")
    report = _report("evening", "2026-09-29 19:48:00")

    assert report_is_fresh(report, period="evening", target=target) is True
    assert recovery_decision(
        report, now=_dt("2026-09-29T20:23:00"), period="evening",
        delivery=_receipt("evening", "2026-09-29 19:48:00", "2026-09-29 20:00:00")
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
        delivery=_receipt("evening", "2026-09-29 20:35:00", "2026-09-29 20:36:00"),
    )

    assert should_run is False
    assert "already delivered" in reason


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
        delivery=_receipt("morning", "2026-09-30 05:36:00", "2026-09-30 06:00:00"),
    )[0] is False


def test_morning_recovery_pulses_respect_grace_freshness_and_expiry():
    stale = _report("evening", "2026-09-29 20:00:00")
    assert not recovery_decision(stale, now=_dt("2026-09-30T06:09:00"), period="morning")[0]
    for clock in ["06:10", "06:25", "06:40", "06:55"]:
        now = _dt(f"2026-09-30T{clock}:00")
        assert recovery_decision(stale, now=now, period="morning")[0]
        assert not recovery_decision(
            _report("morning", "2026-09-30 05:36:00"), now=now, period="morning",
            delivery=_receipt("morning", "2026-09-30 05:36:00", "2026-09-30 06:00:00")
        )[0]
    assert not recovery_decision(stale, now=_dt("2026-09-30T08:01:00"), period="morning")[0]


@pytest.mark.parametrize("period,clock,schedule", [
    ("morning", "06:25", "35 21 * * *"),
    ("noon", "12:25", "35 3 * * *"),
    ("evening", "20:25", "10 11 * * *"),
])
def test_generated_but_undelivered_report_still_requires_recovery(period, clock, schedule):
    now = _dt(f"2026-10-03T{clock}:00")
    report = _report(period, now.isoformat())
    for receipt in [None, {}, dict(delivered=False, state="failed")]:
        assert recovery_decision(report, now=now, period=period, delivery=receipt)[0]
        assert scheduled_gate_decision(report, now=now, schedule=schedule, delivery=receipt)[0]


def test_yesterday_delivery_cannot_suppress_today_and_midnight_recovery_expires():
    receipt = _receipt("evening", "2026-10-02 19:45:00", "2026-10-02 20:00:00")
    assert recovery_decision({}, now=_dt("2026-10-03T20:25:00"), period="evening", delivery=receipt)[0]
    assert not recovery_decision({}, now=_dt("2026-10-04T00:25:00"), period="evening", delivery=receipt)[0]


def test_silent_refresh_gate_rechecks_data_and_current_checkpoint(tmp_path):
    import subprocess
    import sys
    report = tmp_path / 'latest.json'
    report.write_text(json.dumps({'updated_at': '2026-10-08 19:06:53', 'data_status': {'us_sip_count': 187, 'us_opra_count': 29}}))
    args = [sys.executable, 'briefing_watchdog.py', '--mode', 'gate', '--data-refresh',
            '--report', str(report), '--period', 'noon', '--now', '2026-10-09T17:24:00+08:00']
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['should_run'] == 'true'
    report.write_text(json.dumps({'updated_at': '2026-10-09 17:20:00', 'data_status': {'us_sip_count': 190, 'us_opra_count': 28}}))
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['should_run'] == 'false'
