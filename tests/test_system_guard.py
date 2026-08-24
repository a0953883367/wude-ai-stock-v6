from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from system_guard import build_guard


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _healthy_reports(tmp_path: Path) -> None:
    timestamp = "2026-08-24 16:00:00"
    status = {
        "expected_tw_count": 67,
        "tw_official_price_count": 175,
        "institutional_count": 176,
        "credit_count": 175,
        "broker_count": 10,
        "financial_quality_count": 67,
    }
    _write(tmp_path / "latest.json", {"updated_at": timestamp, "universe_count": 337, "analyzed_count": 330, "data_status": status})
    _write(tmp_path / "rankings.json", {"updated_at": timestamp, "data": [{"symbol": "2330.TW"}]})
    _write(tmp_path / "all_analysis.json", {"updated_at": timestamp, "candidate_count": 337, "analyzed_count": 330, "data": [{"symbol": "2330.TW"}]})


def test_healthy_guard_is_green(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    assert guard["status"] == "ok"
    assert guard["safety"]["places_orders"] is False


def test_stale_and_inconsistent_reports_are_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    rankings = json.loads((tmp_path / "rankings.json").read_text(encoding="utf-8"))
    rankings["updated_at"] = "2026-08-23 12:00:00"
    _write(tmp_path / "rankings.json", rankings)
    now = datetime(2026, 8, 25, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="failure", owner_publish="success")
    assert guard["status"] == "critical"
    codes = {item["code"]: item["level"] for item in guard["checks"]}
    assert codes["report_freshness"] == "critical"
    assert codes["report_consistency"] == "critical"
    assert codes["publish_friend"] == "critical"


def test_missing_broker_is_warning_not_critical(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    latest["data_status"]["broker_count"] = 0
    _write(tmp_path / "latest.json", latest)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    assert guard["status"] == "warning"
    broker = next(item for item in guard["checks"] if item["code"] == "broker_data")
    assert broker["level"] == "warning"
