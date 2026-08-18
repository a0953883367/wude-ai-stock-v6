import json
from dataclasses import replace

import notifier


def _report(updated_at: str, run_mode: str) -> dict:
    return {
        "period": "noon",
        "run_mode": run_mode,
        "updated_at": updated_at,
    }


def test_intraday_refresh_keeps_fixed_noon_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(
        notifier,
        "SETTINGS",
        replace(notifier.SETTINGS, reports_dir=tmp_path),
    )

    fixed = _report("2026-08-18 12:00:00", "scheduled_report")
    notifier.save_report(fixed, "fixed noon report")
    noon_path = tmp_path / "noon.json"
    archive_path = tmp_path / "archive" / "2026-08-18-noon.json"
    fixed_noon = noon_path.read_text(encoding="utf-8")
    fixed_archive = archive_path.read_text(encoding="utf-8")

    intraday = _report("2026-08-18 13:00:00", "intraday_refresh")
    notifier.save_report(
        intraday,
        "intraday live report",
        save_snapshot=False,
    )

    assert noon_path.read_text(encoding="utf-8") == fixed_noon
    assert archive_path.read_text(encoding="utf-8") == fixed_archive
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["run_mode"] == "intraday_refresh"
    assert latest["updated_at"] == "2026-08-18 13:00:00"
