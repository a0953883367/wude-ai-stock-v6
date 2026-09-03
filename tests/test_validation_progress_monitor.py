from __future__ import annotations

import json
from pathlib import Path

from validation_progress_monitor import (
    acknowledge_notifications,
    pending_notification_messages,
    update_validation_progress_monitor,
)


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _rows(market: str, session_date: str) -> list[dict]:
    return [
        {
            "market": market,
            "type": "個股",
            "symbol": f"{market}{index}",
            "official_session_date": session_date,
        }
        for index in range(3)
    ]


def _source(tmp_path: Path, tw: int, us: int, *, global_days: int | None = None, samples: int = 100, statuses: dict | None = None, invalid: dict | None = None) -> None:
    global_days = min(tw, us) if global_days is None else global_days
    _write(tmp_path / "validation_60d.json", {
        "trading_days_collected": global_days,
        "target_trading_days": 60,
        "remaining_trading_days": 60 - global_days,
        "eligible_samples": samples,
        "tracks": {
            "million_forward_60d": {
                "TW": {"completed_days": tw},
                "US": {"completed_days": us},
            },
        },
    })
    statuses = statuses or {}
    invalid = invalid or {}
    _write(tmp_path / "million_simulation.json", {
        "markets": {
            market: {
                "completed_days": tw if market == "TW" else us,
                "status": statuses.get(market, "running"),
                "invalid_days": [
                    {"session_date": value, "status": "data_insufficient"}
                    for value in invalid.get(market, [])
                ],
            }
            for market in ("TW", "US")
        },
    })


def test_warns_once_then_escalates_and_recovers(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4)
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    assert state["markets"]["TW"]["status"] == "ok"
    assert pending_notification_messages(state) == []

    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 20:00:00",
    )
    assert state["markets"]["TW"]["status"] == "warning"
    assert len(state["pending_notifications"]) == 1
    assert "連續 1 個" in pending_notification_messages(state)[0]
    warning_id = state["pending_notifications"][0]["id"]
    acknowledge_notifications(tmp_path, [warning_id], delivered_at="2026-08-25 20:00:01")

    # Re-running the same completed session never creates another warning.
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 20:30:00",
    )
    assert len(state["pending_notifications"]) == 0

    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-26"),
        period="evening", updated_at="2026-08-26 20:00:00",
    )
    assert state["markets"]["TW"]["status"] == "critical"
    assert len(state["pending_notifications"]) == 1
    critical_id = state["pending_notifications"][0]["id"]
    acknowledge_notifications(tmp_path, [critical_id], delivered_at="2026-08-26 20:00:01")

    _source(tmp_path, 6, 4, global_days=6, samples=120)
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-27"),
        period="evening", updated_at="2026-08-27 20:00:00",
    )
    assert state["markets"]["TW"]["status"] == "ok"
    assert any("停滯警示解除" in text for text in pending_notification_messages(state))


def test_unseen_warning_is_removed_if_same_session_recovers(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4)
    update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    warning = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 17:00:00",
    )
    assert len(warning["pending_notifications"]) == 1

    _source(tmp_path, 6, 4, global_days=6)
    recovered = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 20:00:00",
    )
    assert recovered["markets"]["TW"]["status"] == "ok"
    assert recovered["pending_notifications"] == []


def test_waiting_and_isolated_sessions_do_not_false_alarm(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4)
    update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="evening", updated_at="2026-08-24 20:00:00",
    )

    _source(tmp_path, 5, 4, statuses={"TW": "waiting_data"})
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 17:00:00",
    )
    assert state["markets"]["TW"]["status"] == "ok"
    assert "等待同日正式價格" in state["markets"]["TW"]["detail"]

    _source(tmp_path, 5, 4, invalid={"TW": ["2026-08-25"]})
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-26"),
        period="evening", updated_at="2026-08-26 20:00:00",
    )
    assert state["markets"]["TW"]["status"] == "ok"
    assert state["markets"]["TW"]["stalled_sessions"] == 0
    assert pending_notification_messages(state) == []


def test_delivered_warning_is_explicitly_cleared_when_day_is_isolated(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4)
    update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    warning = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 20:00:00",
    )
    acknowledge_notifications(
        tmp_path,
        [warning["pending_notifications"][0]["id"]],
        delivered_at="2026-08-25 20:00:01",
    )

    _source(tmp_path, 5, 4, invalid={"TW": ["2026-08-25"]})
    resolved = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-26"),
        period="evening", updated_at="2026-08-26 20:00:00",
    )
    assert resolved["markets"]["TW"]["status"] == "ok"
    assert any("已確認為資料隔離日" in text for text in pending_notification_messages(resolved))


def test_notice_stays_pending_until_successfully_delivered(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4)
    update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="evening", updated_at="2026-08-24 20:00:00",
    )
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 17:00:00",
    )
    event_id = state["pending_notifications"][0]["id"]

    same_session = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-25"),
        period="evening", updated_at="2026-08-25 20:00:00",
    )
    assert [item["id"] for item in same_session["pending_notifications"]] == [event_id]

    acknowledged = acknowledge_notifications(
        tmp_path, [event_id], delivered_at="2026-08-25 20:00:01"
    )
    assert acknowledged["pending_notifications"] == []


def test_saturday_weekly_summary_uses_saved_delta(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4, global_days=4, samples=100)
    first = update_validation_progress_monitor(
        tmp_path, _rows("US", "2026-08-31"),
        period="morning", updated_at="2026-08-31 06:00:00",
    )
    assert pending_notification_messages(first) == []

    _source(tmp_path, 9, 8, global_days=8, samples=299)
    saturday = update_validation_progress_monitor(
        tmp_path, _rows("US", "2026-09-04"),
        period="morning", updated_at="2026-09-05 06:00:00",
    )
    messages = pending_notification_messages(saturday)
    assert any("本週增加 4 個真實交易日、199 筆共識樣本" in text for text in messages)
    assert any("目前 8/60" in text for text in messages)


def test_intraday_and_non_close_periods_do_not_advance_observer(tmp_path: Path) -> None:
    _source(tmp_path, 5, 4)
    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="evening", updated_at="2026-08-24 15:00:00", intraday=True,
    )
    assert state["markets"]["TW"]["last_session_date"] == ""

    state = update_validation_progress_monitor(
        tmp_path, _rows("TW", "2026-08-24"),
        period="noon", updated_at="2026-08-24 12:00:00",
    )
    assert state["markets"]["TW"]["last_session_date"] == ""
