"""Forward-only 1/3/5-session validation for chart-pattern evidence.

The ledger is deliberately downstream of the completed V6 rows.  It records
an occurrence before any future return is known, confirms it on the next
completed market session, and publishes aggregate evidence only.  It never
mutates caller rows, formal rankings, weights, or broker instructions.
"""

from __future__ import annotations

import copy
from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MODEL_VERSION = "CHART-PATTERN-1-3-5D-SHADOW-V1"
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
HORIZONS = (1, 3, 5)
INITIAL_REVIEW_DAYS = 20
ADOPTION_REVIEW_DAYS = 60
MAX_SIGNALS = 12_000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _price(row: dict[str, Any], kind: str = "close") -> float | None:
    keys = {
        "close": ("official_adjusted_close_price", "official_close_price", "price"),
        "high": ("official_adjusted_high_price", "official_high_price"),
        "low": ("official_adjusted_low_price", "official_low_price"),
    }[kind]
    for key in keys:
        value = _finite(row.get(key))
        if value is not None and value > 0:
            return value
    return _price(row, "close") if kind != "close" else None


def _directional_return(signal: dict[str, Any], close: float) -> tuple[float, float]:
    base = float(signal["signal_close"])
    raw = (close / base - 1.0) * 100.0
    directional = raw if signal["direction"] == "bullish" else -raw
    return round(raw, 4), round(directional, 4)


def _empty_report() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": "forward_only_chart_pattern_shadow",
        "sessions": {market: [] for market in MARKETS},
        "last_patterns": {market: {} for market in MARKETS},
        "signals": [],
    }


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_report()
    if not isinstance(payload, dict) or payload.get("model_version") != MODEL_VERSION:
        return _empty_report()
    payload.setdefault("sessions", {market: [] for market in MARKETS})
    payload.setdefault("last_patterns", {market: {} for market in MARKETS})
    payload.setdefault("signals", [])
    for market in MARKETS:
        payload["sessions"].setdefault(market, [])
        payload["last_patterns"].setdefault(market, {})
    return payload


def _session_observation(row: dict[str, Any]) -> dict[str, Any] | None:
    close = _price(row)
    if close is None:
        return None
    return {
        "close": close,
        "high": _price(row, "high") or close,
        "low": _price(row, "low") or close,
        "relative_volume": _finite(row.get("relative_volume")),
        "pattern": str(row.get("chart_pattern_shadow_name") or "未偵測"),
        "pattern_direction": str(row.get("chart_pattern_shadow_direction") or "neutral"),
        "pattern_status": str(row.get("chart_pattern_shadow_status") or ""),
        "pattern_volume_confirmed": row.get("chart_pattern_shadow_volume_confirmed") is True,
    }


def _settle_signal(signal: dict[str, Any], session_index: int,
                   observation: dict[str, Any]) -> None:
    distance = session_index - int(signal["session_index"])
    if distance <= 0:
        return
    raw, directional = _directional_return(signal, observation["close"])
    path = signal.setdefault("path", [])
    if not any(int(item.get("session_distance") or -1) == distance for item in path):
        path.append({
            "session_distance": distance,
            "close": round(observation["close"], 6),
            "high": round(observation["high"], 6),
            "low": round(observation["low"], 6),
            "raw_return_pct": raw,
            "directional_return_pct": directional,
        })
    if distance == 1 and not isinstance(signal.get("next_session_confirmation"), dict):
        same_direction = observation["pattern_direction"] == signal["direction"]
        price_direction = directional > 0
        volume_confirmed = bool(
            observation["pattern_volume_confirmed"]
            or (observation["relative_volume"] is not None
                and observation["relative_volume"] >= 1.0)
        )
        signal["next_session_confirmation"] = {
            "status": "confirmed" if same_direction and price_direction else "not_confirmed",
            "direction_confirmed": same_direction and price_direction,
            "volume_confirmed": volume_confirmed,
            "same_pattern": observation["pattern"] == signal["pattern"],
            "actual_return_pct": raw,
        }
    if distance in HORIZONS:
        outcomes = signal.setdefault("outcomes", {})
        key = str(distance)
        if key not in outcomes:
            adverse = 0.0
            for item in path:
                if int(item["session_distance"]) > distance:
                    continue
                if signal["direction"] == "bullish":
                    value = (float(item["low"]) / float(signal["signal_close"]) - 1) * 100
                else:
                    value = -(float(item["high"]) / float(signal["signal_close"]) - 1) * 100
                adverse = min(adverse, value)
            outcomes[key] = {
                "raw_return_pct": raw,
                "directional_return_pct": directional,
                "hit": directional > 0,
                "max_adverse_excursion_pct": round(adverse, 4),
            }


def _aggregate(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        groups[(signal["market"], signal["pattern"])].append(signal)
    summaries = []
    for (market, pattern), rows in sorted(groups.items()):
        item: dict[str, Any] = {
            "market": market,
            "pattern": pattern,
            "signal_count": len(rows),
            "confirmed_count": sum(
                1 for row in rows
                if (row.get("next_session_confirmation") or {}).get("direction_confirmed")
            ),
            "volume_confirmed_count": sum(
                1 for row in rows
                if (row.get("next_session_confirmation") or {}).get("volume_confirmed")
            ),
            "horizons": {},
        }
        for horizon in HORIZONS:
            outcomes = [row.get("outcomes", {}).get(str(horizon)) for row in rows]
            outcomes = [outcome for outcome in outcomes if isinstance(outcome, dict)]
            directional = [float(outcome["directional_return_pct"]) for outcome in outcomes]
            adverse = [float(outcome["max_adverse_excursion_pct"]) for outcome in outcomes]
            item["horizons"][str(horizon)] = {
                "sample_count": len(outcomes),
                "hit_rate_pct": round(sum(value > 0 for value in directional) / len(directional) * 100, 1) if directional else None,
                "average_directional_return_pct": round(sum(directional) / len(directional), 4) if directional else None,
                "maximum_adverse_excursion_pct": round(min(adverse), 4) if adverse else None,
            }
        summaries.append(item)
    return summaries


def update_chart_pattern_validation(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict[str, Any]:
    """Update immutable occurrences and outcomes from completed sessions only."""
    reports_dir = Path(reports_dir)
    path = reports_dir / "chart_pattern_validation.json"
    report = _load(path)
    frozen_rows = copy.deepcopy(rows)
    if not intraday:
        for market in MARKETS:
            if period != CLOSED_PERIOD[market]:
                continue
            market_rows = [
                row for row in frozen_rows
                if str(row.get("market") or "").upper() == market
                and str(row.get("official_session_date") or "")
            ]
            if not market_rows:
                continue
            session_date = max(str(row["official_session_date"]) for row in market_rows)
            market_rows = [row for row in market_rows if str(row["official_session_date"]) == session_date]
            sessions = report["sessions"][market]
            if session_date not in sessions:
                sessions.append(session_date)
                sessions.sort()
            session_index = sessions.index(session_date)
            by_symbol = {
                str(row.get("symbol") or "").upper(): _session_observation(row)
                for row in market_rows if row.get("symbol")
            }
            for signal in report["signals"]:
                if signal.get("market") != market or int(signal.get("session_index") or 0) >= session_index:
                    continue
                observation = by_symbol.get(str(signal.get("symbol") or "").upper())
                if observation:
                    _settle_signal(signal, session_index, observation)
            previous = report["last_patterns"][market]
            current: dict[str, str] = {}
            for row in market_rows:
                symbol = str(row.get("symbol") or "").upper()
                observation = by_symbol.get(symbol)
                if not observation:
                    continue
                pattern = observation["pattern"]
                current[symbol] = pattern
                direction = observation["pattern_direction"]
                if pattern == "未偵測" or direction not in {"bullish", "bearish"}:
                    continue
                if previous.get(symbol) == pattern:
                    continue
                signal_id = f"{market}:{symbol}:{session_date}:{pattern}"
                if any(item.get("signal_id") == signal_id for item in report["signals"]):
                    continue
                report["signals"].append({
                    "signal_id": signal_id,
                    "market": market,
                    "symbol": symbol,
                    "name": str(row.get("name") or symbol),
                    "asset_type": str(row.get("type") or ""),
                    "signal_date": session_date,
                    "session_index": session_index,
                    "pattern": pattern,
                    "direction": direction,
                    "signal_status": observation["pattern_status"],
                    "signal_confidence_pct": _finite(row.get("chart_pattern_shadow_confidence")),
                    "signal_volume_confirmed": observation["pattern_volume_confirmed"],
                    "signal_close": observation["close"],
                    "entry": _finite(row.get("chart_pattern_shadow_entry")),
                    "stop": _finite(row.get("chart_pattern_shadow_stop")),
                    "target": _finite(row.get("chart_pattern_shadow_target")),
                    "formal_rank_at_signal": row.get("overall_rank"),
                    "formal_score_at_signal": _finite(row.get("overall_ranking_score")),
                    "next_session_confirmation": None,
                    "outcomes": {},
                    "path": [],
                    "affects_formal": False,
                })
            report["last_patterns"][market] = current
        report["signals"] = report["signals"][-MAX_SIGNALS:]

    valid_days = {market: len(report["sessions"][market]) for market in MARKETS}
    collected_days = max(valid_days.values(), default=0)
    signals = report["signals"]
    report.update({
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "updated_at": updated_at,
        "period": period,
        "run_mode": "intraday_read_only" if intraday else "completed_session_update",
        "status": "collecting" if collected_days < ADOPTION_REVIEW_DAYS else "ready_for_manual_review",
        "summary": {
            "signal_count": len(signals),
            "pending_next_session_confirmation": sum(1 for item in signals if not isinstance(item.get("next_session_confirmation"), dict)),
            "confirmed_count": sum(1 for item in signals if (item.get("next_session_confirmation") or {}).get("direction_confirmed")),
            "volume_confirmed_count": sum(1 for item in signals if (item.get("next_session_confirmation") or {}).get("volume_confirmed")),
            "matured_1d": sum("1" in item.get("outcomes", {}) for item in signals),
            "matured_3d": sum("3" in item.get("outcomes", {}) for item in signals),
            "matured_5d": sum("5" in item.get("outcomes", {}) for item in signals),
            "valid_trading_days": valid_days,
            "initial_review_days": INITIAL_REVIEW_DAYS,
            "adoption_review_days": ADOPTION_REVIEW_DAYS,
            "remaining_to_initial_review": max(0, INITIAL_REVIEW_DAYS - collected_days),
            "remaining_to_adoption_review": max(0, ADOPTION_REVIEW_DAYS - collected_days),
        },
        "pattern_statistics": _aggregate(signals),
        "method": {
            "signal_day": "只記錄型態，不立即採用",
            "next_session": "下一個完整收盤確認方向與量能",
            "outcomes": "追蹤第1、3、5個交易日方向命中、平均方向報酬與最大不利幅度",
            "review_gate": "20個交易日初評；60個交易日後才可人工審查是否採用",
        },
        "policy": {
            "forward_only": True,
            "same_session_outcome_forbidden": True,
            "formal_v6_unchanged": True,
            "formal_ranking_unchanged": True,
            "formal_weights_unchanged": True,
            "automatic_orders": False,
            "manual_approval_required_after_60_days": True,
        },
    })
    reports_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return report
