"""Auditable forward testing for completed exchange sessions only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from model_lab import MODEL_NAMES, evaluate_direction, track_predictions


HORIZONS = (1, 5, 10, 20)
METHODOLOGY_VERSION = 5
MINIMUM_TRADING_DAYS = 60
MINIMUM_CONSENSUS_SAMPLES = 200
RETURN_TRACKS = {
    "overnight": "close_to_open_return_pct",
    "session": "open_to_close_return_pct",
    "full_day": "close_to_close_return_pct",
}


def _outcome_return(value: Any, field: str = "close_to_close_return_pct") -> float | None:
    if isinstance(value, dict):
        value = value.get(field, value.get("return_pct") if field == "close_to_close_return_pct" else None)
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


def _track_bundle(
    snapshots: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    direction_for: Callable[[dict[str, Any], str], str],
) -> dict[str, Any]:
    """Score the three auditable components of the next completed session."""
    result: dict[str, Any] = {}
    for track, field in RETURN_TRACKS.items():
        records: list[tuple[float, str]] = []
        eligible = 0
        for snapshot in snapshots:
            for row in snapshot.get("predictions", []):
                if not predicate(row):
                    continue
                value = _outcome_return(row.get("outcomes", {}).get("1"), field)
                if value is None:
                    continue
                eligible += 1
                direction = direction_for(row, track)
                if evaluate_direction(direction, value) is not None:
                    records.append((value, direction))
        result[track] = _metric(records, eligible)
    return result


def _consensus_direction(row: dict[str, Any]) -> str:
    return _track_consensus_direction(row, "full_day")


def _track_consensus_direction(row: dict[str, Any], track: str) -> str:
    return str(
        row.get("track_predictions", {})
        .get(track, {})
        .get("consensus", {})
        .get("direction")
        or "ABSTAIN"
    )


def _trade_direction(row: dict[str, Any]) -> str:
    return "UP" if row.get("trade_triggered") else "ABSTAIN"


def _track_trade_direction(row: dict[str, Any], _track: str) -> str:
    return _trade_direction(row)


def _model_direction(name: str) -> Callable[[dict[str, Any]], str]:
    return lambda row: _track_model_direction(row, "full_day", name)


def _track_model_direction(row: dict[str, Any], track: str, name: str) -> str:
    return str(
        row.get("track_predictions", {})
        .get(track, {})
        .get("models", {})
        .get(name, {})
        .get("direction")
        or "ABSTAIN"
    )


def _summary(snapshots: list[dict[str, Any]], legacy_reset: bool = False) -> dict[str, Any]:
    horizons = _metric_bundle(snapshots, lambda _row: True, _consensus_direction)
    tracks = _track_bundle(snapshots, lambda _row: True, _track_consensus_direction)
    trade_horizons = _metric_bundle(snapshots, lambda _row: True, _trade_direction)
    trade_tracks = _track_bundle(snapshots, lambda _row: True, _track_trade_direction)
    models = {
        name: {
            "horizons": _metric_bundle(snapshots, lambda _row: True, _model_direction(name)),
            "tracks": {
                track: _track_bundle(
                    snapshots,
                    lambda _row: True,
                    lambda row, requested, model=name: _track_model_direction(row, requested, model),
                )[track]
                for track in RETURN_TRACKS
            },
        }
        for name in MODEL_NAMES
    }
    leaderboards = {
        track: sorted(
            ({"name": name, **metrics["tracks"][track]} for name, metrics in models.items()),
            key=lambda item: (
                int(item["samples"]) >= 30,
                float(item["win_rate_pct"]),
                int(item["samples"]),
            ),
            reverse=True,
        )
        for track in RETURN_TRACKS
    }

    groups: dict[str, Any] = {}
    for group in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        predicate = lambda row, expected=group: str(row.get("cohort")) == expected
        groups[group] = {
            "horizons": _metric_bundle(snapshots, predicate, _consensus_direction),
            "tracks": _track_bundle(snapshots, predicate, _track_consensus_direction),
            "trade_signals": _metric_bundle(snapshots, predicate, _trade_direction),
            "trade_tracks": _track_bundle(snapshots, predicate, _track_trade_direction),
            "models": {
                name: {
                    "horizons": _metric_bundle(snapshots, predicate, _model_direction(name)),
                    "tracks": {
                        track: _track_bundle(
                            snapshots,
                            predicate,
                            lambda row, requested, model=name: _track_model_direction(row, requested, model),
                        )[track]
                        for track in RETURN_TRACKS
                    },
                }
                for name in MODEL_NAMES
            },
        }

    session_dates = {str(item.get("session_date")) for item in snapshots if item.get("predictions")}
    collected = len(session_dates)
    one_day_samples = int(horizons["1"]["samples"])
    ready_for_model_selection = (
        collected >= MINIMUM_TRADING_DAYS
        and one_day_samples >= MINIMUM_CONSENSUS_SAMPLES
    )
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_count": len(snapshots),
        "horizons": horizons,
        "tracks": tracks,
        "trade_signals": trade_horizons,
        "trade_tracks": trade_tracks,
        "models": models,
        "model_leaderboard": leaderboards["full_day"],
        "model_leaderboards": leaderboards,
        "groups": groups,
        "calibration": {
            "trading_days_collected": collected,
            "minimum_trading_days": MINIMUM_TRADING_DAYS,
            "minimum_consensus_samples": MINIMUM_CONSENSUS_SAMPLES,
            "remaining_trading_days": max(0, MINIMUM_TRADING_DAYS - collected),
            "status": (
                "ready_for_model_selection"
                if ready_for_model_selection
                else "collecting_clean_outcomes"
            ),
            # Promotion is a separate, auditable step. Reaching the sample
            # gate only makes the leaderboard eligible for selection; it must
            # never silently change the production ranking model.
            "ready_for_model_selection": ready_for_model_selection,
            "affects_ai_score": False,
            "eligible_one_day_samples": one_day_samples,
            "legacy_history_reset": legacy_reset,
        },
        "note": (
            "V5延續三段分離驗證，並將台股與美股改為不同權重及收盤檢查點；"
            "台股、台灣ETF、美股、美國ETF分開統計。未通過樣本門檻前不影響AI排名。"
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


def _performance_open_price(row: dict[str, Any]) -> float:
    for key in ("official_adjusted_open_price", "official_open_price"):
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
        tracks = track_predictions(row)
        full_day = tracks["full_day"]
        is_etf = "ETF" in str(row.get("type", "")) or str(row.get("backtest_group", "")).endswith("ETF")
        cohort = f"{market}_{'ETF' if is_etf else 'STOCK'}"
        rows.append({
            "symbol": symbol,
            "name": row.get("name"),
            "market": market,
            "group": row.get("backtest_group"),
            "cohort": cohort,
            "rank": row.get("backtest_rank"),
            "official_session_date": session_date,
            "official_price": round(price, 4),
            "track_predictions": tracks,
            # Compatibility aliases are explicitly the full-day prediction.
            "model_predictions": full_day["models"],
            "consensus": full_day["consensus"],
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
        str(row.get("symbol")): (
            _performance_open_price(row),
            _performance_price(row),
        )
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
            current_open, current_close = prices.get(symbol, (0.0, 0.0))
            if base <= 0 or current_close <= 0 or key in row.get("outcomes", {}):
                continue
            close_to_close = round((current_close / base - 1) * 100, 4)
            outcome = {
                # Keep return_pct as a compatibility alias for downstream
                # readers while V5 exposes the exact comparison explicitly.
                "return_pct": close_to_close,
                "close_to_close_return_pct": close_to_close,
                "evaluated_session_date": session_date,
                "evaluated_price": round(current_close, 4),
                "evaluated_close_price": round(current_close, 4),
            }
            if elapsed == 1 and current_open > 0:
                outcome.update({
                    "close_to_open_return_pct": round((current_open / base - 1) * 100, 4),
                    "open_to_close_return_pct": round((current_close / current_open - 1) * 100, 4),
                    "evaluated_open_price": round(current_open, 4),
                })
            row.setdefault("outcomes", {})[key] = outcome


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
    """Load verified V5 outcomes; legacy metrics never affect scoring."""
    try:
        value = json.loads((reports_dir / "performance.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}
    if int(value.get("methodology_version") or 0) != METHODOLOGY_VERSION:
        return {}
    return value
