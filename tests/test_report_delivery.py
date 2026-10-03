from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from report_delivery import deliver_verified_report, validate_fixed_report


TAIPEI = ZoneInfo("Asia/Taipei")


def _report(period: str = "evening", updated_at: str = "2026-09-27 19:35:00") -> dict:
    return {
        "period": period,
        "run_mode": "scheduled_report",
        "updated_at": updated_at,
        "data_status": {"us_sip_count": 185, "us_opra_count": 29},
    }


def _healthy_response():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "us_sip_configured": True,
                "us_opra_configured": True,
            }

    return Response()


def test_fixed_report_rejects_noon_at_evening_delivery_window():
    valid, reasons = validate_fixed_report(
        _report("noon"),
        expected_period="evening",
        now=datetime(2026, 9, 27, 20, 0, tzinfo=TAIPEI),
    )

    assert valid is False
    assert "時段不是 evening" in reasons


def test_fixed_report_rejects_empty_sip_or_opra():
    report = _report()
    report["data_status"] = {"us_sip_count": 0, "us_opra_count": 0}

    valid, reasons = validate_fixed_report(
        report,
        expected_period="evening",
        now=datetime(2026, 9, 27, 20, 0, tzinfo=TAIPEI),
    )

    assert valid is False
    assert "SIP 資料為 0" in reasons
    assert "OPRA 資料為 0" in reasons


def test_verified_delivery_records_success_without_touching_report(tmp_path):
    report = _report()
    (tmp_path / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "latest.md").write_text("verified evening", encoding="utf-8")
    sent = []

    delivered = deliver_verified_report(
        tmp_path,
        period="evening",
        now=datetime(2026, 9, 27, 20, 0, tzinfo=TAIPEI),
        sender=lambda message: sent.append(message) or True,
        health_get=lambda *args, **kwargs: _healthy_response(),
    )

    status = json.loads(
        (tmp_path / "report_delivery_status.json").read_text(encoding="utf-8")
    )
    assert delivered is True
    assert sent == ["verified evening"]
    assert status["state"] == "delivered"
    assert status["report_updated_at"] == report["updated_at"]
    assert all(value is False for value in status["safety"].values())


def test_stale_report_is_blocked_before_sender(tmp_path):
    report = _report(updated_at="2026-09-26 20:00:00")
    (tmp_path / "latest.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "latest.md").write_text("stale", encoding="utf-8")
    sent = []

    delivered = deliver_verified_report(
        tmp_path,
        period="evening",
        now=datetime(2026, 9, 27, 20, 0, tzinfo=TAIPEI),
        sender=lambda message: sent.append(message) or True,
        health_get=lambda *args, **kwargs: _healthy_response(),
    )

    status = json.loads(
        (tmp_path / "report_delivery_status.json").read_text(encoding="utf-8")
    )
    assert delivered is False
    assert sent == []
    assert status["state"] == "blocked_stale_or_incomplete"


def test_period_receipts_survive_later_reports_and_failed_retry(tmp_path):
    from report_delivery import record_delivery
    from briefing_watchdog import load_daily_delivery, delivery_is_current

    def record(period, hour, delivered):
        return record_delivery(
            tmp_path, period=period, report_updated_at=f"2026-10-03 {hour:02d}:00:00",
            state="delivered" if delivered else "delivery_failed", delivered=delivered,
            expected_delivery=True, detail="test", checked_at=datetime(2026, 10, 3, hour, 1, tzinfo=TAIPEI),
        )

    record("morning", 6, True)
    record("noon", 12, True)
    record("morning", 6, False)
    record("evening", 20, True)
    data = load_daily_delivery(tmp_path / "report_delivery_status.json", datetime(2026, 10, 3, 21, tzinfo=TAIPEI))
    for period, hour in (("morning", 6), ("noon", 12), ("evening", 20)):
        assert delivery_is_current(data, period=period, target=datetime(2026, 10, 3, hour, tzinfo=TAIPEI))
    morning = json.loads((tmp_path / "delivery_receipts/2026-10-03-morning.json").read_text())
    assert morning["last_attempt"]["delivered"] is False
    assert morning["last_success"]["delivered"] is True
    tomorrow = load_daily_delivery(tmp_path / "report_delivery_status.json", datetime(2026, 10, 4, 7, tzinfo=TAIPEI))
    assert not delivery_is_current(tomorrow, period="morning", target=datetime(2026, 10, 4, 6, tzinfo=TAIPEI))


def test_failed_delivery_never_creates_success_evidence(tmp_path):
    from report_delivery import record_delivery
    from briefing_watchdog import load_daily_delivery, delivery_is_current
    now = datetime(2026, 10, 3, 20, 1, tzinfo=TAIPEI)
    record_delivery(tmp_path, period="evening", report_updated_at="2026-10-03 20:00:00",
                    state="delivery_failed", delivered=False, expected_delivery=True,
                    detail="failed", checked_at=now)
    receipt = load_daily_delivery(tmp_path / "report_delivery_status.json", now)
    assert not delivery_is_current(receipt, period="evening", target=now.replace(minute=0))
