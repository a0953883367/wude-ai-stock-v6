"""Auditable forward testing for completed exchange sessions only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from model_lab import MODEL_NAMES, consensus_prediction, evaluate_direction, model_predictions


HORIZONS = (1, 5, 10, 20)
METHODOLOGY_VERSION = 2
MINIMUM_TRADING_DAYS = 60
MINIMUM_CONSENSUS_SAMPLES = 200


def _outcome_return(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("return_pct")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric(records: list[tuple[float, str]], eligible: int = 0) -> dict[str, float | int]:
    directional = [value if direction == "UP" else -value for value, direction in records]
    if not directional:
        return {
            "samples": 0,
            "eligible_samples": eligible,
            "coverage_pct": 0.0,
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "worst_return_pct": 0.0,
        }
    return {
        "samples": len(directional),
        "eligible_samples": eligible,
        "coverage_pct": round(len(directional) / max(eligible, 1) * 100, 1),
        "win_rate_pct": round(sum(value > 0 for value in directional) / len(directional) * 100, 1),
        "avg_return_pct": round(sum(directional) / len(directional), 2),
        "worst_return_pct": round(min(directional), 2),
    }


def _metric_bundle(
    snapshots: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    direction_for: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in HORIZONS:
        key = str(horizon)
        records: list[tuple[float, str]] = []
        eligible = 0
        for snapshot in snapshots:
            for row in snapshot.get("predictions", []):
                if not predicate(row):
                    continue
                value = _outcome_return(row.get("outcomes", {}).get(key))
                if value is None:
                    continue
                eligible += 1
                direction = direction_for(row)
                if evaluate_direction(direction, value) is not None:
                    records.append((value, direction))
        result[key] = _metric(records, eligible)
    return result


def _consensus_direction(row: dict[str, Any]) -> str:
    return str(row.get("consensus", {}).get("direction") or "ABSTAIN")


def _trade_direction(row: dict[str, Any]) -> str:
    return "UP" if row.get("trade_triggered") else "ABSTAIN"


def _model_direction(name: str) -> Callable[[dict[str, Any]], str]:
    return lambda row: str(row.get("model_predictions", {}).get(name, {}).get("direction") or "ABSTAIN")


def _summary(snapshots: list[dict[str, Any]], legacy_reset: bool = False) -> dict[str, Any]:
    horizons = _metric_bundle(snapshots, lambda _row: True, _consensus_direction)
    trade_horizons = _metric_bundle(snapshots, lambda _row: True, _trade_direction)
    models = {
        name: {"horizons": _metric_bundle(snapshots, lambda _row: True, _model_direction(name))}
        for name in MODEL_NAMES
    }
    leaderboard = sorted(
        ({"name": name, **metrics["horizons"]["1"]} for name, metrics in models.items()),
        key=lambda item: (int(item["samples"]) >= 30, float(item["win_rate_pct"]), int(item["samples"])),
        reverse=True,
    )

    groups: dict[str, Any] = {}
    for group in ("TW", "US", "ETF"):
        predicate = lambda row, expected=group: str(row.get("group")) == expected
        groups[group] = {
            "horizons": _metric_bundle(snapshots, predicate, _consensus_direction),
            "trade_signals": _metric_bundle(snapshots, predicate, _trade_direction),
            "models": {
                name: {"horizons": _metric_bundle(snapshots, predicate, _model_direction(name))}
                for name in MODEL_NAMES
            },
        }

    session_dates = {str(item.get("session_date")) for item in snapshots if item.get("predictions")}
    collected = len(session_dates)
    one_day_samples = int(horizons["1"]["samples"])
    active = collected >= MINIMUM_TRADING_DAYS and one_day_samples >= MINIMUM_CONSENSUS_SAMPLES
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_count": len(snapshots),
        "horizons": horizons,
        "trade_signals": trade_horizons,
        "models": models,
        "model_leaderboard": leaderboard,
        "groups": groups,
        "calibration": {
            "trading_days_collected": collected,
            "minimum_trading_days": MINIMUM_TRADING_DAYS,
            "minimum_consensus_samples": MINIMUM_CONSENSUS_SAMPLES,
            "remaining_trading_days": max(0, MINIMUM_TRADING_DAYS - collected),
            "status": "active" if active else "collecting_clean_outcomes",
            "affects_ai_score": active,
            "eligible_one_day_samples": one_day_samples,
            "legacy_history_reset": legacy_reset,
        },
        "note": (
            "V2只以各市場已完成的正式交易日收盤價驗證；同市場同交易日只保存一次，"
            "十個候選模型以相同資料向前測試，未達門檻不影響AI分數。"
        ),
    }


def _canonical_market(period: str) -> str | None:
    return {"morning": "US", "evening": "TW"}.get(period)


def _performance_price(row: dict[str, Any]) -> float:
    for key in ("official_adjusted_close_price", "official_close_price"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _session_date(row: dict[str, Any]) -> str:
    value = str(row.get("official_session_date") or "")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def _trade_triggered(row: dict[str, Any], official_price: float) -> bool:
    try:
        low = float(row.get("short_term_entry_low") or 0)
        high = float(row.get("short_term_entry_high") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        row.get("short_term_eligible")
        and not row.get("trade_guard_blocked")
        and low > 0
        and high >= low
        and low <= official_price <= high
    )


def _new_snapshot(
    market: str,
    session_date: str,
    predictions: list[dict[str, Any]],
    updated_at: str,
    period: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in predictions:
        if str(row.get("market")) != market or _session_date(row) != session_date:
            continue
        symbol = str(row.get("symbol") or "")
        price = _performance_price(row)
        if not symbol or symbol in seen or price <= 0:
            continue
        seen.add(symbol)
        votes = model_predictions(row)
        rows.append({
            "symbol": symbol,
            "name": row.get("name"),
            "market": market,
            "group": row.get("backtest_group"),
            "rank": row.get("backtest_rank"),
            "official_session_date": session_date,
            "official_price": round(price, 4),
            "model_predictions": votes,
            "consensus": consensus_prediction(votes),
            "trade_triggered": _trade_triggered(row, price),
            "outcomes": {},
        })
    return {
        "id": f"{market}:{session_date}",
        "market": market,
        "session_date": session_date,
        "captured_at": updated_at,
        "period": period,
        "predictions": rows,
    }


def _evaluate_with_new_session(
    snapshots: list[dict[str, Any]],
    market: str,
    session_date: str,
    current_rows: list[dict[str, Any]],
) -> None:
    prices = {
        str(row.get("symbol")): _performance_price(row)
        for row in current_rows
        if str(row.get("market")) == market and _session_date(row) == session_date
    }
    market_sessions = sorted({
        str(item.get("session_date"))
        for item in snapshots
        if str(item.get("market")) == market and item.get("session_date")
    } | {session_date})
    positions = {value: index for index, value in enumerate(market_sessions)}
    current_index = positions[session_date]
    for snapshot in snapshots:
        if str(snapshot.get("market")) != market:
            continue
        origin = str(snapshot.get("session_date") or "")
        if origin not in positions or positions[origin] >= current_index:
            continue
        elapsed = current_index - positions[origin]
        if elapsed not in HORIZONS:
            continue
        key = str(elapsed)
        for row in snapshot.get("predictions", []):
            symbol = str(row.get("symbol") or "")
            base = float(row.get("official_price") or 0)
            current = float(prices.get(symbol) or 0)
            if base <= 0 or current <= 0 or key in row.get("outcomes", {}):
                continue
            row.setdefault("outcomes", {})[key] = {
                "return_pct": round((current / base - 1) * 100, 4),
                "evaluated_session_date": session_date,
                "evaluated_price": round(current, 4),
            }


def update_performance(
    reports_dir: Path,
    predictions: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    updated_at: str,
    period: str,
) -> dict[str, Any]:
    """Evaluate and save only one completed-session snapshot per market."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    history_path = reports_dir / "prediction_history.json"
    legacy_reset = False
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        if int(history.get("version") or 0) != METHODOLOGY_VERSION:
            snapshots: list[dict[str, Any]] = []
            legacy_reset = bool(history.get("snapshots"))
        else:
            snapshots = list(history.get("snapshots", []))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        snapshots = []

    market = _canonical_market(period)
    if market:
        session_dates = sorted({
            _session_date(row)
            for row in current_rows
            if str(row.get("market")) == market and _session_date(row)
        })
        if session_dates:
            session_date = session_dates[-1]
            snapshot_id = f"{market}:{session_date}"
            if not any(str(item.get("id")) == snapshot_id for item in snapshots):
                _evaluate_with_new_session(snapshots, market, session_date, current_rows)
                snapshot = _new_snapshot(market, session_date, predictions, updated_at, period)
                if snapshot["predictions"]:
                    snapshots.append(snapshot)

    cutoff = date.fromisoformat(updated_at[:10]) - timedelta(days=365)
    snapshots = [
        item for item in snapshots
        if str(item.get("session_date") or "") >= cutoff.isoformat()
    ]
    summary = _summary(snapshots, legacy_reset=legacy_reset)
    history_path.write_text(
        json.dumps({"version": METHODOLOGY_VERSION, "snapshots": snapshots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "performance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def load_performance_context(reports_dir: Path) -> dict[str, Any]:
    """Load verified V2 outcomes; legacy metrics never affect scoring."""
    try:
        value = json.loads((reports_dir / "performance.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}
    if int(value.get("methodology_version") or 0) != METHODOLOGY_VERSION:
        return {}
    return value
