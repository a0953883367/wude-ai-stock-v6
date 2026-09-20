"""Forward-only validation ledger for the shadow trade-plan engine.

The validator records plan snapshots before outcomes are known, then settles them
only with later completed-session closes. It never changes formal V6 scores,
rankings, weights, or broker state.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MODEL_VERSION = "TRADE-PLAN-VALIDATION-V1"
CHECKPOINTS = (1, 3, 5)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def _signal_id(symbol: str, horizon: str, source_session: str) -> str:
    return f"{symbol}|{horizon}|{source_session}"


def _market_session_key(market: str, session_date: str) -> str:
    return f"{market}:{session_date}"


def _eligible_plan(plan: dict[str, Any]) -> bool:
    quality = plan.get("plan_quality") if isinstance(plan.get("plan_quality"), dict) else {}
    return bool(
        plan.get("active_entry_plan")
        and quality.get("entry_eligible") is True
        and plan.get("entry_low") is not None
        and plan.get("entry_high") is not None
        and plan.get("stop") is not None
        and plan.get("target1") is not None
        and int(plan.get("buy_window_sessions") or 0) > 0
    )


def _new_signal(row: dict[str, Any], horizon: str, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": _signal_id(str(row.get("symbol") or ""), horizon, str(row.get("session_date") or "")),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "market": row.get("market"),
        "horizon": horizon,
        "source_session_date": row.get("session_date"),
        "source_price": row.get("price"),
        "entry_low": plan.get("entry_low"),
        "entry_high": plan.get("entry_high"),
        "stop": plan.get("stop"),
        "target1": plan.get("target1"),
        "target2": plan.get("target2"),
        "buy_window_sessions": int(plan.get("buy_window_sessions") or 0),
        "max_hold_sessions": int(plan.get("max_hold_sessions") or 0),
        "target1_pct": int(plan.get("target1_pct") or 0),
        "target2_pct": int(plan.get("target2_pct") or 0),
        "runner_pct": int(plan.get("runner_pct") or 0),
        "score": plan.get("score"),
        "confidence": plan.get("confidence"),
        "data_quality_pct": plan.get("data_quality_pct"),
        "status": "waiting_entry",
        "entry_session_date": None,
        "entry_price": None,
        "entry_wait_sessions": 0,
        "hold_sessions": 0,
        "target1_hit": False,
        "target1_hit_session": None,
        "target2_hit": False,
        "target2_hit_session": None,
        "stop_hit": False,
        "stop_hit_session": None,
        "latest_session_date": row.get("session_date"),
        "latest_price": row.get("price"),
        "checkpoint_returns_pct": {},
        "final_return_pct": None,
        "final_reason": None,
        "matured": False,
    }


def _same_market_rows(plan_report: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in plan_report.get("plans") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        market = str(row.get("market") or "")
        if symbol and market:
            result[(market, symbol)] = row
    return result


def _session_dates(signals: list[dict[str, Any]], market: str) -> list[str]:
    return sorted({
        str(signal.get("latest_session_date") or signal.get("source_session_date") or "")
        for signal in signals
        if signal.get("market") == market
        and (signal.get("latest_session_date") or signal.get("source_session_date"))
    })


def _percent_return(entry: float, current: float) -> float:
    return round((current / entry - 1.0) * 100.0, 4)


def _advance_signal(signal: dict[str, Any], row: dict[str, Any]) -> None:
    session_date = str(row.get("session_date") or "")
    current = _number(row.get("price"))
    if not session_date or current is None or current <= 0:
        return
    if session_date <= str(signal.get("latest_session_date") or ""):
        return

    signal["latest_session_date"] = session_date
    signal["latest_price"] = current

    status = str(signal.get("status") or "")
    entry_low = _number(signal.get("entry_low"))
    entry_high = _number(signal.get("entry_high"))
    stop = _number(signal.get("stop"))
    target1 = _number(signal.get("target1"))
    target2 = _number(signal.get("target2"))

    if status == "waiting_entry":
        signal["entry_wait_sessions"] = int(signal.get("entry_wait_sessions") or 0) + 1
        if entry_low is not None and entry_high is not None and entry_low <= current <= entry_high:
            signal["status"] = "active"
            signal["entry_session_date"] = session_date
            signal["entry_price"] = current
            signal["hold_sessions"] = 0
            return
        if int(signal.get("entry_wait_sessions") or 0) >= int(signal.get("buy_window_sessions") or 0):
            signal["status"] = "expired_untriggered"
            signal["final_reason"] = "買進期限內收盤價未進入買進區"
            signal["matured"] = True
        return

    if status != "active":
        return

    entry = _number(signal.get("entry_price"))
    if entry is None or entry <= 0:
        signal["status"] = "invalid"
        signal["final_reason"] = "缺少有效進場價"
        signal["matured"] = True
        return

    signal["hold_sessions"] = int(signal.get("hold_sessions") or 0) + 1
    hold = int(signal["hold_sessions"])
    checkpoints = signal.setdefault("checkpoint_returns_pct", {})
    if hold in CHECKPOINTS and str(hold) not in checkpoints:
        checkpoints[str(hold)] = _percent_return(entry, current)

    if stop is not None and current <= stop:
        signal["stop_hit"] = True
        signal["stop_hit_session"] = session_date
        signal["status"] = "closed"
        signal["final_return_pct"] = _percent_return(entry, current)
        signal["final_reason"] = "收盤跌破停損"
        signal["matured"] = True
        return

    if target1 is not None and current >= target1 and not signal.get("target1_hit"):
        signal["target1_hit"] = True
        signal["target1_hit_session"] = session_date
    if target2 is not None and current >= target2 and not signal.get("target2_hit"):
        signal["target2_hit"] = True
        signal["target2_hit_session"] = session_date

    max_hold = int(signal.get("max_hold_sessions") or 0)
    if max_hold > 0 and hold >= max_hold:
        signal["status"] = "closed"
        signal["final_return_pct"] = _percent_return(entry, current)
        signal["final_reason"] = "達最長持有期"
        signal["matured"] = True


def _stats(signals: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[str, dict[str, Any]] = {}
    for horizon in ("short", "medium", "long"):
        rows = [s for s in signals if s.get("horizon") == horizon]
        triggered = [s for s in rows if s.get("entry_session_date")]
        matured = [s for s in rows if s.get("matured") and s.get("entry_session_date")]
        expired = [s for s in rows if s.get("status") == "expired_untriggered"]
        target1 = [s for s in triggered if s.get("target1_hit")]
        stop = [s for s in triggered if s.get("stop_hit")]
        final_returns = [_number(s.get("final_return_pct")) for s in matured]
        final_returns = [v for v in final_returns if v is not None]
        by_horizon[horizon] = {
            "signals": len(rows),
            "triggered": len(triggered),
            "expired_untriggered": len(expired),
            "matured_triggered": len(matured),
            "target1_hit_rate_pct": round(len(target1) / len(triggered) * 100, 2) if triggered else None,
            "stop_hit_rate_pct": round(len(stop) / len(triggered) * 100, 2) if triggered else None,
            "average_final_return_pct": round(sum(final_returns) / len(final_returns), 4) if final_returns else None,
        }
    return by_horizon


def update_trade_plan_validation(reports_dir: Path) -> dict[str, Any]:
    plan_report = _read_json(reports_dir / "trade_plan_shadow.json")
    path = reports_dir / "trade_plan_validation.json"
    previous = _read_json(path)
    signals = previous.get("signals") if isinstance(previous.get("signals"), list) else []
    signals = [dict(s) for s in signals if isinstance(s, dict)]
    existing = {str(s.get("signal_id") or "") for s in signals}

    current_rows = _same_market_rows(plan_report)
    current_session_by_market: dict[str, str] = {}
    for row in current_rows.values():
        market = str(row.get("market") or "")
        session_date = str(row.get("session_date") or "")
        if market and session_date and session_date > current_session_by_market.get(market, ""):
            current_session_by_market[market] = session_date

    for row in current_rows.values():
        source_session = str(row.get("session_date") or "")
        if not source_session:
            continue
        for horizon, plan in (row.get("plans") or {}).items():
            if horizon not in {"short", "medium", "long"} or not isinstance(plan, dict):
                continue
            if not _eligible_plan(plan):
                continue
            signal_id = _signal_id(str(row.get("symbol") or ""), horizon, source_session)
            if signal_id not in existing:
                signals.append(_new_signal(row, horizon, plan))
                existing.add(signal_id)

    for signal in signals:
        key = (str(signal.get("market") or ""), str(signal.get("symbol") or ""))
        row = current_rows.get(key)
        if row:
            _advance_signal(signal, row)

    output = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": "forward_validation_only",
        "updated_at": plan_report.get("updated_at"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "collecting",
        "policy": {
            "future_data_forbidden": True,
            "entry_trigger_uses_later_official_close_only": True,
            "intraday_touch_not_assumed": True,
            "same_session_never_used_as_outcome": True,
            "formal_v6_unchanged": True,
            "formal_rankings_unchanged": True,
            "formal_weights_unchanged": True,
            "automatic_orders": False,
        },
        "summary": {
            "signal_count": len(signals),
            "waiting_entry": sum(s.get("status") == "waiting_entry" for s in signals),
            "active": sum(s.get("status") == "active" for s in signals),
            "matured": sum(bool(s.get("matured")) for s in signals),
            "by_horizon": _stats(signals),
        },
        "signals": signals,
    }
    _write_json(path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="更新影子交易計畫前向驗證")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    report = update_trade_plan_validation(args.reports_dir)
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
