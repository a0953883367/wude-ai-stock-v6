"""Auditable forward testing for completed exchange sessions only."""

from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from model_lab import MODEL_NAMES, evaluate_direction, track_predictions


HORIZONS = (1, 5, 10, 20)
METHODOLOGY_VERSION = 6
AUDIT_SCHEMA_VERSION = 3
MINIMUM_TRADING_DAYS = 60
MINIMUM_CONSENSUS_SAMPLES = 200
TOP_K = (5, 10, 20)
MARKET_REGIMES = ("bull", "bear", "sideways")
MARKET_REGIME_LABELS = {"bull": "多頭", "bear": "空頭", "sideways": "盤整"}
TW_THRESHOLD_GRID = (
    (58.0, 6),
    (60.0, 6),
    (62.0, 6),
    (65.0, 6),
    (60.0, 7),
    (62.0, 7),
)
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
    actual = [value for value, _direction in records]
    if not directional:
        return {
            "samples": 0,
            "eligible_samples": eligible,
            "abstain_samples": eligible,
            "coverage_pct": 0.0,
            "win_rate_pct": 0.0,
            "avg_return_pct": 0.0,
            "worst_return_pct": 0.0,
            "lowest_actual_return_pct": 0.0,
            "highest_actual_return_pct": 0.0,
            "sample_status": _sample_status(0),
        }
    return {
        "samples": len(directional),
        "eligible_samples": eligible,
        "abstain_samples": max(0, eligible - len(directional)),
        "coverage_pct": round(len(directional) / max(eligible, 1) * 100, 1),
        "win_rate_pct": round(sum(value > 0 for value in directional) / len(directional) * 100, 1),
        "avg_return_pct": round(sum(directional) / len(directional), 2),
        "worst_return_pct": round(min(directional), 2),
        "lowest_actual_return_pct": round(min(actual), 2),
        "highest_actual_return_pct": round(max(actual), 2),
        "sample_status": _sample_status(len(directional)),
    }


def _shadow_consensus_direction(
    row: dict[str, Any], threshold: float, minimum_votes: int
) -> str:
    """Re-score stored TW model votes without changing production forecasts."""
    models = (
        row.get("track_predictions", {})
        .get("full_day", {})
        .get("models", {})
    )
    up = down = 0
    for vote in models.values():
        try:
            score = float(vote.get("score"))
            coverage = float(vote.get("evidence_coverage_pct"))
        except (AttributeError, TypeError, ValueError):
            continue
        if coverage < 75.0:
            continue
        if score >= threshold:
            up += 1
        elif score <= 100.0 - threshold:
            down += 1
    if up >= minimum_votes and up >= down + 3:
        return "UP"
    if down >= minimum_votes and down >= up + 3:
        return "DOWN"
    return "ABSTAIN"


def _tw_threshold_calibration(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare TW research thresholds on immutable completed outcomes only."""
    cohorts: dict[str, list[dict[str, Any]]] = {"TW_STOCK": [], "TW_ETF": []}
    for cohort in cohorts:
        for threshold, minimum_votes in TW_THRESHOLD_GRID:
            records: list[tuple[float, str]] = []
            eligible = 0
            for snapshot in snapshots:
                for row in snapshot.get("predictions", []):
                    if str(row.get("cohort")) != cohort:
                        continue
                    value = _outcome_return(row.get("outcomes", {}).get("1"))
                    if value is None:
                        continue
                    eligible += 1
                    direction = _shadow_consensus_direction(row, threshold, minimum_votes)
                    if evaluate_direction(direction, value) is not None:
                        records.append((value, direction))
            cohorts[cohort].append({
                "score_threshold": threshold,
                "minimum_votes": minimum_votes,
                **_metric(records, eligible),
            })
    return {
        "mode": "shadow_only",
        "minimum_samples_before_review": 20,
        "affects_ai_score": False,
        "automatic_promotion": False,
        "cohorts": cohorts,
        "note": "只比較已固定台股預測的完成結果；不讀取美股，也不自動修改正式權重。",
    }


def _sample_status(samples: int) -> str:
    if samples < 20:
        return "樣本不足"
    if samples < 60:
        return "初步參考"
    return "較具參考性"


def _within_rank(row: dict[str, Any], maximum: int) -> bool:
    try:
        rank = int(row.get("rank") or 0)
    except (TypeError, ValueError):
        return False
    return 0 < rank <= maximum


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


def _max_drawdown_pct(returns: list[float]) -> float:
    equity = peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak * 100)
    return round(maximum, 2)


def _regime_metric(
    snapshots: list[dict[str, Any]],
    *,
    market: str,
    regime: str,
    horizon: str,
    predicate: Callable[[dict[str, Any]], bool],
    direction_for: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Evaluate one strategy inside one source-date market regime."""
    records: list[tuple[float, str]] = []
    excess_returns: list[float] = []
    benchmark_returns: list[float] = []
    session_returns: list[float] = []
    eligible = 0
    for snapshot in sorted(snapshots, key=lambda item: str(item.get("session_date") or "")):
        source_regime = snapshot.get("market_regime") or {}
        if str(snapshot.get("market")) != market or source_regime.get("regime") != regime:
            continue
        local_returns: list[float] = []
        for row in snapshot.get("predictions", []):
            if str(row.get("cohort")) != f"{market}_STOCK":
                continue
            if not predicate(row):
                continue
            outcome = row.get("outcomes", {}).get(horizon)
            actual = _outcome_return(outcome)
            if actual is None:
                continue
            eligible += 1
            direction = direction_for(row)
            if evaluate_direction(direction, actual) is None:
                continue
            directional = actual if direction == "UP" else -actual
            records.append((actual, direction))
            local_returns.append(directional)
            if isinstance(outcome, dict):
                benchmark = _outcome_return(outcome.get("benchmark_close_to_close_return_pct"))
                if benchmark is not None:
                    directional_benchmark = benchmark if direction == "UP" else -benchmark
                    benchmark_returns.append(directional_benchmark)
                    excess_returns.append(directional - directional_benchmark)
        if local_returns:
            session_returns.append(sum(local_returns) / len(local_returns))
    metric = _metric(records, eligible)
    directional_values = [value if direction == "UP" else -value for value, direction in records]
    gains = sum(value for value in directional_values if value > 0)
    losses = abs(sum(value for value in directional_values if value < 0))
    metric.update({
        "sessions": len(session_returns),
        "max_drawdown_pct": _max_drawdown_pct(session_returns) if horizon == "1" else None,
        "drawdown_basis": "不重疊隔日訊號序列" if horizon == "1" else "多日報酬重疊，不計回撤",
        "profit_factor": round(gains / losses, 2) if losses > 0 else None,
        "benchmark_avg_return_pct": round(sum(benchmark_returns) / len(benchmark_returns), 2) if benchmark_returns else None,
        "avg_excess_return_pct": round(sum(excess_returns) / len(excess_returns), 2) if excess_returns else None,
    })
    return metric


def _regime_validation(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    strategies = {
        "consensus": (lambda _row: True, _consensus_direction),
        "buy_trigger": (lambda row: bool(row.get("trade_triggered")), _trade_direction),
        "top5": (lambda row: _within_rank(row, 5), _consensus_direction),
        "top10": (lambda row: _within_rank(row, 10), _consensus_direction),
    }
    markets: dict[str, Any] = {}
    for market in ("TW", "US"):
        markets[market] = {}
        for regime in MARKET_REGIMES:
            markets[market][regime] = {
                "label": MARKET_REGIME_LABELS[regime],
                "source_sessions": sum(
                    str(snapshot.get("market")) == market
                    and (snapshot.get("market_regime") or {}).get("regime") == regime
                    for snapshot in snapshots
                ),
                "strategies": {
                    name: {
                        horizon: _regime_metric(
                            snapshots,
                            market=market,
                            regime=regime,
                            horizon=horizon,
                            predicate=predicate,
                            direction_for=direction_for,
                        )
                        for horizon in map(str, HORIZONS)
                    }
                    for name, (predicate, direction_for) in strategies.items()
                },
            }
    classified = sum(
        (snapshot.get("market_regime") or {}).get("regime") in MARKET_REGIMES
        for snapshot in snapshots
    )
    return {
        "mode": "forward_only",
        "asset_scope": "STOCK_ONLY",
        "affects_ai_score": False,
        "classified_snapshot_count": classified,
        "unclassified_snapshot_count": len(snapshots) - classified,
        "regime_rule": (
            "多頭：收盤高於MA60 1%、MA20高於MA60 0.5%、20日報酬至少2%且MA20上彎；"
            "空頭條件反向；其餘為盤整。全部只使用預測當日及以前資料。"
        ),
        "strategies": {
            "consensus": "10模型共識",
            "buy_trigger": "實際買點觸發",
            "top5": "排名前5共識",
            "top10": "排名前10共識",
        },
        "markets": markets,
    }


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
            "top_k": {
                str(limit): {
                    "tracks": _track_bundle(
                        snapshots,
                        lambda row, base=predicate, maximum=limit: (
                            base(row) and _within_rank(row, maximum)
                        ),
                        _track_consensus_direction,
                    ),
                    "horizons": _metric_bundle(
                        snapshots,
                        lambda row, base=predicate, maximum=limit: (
                            base(row) and _within_rank(row, maximum)
                        ),
                        _consensus_direction,
                    ),
                }
                for limit in TOP_K
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
        "regime_validation": _regime_validation(snapshots),
        "tw_threshold_calibration": _tw_threshold_calibration(snapshots),
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
            "V6延續三段分離驗證，分開研究方向與強訊號，並使用台美股各自收盤檢查點；"
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


def _performance_session_price(row: dict[str, Any], kind: str) -> float:
    for key in (f"official_adjusted_{kind}_price", f"official_{kind}_price"):
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
    market_regime: dict[str, Any] | None = None,
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
            "model_version": row.get("next_session_model_version") or "V6-shadow",
            "market_model": row.get("next_session_market_model") or row.get("market_model_version"),
            "official_session_date": session_date,
            "official_price": round(price, 4),
            "entry_low": row.get("short_term_entry_low"),
            "entry_high": row.get("short_term_entry_high"),
            "stop_price": row.get("short_term_stop"),
            "target1_price": row.get("short_term_target1"),
            "target2_price": row.get("short_term_target2"),
            "track_predictions": tracks,
            # Compatibility aliases are explicitly the full-day prediction.
            "model_predictions": full_day["models"],
            "consensus": full_day["consensus"],
            "trade_triggered": _trade_triggered(row, price),
            "outcomes": {},
        })
    snapshot = {
        "id": f"{market}:{session_date}",
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "market": market,
        "session_date": session_date,
        "captured_at": updated_at,
        "period": period,
        "market_regime": market_regime or None,
        "market_regime_frozen": bool(market_regime),
        "predictions": rows,
    }
    snapshot["integrity_sha256"] = _snapshot_hash(snapshot)
    return snapshot


def _immutable_snapshot_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    predictions = []
    for row in snapshot.get("predictions", []):
        if not isinstance(row, dict):
            continue
        predictions.append({key: value for key, value in row.items() if key != "outcomes"})
    payload = {
        "id": snapshot.get("id"),
        "audit_schema_version": snapshot.get("audit_schema_version"),
        "market": snapshot.get("market"),
        "session_date": snapshot.get("session_date"),
        "captured_at": snapshot.get("captured_at"),
        "period": snapshot.get("period"),
        "predictions": predictions,
    }
    # Old V6 snapshots did not contain a regime label.  Omitting the absent
    # key keeps their existing integrity hashes valid instead of rewriting history.
    if int(snapshot.get("audit_schema_version") or 0) >= 3:
        frozen = bool(snapshot.get("market_regime_frozen"))
        payload["market_regime_frozen"] = frozen
        if frozen:
            payload["market_regime"] = snapshot.get("market_regime")
    return payload


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        _immutable_snapshot_payload(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_integrity(snapshot: dict[str, Any]) -> str:
    recorded = str(snapshot.get("integrity_sha256") or "")
    if not recorded:
        return "legacy_unverified"
    return "verified" if recorded == _snapshot_hash(snapshot) else "mismatch"


def _evaluate_with_new_session(
    snapshots: list[dict[str, Any]],
    market: str,
    session_date: str,
    current_rows: list[dict[str, Any]],
    market_regime_history: dict[str, dict[str, Any]] | None = None,
) -> None:
    prices = {
        str(row.get("symbol")): (
            _performance_open_price(row),
            _performance_price(row),
            _performance_session_price(row, "high"),
            _performance_session_price(row, "low"),
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
    current_market_regime = (market_regime_history or {}).get(session_date) or {}
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
            current_open, current_close, current_high, current_low = prices.get(
                symbol, (0.0, 0.0, 0.0, 0.0)
            )
            if base <= 0 or current_close <= 0 or key in row.get("outcomes", {}):
                continue
            close_to_close = round((current_close / base - 1) * 100, 4)
            outcome = {
                # Keep return_pct as a compatibility alias for downstream
                # readers while V6 exposes the exact comparison explicitly.
                "return_pct": close_to_close,
                "close_to_close_return_pct": close_to_close,
                "evaluated_session_date": session_date,
                "evaluated_price": round(current_close, 4),
                "evaluated_close_price": round(current_close, 4),
            }
            source_benchmark = (
                snapshot.get("market_regime")
                or (market_regime_history or {}).get(origin)
                or {}
            )
            current_benchmark = current_market_regime
            try:
                source_benchmark_close = float(source_benchmark.get("benchmark_close") or 0)
                current_benchmark_close = float(current_benchmark.get("benchmark_close") or 0)
            except (TypeError, ValueError):
                source_benchmark_close = current_benchmark_close = 0.0
            if (
                source_benchmark_close > 0
                and current_benchmark_close > 0
                and source_benchmark.get("benchmark") == current_benchmark.get("benchmark")
            ):
                outcome["benchmark_close_to_close_return_pct"] = round(
                    (current_benchmark_close / source_benchmark_close - 1) * 100, 4
                )
            if elapsed == 1 and current_open > 0:
                outcome.update({
                    "close_to_open_return_pct": round((current_open / base - 1) * 100, 4),
                    "open_to_close_return_pct": round((current_close / current_open - 1) * 100, 4),
                    "evaluated_open_price": round(current_open, 4),
                })
            if elapsed == 1 and current_high > 0 and current_low > 0:
                outcome.update({
                    "evaluated_high_price": round(current_high, 4),
                    "evaluated_low_price": round(current_low, 4),
                    "stop_touched": bool(
                        float(row.get("stop_price") or 0) > 0
                        and current_low <= float(row.get("stop_price") or 0)
                    ),
                    "target1_touched": bool(
                        float(row.get("target1_price") or 0) > 0
                        and current_high >= float(row.get("target1_price") or 0)
                    ),
                    "target2_touched": bool(
                        float(row.get("target2_price") or 0) > 0
                        and current_high >= float(row.get("target2_price") or 0)
                    ),
                })
            row.setdefault("outcomes", {})[key] = outcome


def _accuracy_audit(snapshots: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    integrity = {"verified": 0, "legacy_unverified": 0, "mismatch": 0}
    for snapshot in snapshots:
        integrity[_snapshot_integrity(snapshot)] += 1
    recent: list[dict[str, Any]] = []
    for snapshot in reversed(snapshots):
        status = _snapshot_integrity(snapshot)
        if status == "mismatch":
            continue
        for row in snapshot.get("predictions", []):
            outcome = row.get("outcomes", {}).get("1")
            if not isinstance(outcome, dict):
                continue
            tracks = row.get("track_predictions", {})
            track_results = {}
            for track, field in RETURN_TRACKS.items():
                actual = _outcome_return(outcome, field)
                direction = _track_consensus_direction(row, track)
                track_results[track] = {
                    "direction": direction,
                    "confidence": (tracks.get(track, {}).get("consensus", {}) or {}).get("confidence"),
                    "actual_return_pct": actual,
                    "hit": evaluate_direction(direction, actual) if actual is not None else None,
                }
            recent.append({
                "snapshot_id": snapshot.get("id"),
                "integrity": status,
                "captured_at": snapshot.get("captured_at"),
                "source_session_date": snapshot.get("session_date"),
                "evaluated_session_date": outcome.get("evaluated_session_date"),
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "cohort": row.get("cohort"),
                "rank": row.get("rank"),
                "model_version": row.get("model_version"),
                "market_model": row.get("market_model"),
                "source_market_regime": (snapshot.get("market_regime") or {}).get("regime"),
                "source_market_regime_label": MARKET_REGIME_LABELS.get(
                    (snapshot.get("market_regime") or {}).get("regime")
                ),
                "source_close_price": row.get("official_price"),
                "actual_open_price": outcome.get("evaluated_open_price"),
                "actual_high_price": outcome.get("evaluated_high_price"),
                "actual_low_price": outcome.get("evaluated_low_price"),
                "actual_close_price": outcome.get("evaluated_close_price"),
                "benchmark_return_pct": outcome.get("benchmark_close_to_close_return_pct"),
                "tracks": track_results,
                "stop_touched": outcome.get("stop_touched"),
                "target1_touched": outcome.get("target1_touched"),
                "target2_touched": outcome.get("target2_touched"),
            })
    public_groups = {
        key: {
            "tracks": value.get("tracks", {}),
            "top_k": value.get("top_k", {}),
        }
        for key, value in summary.get("groups", {}).items()
    }
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "updated_at": summary.get("updated_at"),
        "immutable_rule": "同一市場與交易日只建立一次預測；結果只追加，不覆寫預測內容。",
        "integrity": integrity,
        "calibration": summary.get("calibration", {}),
        "tw_threshold_calibration": summary.get("tw_threshold_calibration", {}),
        "groups": public_groups,
        "tracks": summary.get("tracks", {}),
        "regime_validation": summary.get("regime_validation", {}),
        "recent": recent[:200],
    }


def update_performance(
    reports_dir: Path,
    predictions: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    updated_at: str,
    period: str,
    market_regimes: dict[str, dict[str, dict[str, Any]]] | None = None,
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
            current_market_regime = (market_regimes or {}).get(market, {}).get(session_date)
            snapshot_id = f"{market}:{session_date}"
            if not any(str(item.get("id")) == snapshot_id for item in snapshots):
                _evaluate_with_new_session(
                    snapshots,
                    market,
                    session_date,
                    current_rows,
                    (market_regimes or {}).get(market, {}),
                )
                snapshot = _new_snapshot(
                    market,
                    session_date,
                    predictions,
                    updated_at,
                    period,
                    current_market_regime,
                )
                if snapshot["predictions"]:
                    snapshots.append(snapshot)

    cutoff = date.fromisoformat(updated_at[:10]) - timedelta(days=365)
    snapshots = [
        item for item in snapshots
        if str(item.get("session_date") or "") >= cutoff.isoformat()
    ]
    def with_historical_regime(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for item in items:
            if item.get("market_regime"):
                enriched.append(item)
                continue
            historical_regime = (
                (market_regimes or {})
                .get(str(item.get("market") or ""), {})
                .get(str(item.get("session_date") or ""))
            )
            enriched.append(
                {**item, "market_regime": historical_regime}
                if historical_regime
                else item
            )
        return enriched

    valid_snapshots = [item for item in snapshots if _snapshot_integrity(item) != "mismatch"]
    analysis_snapshots = with_historical_regime(valid_snapshots)
    audit_snapshots = with_historical_regime(snapshots)
    summary = _summary(analysis_snapshots, legacy_reset=legacy_reset)
    history_path.write_text(
        json.dumps({"version": METHODOLOGY_VERSION, "snapshots": snapshots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "performance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (reports_dir / "accuracy.json").write_text(
        json.dumps(_accuracy_audit(audit_snapshots, summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def load_performance_context(reports_dir: Path) -> dict[str, Any]:
    """Load verified V6 outcomes; legacy metrics never affect scoring."""
    try:
        value = json.loads((reports_dir / "performance.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}
    if int(value.get("methodology_version") or 0) != METHODOLOGY_VERSION:
        return {}
    return value


def load_frozen_forecasts(
    reports_dir: Path, market: str = "TW"
) -> dict[str, dict[str, Any]]:
    """Return the latest verified fixed forecast for one market.

    This is deliberately separate from the mutable report files. Re-running a
    report for the same completed session must render the immutable forecast,
    even when a weekend/provider refresh temporarily has less data.
    """
    try:
        history = json.loads(
            (reports_dir / "prediction_history.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, TypeError, OSError):
        return {}
    if int(history.get("version") or 0) != METHODOLOGY_VERSION:
        return {}
    selected: dict[str, dict[str, Any]] = {}
    labels = {"UP": "📈 看漲", "DOWN": "📉 看跌", "ABSTAIN": "⚪ 棄權"}
    snapshots = [
        item for item in history.get("snapshots", [])
        if str(item.get("market") or "").upper() == market.upper()
        and _snapshot_integrity(item) == "verified"
    ]
    snapshots.sort(key=lambda item: (str(item.get("session_date") or ""), str(item.get("captured_at") or "")))
    for snapshot in snapshots:
        for row in snapshot.get("predictions", []):
            tracks = row.get("track_predictions", {})
            compact = {
                track: (bundle.get("consensus", {}) if isinstance(bundle, dict) else {})
                for track, bundle in tracks.items()
            }
            full_day = compact.get("full_day", {})
            direction = str(full_day.get("direction") or "ABSTAIN")
            qualities = []
            for bundle in tracks.values():
                for vote in (bundle.get("models", {}) if isinstance(bundle, dict) else {}).values():
                    try:
                        qualities.append(float(vote.get("evidence_coverage_pct")))
                    except (AttributeError, TypeError, ValueError):
                        pass
            selected[str(row.get("symbol") or "")] = {
                "next_session_model_version": row.get("model_version") or "V6-shadow",
                "next_session_market_model": row.get("market_model") or "TW-NEXT-V6",
                "next_session_direction": labels.get(direction, "⚪ 棄權"),
                "next_session_confidence": full_day.get("confidence") or 0.0,
                "next_session_up_votes": full_day.get("up_votes") or 0,
                "next_session_down_votes": full_day.get("down_votes") or 0,
                "next_session_abstain_votes": full_day.get("abstain_votes") or 0,
                "next_session_tracks": compact,
                "next_session_model_votes": (
                    tracks.get("full_day", {}).get("models", {})
                    if isinstance(tracks.get("full_day", {}), dict)
                    else {}
                ),
                "next_session_note": "沿用同一交易日雜湊驗證的固定預測；重新整理不重新配分。",
                "next_session_source_session_date": snapshot.get("session_date") or "",
                "next_session_generated_at": snapshot.get("captured_at") or "",
                "next_session_signal_level": full_day.get("signal_level") or "ABSTAIN",
                "next_session_data_quality": min(qualities) if qualities else 0.0,
                "next_session_data_mode": "固定快照（雜湊驗證通過）",
            }
    return selected
