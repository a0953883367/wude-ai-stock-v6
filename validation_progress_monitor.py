"""Observe whether forward-validation evidence advances on completed sessions.

This module is deliberately downstream of every model and ledger.  It reads
their completed-session counters, records health, and prepares user notices;
it cannot change rankings, weights, forecasts, or orders.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
MARKET_LABEL = {"TW": "台股", "US": "美股"}
LEVEL_ORDER = {"ok": 0, "warning": 1, "critical": 2}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _market_session(rows: Iterable[dict[str, Any]], market: str) -> str:
    stock_dates = [
        str(row.get("official_session_date") or "")
        for row in rows
        if str(row.get("market") or "").upper() == market
        and "ETF" not in str(row.get("type") or "").upper()
        and row.get("official_session_date")
    ]
    dates = stock_dates or [
        str(row.get("official_session_date") or "")
        for row in rows
        if str(row.get("market") or "").upper() == market
        and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _empty_market() -> dict[str, Any]:
    return {
        "status": "initializing",
        "last_session_date": "",
        "last_completed_days": 0,
        "last_progress_at": None,
        "stalled_sessions": 0,
        "alert_notified": False,
        "detail": "等待第一個完成交易日建立基準",
    }


def _empty_state(updated_at: str, validation: dict[str, Any]) -> dict[str, Any]:
    days = int(validation.get("trading_days_collected") or 0)
    samples = int(validation.get("eligible_samples") or 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "status": "ok",
        "mode": "completed_session_observer_only",
        "current": {
            "trading_days_collected": days,
            "target_trading_days": int(validation.get("target_trading_days") or 60),
            "remaining_trading_days": int(validation.get("remaining_trading_days") or max(60 - days, 0)),
            "eligible_samples": samples,
        },
        "markets": {market: _empty_market() for market in MARKETS},
        "weekly_baseline": {
            "date": updated_at[:10],
            "trading_days_collected": days,
            "eligible_samples": samples,
        },
        "last_weekly_summary_date": None,
        "pending_notifications": [],
        "policy": {
            "warning_after_missed_sessions": 1,
            "critical_after_missed_sessions": 2,
            "weekends_and_holidays_ignored": True,
            "isolated_invalid_sessions_ignored": True,
            "same_session_runs_deduplicated": True,
            "changes_rankings": False,
            "changes_weights": False,
            "places_orders": False,
        },
    }


def _load_state(path: Path, updated_at: str, validation: dict[str, Any]) -> dict[str, Any]:
    state = _read(path)
    if state.get("schema_version") != SCHEMA_VERSION:
        return _empty_state(updated_at, validation)
    markets = state.get("markets")
    if not isinstance(markets, dict):
        return _empty_state(updated_at, validation)
    for market in MARKETS:
        if not isinstance(markets.get(market), dict):
            markets[market] = _empty_market()
    if not isinstance(state.get("pending_notifications"), list):
        state["pending_notifications"] = []
    return state


def _completed_days(validation: dict[str, Any], million: dict[str, Any], market: str) -> int:
    track = (
        ((validation.get("tracks") or {}).get("million_forward_60d") or {}).get(market)
        or {}
    )
    if track.get("completed_days") is not None:
        return int(track.get("completed_days") or 0)
    return int(((million.get("markets") or {}).get(market) or {}).get("completed_days") or 0)


def _queue(state: dict[str, Any], event: dict[str, Any]) -> None:
    pending = [item for item in state.get("pending_notifications", []) if isinstance(item, dict)]
    if any(item.get("id") == event["id"] for item in pending):
        return
    pending.append(event)
    state["pending_notifications"] = pending[-12:]


def _alert_event(
    market: str,
    session_date: str,
    level: str,
    stalled: int,
    completed: int,
    updated_at: str,
) -> dict[str, Any]:
    icon = "🔴" if level == "critical" else "⚠️"
    return {
        "id": f"stall:{market}:{session_date}:{level}:{stalled}",
        "type": "validation_stall",
        "market": market,
        "level": level,
        "created_at": updated_at,
        "message": (
            f"{icon} 60日驗證進度提醒：{MARKET_LABEL[market]}已完成 {session_date}，"
            f"但驗證仍為 {completed} 天；連續 {stalled} 個應前進交易日沒有增加。"
            "只通報檢查，不改模型、權重或正式排名。"
        ),
    }


def _recovery_event(
    market: str,
    session_date: str,
    previous: int,
    completed: int,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "id": f"recovery:{market}:{session_date}:{completed}",
        "type": "validation_recovery",
        "market": market,
        "level": "ok",
        "created_at": updated_at,
        "message": (
            f"✅ 60日驗證恢復：{MARKET_LABEL[market]}進度已由 {previous} 增至 "
            f"{completed} 天（{session_date}），停滯警示解除。"
        ),
    }


def _resolve_alert(state: dict[str, Any], market: str) -> bool:
    """Discard undelivered stale alerts and report whether one was delivered."""
    state["pending_notifications"] = [
        item
        for item in state.get("pending_notifications", [])
        if not (
            isinstance(item, dict)
            and item.get("market") == market
            and item.get("type") in {"validation_stall", "validation_regression"}
        )
    ]
    return bool(state["markets"][market].get("alert_notified"))


def _isolated_resolution_event(
    market: str,
    session_date: str,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "id": f"isolated-resolution:{market}:{session_date}",
        "type": "validation_recovery",
        "market": market,
        "level": "ok",
        "created_at": updated_at,
        "message": (
            f"✅ 60日驗證提醒解除：{MARKET_LABEL[market]} {session_date} 已確認為資料隔離日，"
            "不列入停滯次數；模型、權重與正式排名均未變更。"
        ),
    }


def _observe_market(
    state: dict[str, Any],
    validation: dict[str, Any],
    million: dict[str, Any],
    rows: list[dict[str, Any]],
    market: str,
    updated_at: str,
) -> None:
    session_date = _market_session(rows, market)
    completed = _completed_days(validation, million, market)
    market_state = state["markets"][market]
    last_session = str(market_state.get("last_session_date") or "")
    previous = int(market_state.get("last_completed_days") or 0)
    old_status = str(market_state.get("status") or "initializing")

    if not session_date:
        market_state["detail"] = "本次找不到一致的完成交易日；等待下一次固定報告"
        return

    if not last_session:
        market_state.update({
            "status": "ok",
            "last_session_date": session_date,
            "last_completed_days": completed,
            "last_progress_at": updated_at,
            "stalled_sessions": 0,
            "alert_notified": False,
            "detail": f"已建立 {session_date}／{completed} 天監控基準",
        })
        return

    if session_date < last_session or completed < previous:
        market_state.update({
            "status": "critical",
            "last_session_date": max(session_date, last_session),
            "last_completed_days": completed,
            "stalled_sessions": max(2, int(market_state.get("stalled_sessions") or 0)),
            "detail": f"進度倒退：交易日 {last_session}→{session_date}，完成天數 {previous}→{completed}",
        })
        _queue(state, {
            "id": f"regression:{market}:{session_date}:{completed}",
            "type": "validation_regression",
            "market": market,
            "level": "critical",
            "created_at": updated_at,
            "message": (
                f"🔴 60日驗證資料倒退：{MARKET_LABEL[market]}完成天數由 {previous} 變成 "
                f"{completed}。已停止只讀監控判定，請檢查資料；正式模型未變更。"
            ),
        })
        return

    if session_date == last_session:
        if completed > previous:
            market_state.update({
                "status": "ok",
                "last_completed_days": completed,
                "last_progress_at": updated_at,
                "stalled_sessions": 0,
                "detail": f"同一交易日補齊資料，進度已到 {completed} 天",
            })
            if old_status in {"warning", "critical"} and _resolve_alert(state, market):
                _queue(state, _recovery_event(market, session_date, previous, completed, updated_at))
            elif old_status in {"warning", "critical"}:
                market_state["alert_notified"] = False
        return

    market_row = ((million.get("markets") or {}).get(market) or {})
    invalid_dates = {
        str(item.get("session_date") or "")
        for item in market_row.get("invalid_days", [])
        if isinstance(item, dict)
    }
    market_state["last_session_date"] = session_date
    if completed > previous:
        market_state.update({
            "status": "ok",
            "last_completed_days": completed,
            "last_progress_at": updated_at,
            "stalled_sessions": 0,
            "detail": f"{session_date} 正常增加至 {completed} 天",
        })
        if old_status in {"warning", "critical"} and _resolve_alert(state, market):
            _queue(state, _recovery_event(market, session_date, previous, completed, updated_at))
        elif old_status in {"warning", "critical"}:
            market_state["alert_notified"] = False
        return

    if last_session in invalid_dates or session_date in invalid_dates:
        resolved_notified_alert = (
            old_status in {"warning", "critical"} and _resolve_alert(state, market)
        )
        market_state.update({
            "status": "ok",
            "last_completed_days": completed,
            "stalled_sessions": 0,
            "detail": f"{last_session} 已明確隔離，不列入停滯次數",
        })
        if resolved_notified_alert:
            _queue(state, _isolated_resolution_event(market, last_session, updated_at))
        elif old_status in {"warning", "critical"}:
            market_state["alert_notified"] = False
        return

    if str(market_row.get("status") or "") == "waiting_data":
        market_state.update({
            "status": old_status if old_status in {"warning", "critical"} else "ok",
            "last_completed_days": completed,
            "stalled_sessions": (
                int(market_state.get("stalled_sessions") or 0)
                if old_status in {"warning", "critical"}
                else 0
            ),
            "detail": (
                f"既有停滯仍待釐清；{session_date} 正等待同日正式價格，暫不新增次數"
                if old_status in {"warning", "critical"}
                else f"{session_date} 正等待同日正式價格，暫不判為停滯"
            ),
        })
        return

    stalled = int(market_state.get("stalled_sessions") or 0) + 1
    level = "critical" if stalled >= 2 else "warning"
    market_state.update({
        "status": level,
        "last_completed_days": completed,
        "stalled_sessions": stalled,
        "detail": f"{session_date} 應增加但仍為 {completed} 天；連續 {stalled} 次",
    })
    if old_status != level:
        _queue(state, _alert_event(market, session_date, level, stalled, completed, updated_at))


def _weekly_summary(state: dict[str, Any], period: str, updated_at: str) -> None:
    try:
        current_date = datetime.fromisoformat(updated_at).date()
    except ValueError:
        return
    if period != "morning" or current_date.weekday() != 5:
        return
    if state.get("last_weekly_summary_date") == current_date.isoformat():
        return
    baseline = state.get("weekly_baseline") if isinstance(state.get("weekly_baseline"), dict) else {}
    if not baseline.get("date"):
        state["weekly_baseline"] = {
            "date": current_date.isoformat(),
            "trading_days_collected": state["current"]["trading_days_collected"],
            "eligible_samples": state["current"]["eligible_samples"],
        }
        return
    if str(baseline.get("date")) == current_date.isoformat():
        return
    days_delta = max(
        0,
        int(state["current"]["trading_days_collected"])
        - int(baseline.get("trading_days_collected") or 0),
    )
    samples_delta = max(
        0,
        int(state["current"]["eligible_samples"])
        - int(baseline.get("eligible_samples") or 0),
    )
    current_days = int(state["current"]["trading_days_collected"])
    target = int(state["current"]["target_trading_days"])
    _queue(state, {
        "id": f"weekly:{current_date.isoformat()}",
        "type": "validation_weekly_summary",
        "level": "info",
        "created_at": updated_at,
        "message": (
            f"📅 60日驗證週報：本週增加 {days_delta} 個真實交易日、"
            f"{samples_delta} 筆共識樣本；目前 {current_days}/{target}，"
            f"還差 {max(target - current_days, 0)} 個交易日。"
        ),
    })
    state["last_weekly_summary_date"] = current_date.isoformat()
    state["weekly_baseline"] = {
        "date": current_date.isoformat(),
        "trading_days_collected": current_days,
        "eligible_samples": int(state["current"]["eligible_samples"]),
    }


def update_validation_progress_monitor(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    path = reports_dir / "validation_progress_monitor.json"
    validation = _read(reports_dir / "validation_60d.json")
    million = _read(reports_dir / "million_simulation.json")
    state = _load_state(path, updated_at, validation)
    state["updated_at"] = updated_at
    days = int(validation.get("trading_days_collected") or 0)
    state["current"] = {
        "trading_days_collected": days,
        "target_trading_days": int(validation.get("target_trading_days") or 60),
        "remaining_trading_days": int(validation.get("remaining_trading_days") or max(60 - days, 0)),
        "eligible_samples": int(validation.get("eligible_samples") or 0),
    }
    if not intraday:
        for market in MARKETS:
            if period == CLOSED_PERIOD[market]:
                _observe_market(state, validation, million, rows, market, updated_at)
        _weekly_summary(state, period, updated_at)
    levels = [
        str(state["markets"][market].get("status") or "ok")
        for market in MARKETS
    ]
    highest = max(levels, key=lambda value: LEVEL_ORDER.get(value, 0), default="ok")
    state["status"] = highest if highest in LEVEL_ORDER else "ok"
    _write(path, state)
    return state


def pending_notification_messages(state: dict[str, Any]) -> list[str]:
    return [
        str(item.get("message") or "")
        for item in state.get("pending_notifications", [])
        if isinstance(item, dict) and item.get("message")
    ]


def acknowledge_notifications(
    reports_dir: Path,
    event_ids: Iterable[str],
    *,
    delivered_at: str,
) -> dict[str, Any]:
    path = Path(reports_dir) / "validation_progress_monitor.json"
    state = _read(path)
    acknowledged = {str(value) for value in event_ids}
    acknowledged_events = [
        item
        for item in state.get("pending_notifications", [])
        if isinstance(item, dict) and str(item.get("id") or "") in acknowledged
    ]
    for event in acknowledged_events:
        market = str(event.get("market") or "")
        market_state = (state.get("markets") or {}).get(market)
        if not isinstance(market_state, dict):
            continue
        if event.get("type") in {"validation_stall", "validation_regression"}:
            market_state["alert_notified"] = True
        elif event.get("type") == "validation_recovery":
            market_state["alert_notified"] = False
    state["pending_notifications"] = [
        item
        for item in state.get("pending_notifications", [])
        if not isinstance(item, dict) or str(item.get("id") or "") not in acknowledged
    ]
    if acknowledged:
        state["last_notification_delivered_at"] = delivered_at
        state["last_notification_ids"] = sorted(acknowledged)
    _write(path, state)
    return state
