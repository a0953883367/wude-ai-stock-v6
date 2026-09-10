"""Explain shadow rankings and discover forward-only return associations.

The module deliberately describes associations instead of causation.  It only
uses immutable point-in-time features paired with already matured outcomes and
never writes formal V6 scores, weights, rankings, or orders.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .features import FEATURE_NAMES
from .models import BASE_WEIGHTS, HORIZONS


FEATURE_LABELS = {
    "trend": "價格趨勢",
    "volume": "成交量",
    "capital_flow": "大量資金流",
    "positioning": "法人／籌碼",
    "sector": "產業強弱",
    "market_regime": "大盤環境",
    "fundamental": "基本面",
    "valuation": "估值",
    "news": "新聞事件",
    "entry": "進場位置",
    "shadow_consensus": "影子訊號共識",
    "industry_lifecycle": "產業生命週期",
}
MODEL_FEATURES = tuple(name for name in FEATURE_NAMES if name != "quality")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def explain_prediction(
    features: dict[str, float],
    horizon_code: str,
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Return transparent per-feature price-direction contributions."""
    selected = weights or BASE_WEIGHTS[horizon_code]
    scale = float(HORIZONS[horizon_code]["return_scale"])
    divisor = sum(abs(value) for value in selected.values()) or 1.0
    rows = []
    for name in MODEL_FEATURES:
        if name not in selected:
            continue
        score = _number(features.get(name), 0.5)
        contribution = (score - 0.5) * _number(selected.get(name))
        if not weights:
            contribution = contribution / divisor * scale
        rows.append({
            "factor": name,
            "label": FEATURE_LABELS.get(name, name),
            "score_pct": round(score * 100.0, 1),
            "expected_return_contribution_pct": round(contribution, 3),
            "direction": "up" if contribution > 0 else "down" if contribution < 0 else "neutral",
        })
    upward = sorted(
        (row for row in rows if row["direction"] == "up"),
        key=lambda row: (-row["expected_return_contribution_pct"], row["factor"]),
    )[:3]
    downward = sorted(
        (row for row in rows if row["direction"] == "down"),
        key=lambda row: (row["expected_return_contribution_pct"], row["factor"]),
    )[:3]
    used = [row for row in rows if row["score_pct"] != 50.0]
    return {
        "wording": "資料與預期報酬的模型貢獻，不代表因果或保證漲跌",
        "available_factor_count": len(used),
        "model_factor_count": len(rows),
        "upward_drivers": upward,
        "downward_drivers": downward,
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else None


def _factor_metrics(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    values = [_number(row.get("features", {}).get(name), 0.5) for row in rows]
    returns = [_number(row.get("realized_return_pct")) for row in rows]
    high_returns = [ret for value, ret in zip(values, returns) if value >= 0.60]
    low_returns = [ret for value, ret in zip(values, returns) if value <= 0.40]
    correlation = _pearson(values, returns)
    high_average = sum(high_returns) / len(high_returns) if high_returns else None
    low_average = sum(low_returns) / len(low_returns) if low_returns else None
    spread = (
        high_average - low_average
        if high_average is not None and low_average is not None else None
    )
    return {
        "factor": name,
        "label": FEATURE_LABELS.get(name, name),
        "sample_count": len(rows),
        "high_sample_count": len(high_returns),
        "low_sample_count": len(low_returns),
        "high_positive_rate_pct": (
            round(sum(value > 0 for value in high_returns) / len(high_returns) * 100.0, 2)
            if high_returns else None
        ),
        "high_average_return_pct": round(high_average, 4) if high_average is not None else None,
        "low_average_return_pct": round(low_average, 4) if low_average is not None else None,
        "high_minus_low_return_pct": round(spread, 4) if spread is not None else None,
        "correlation": round(correlation, 4) if correlation is not None else None,
        "sufficient_bucket_samples": len(high_returns) >= 20 and len(low_returns) >= 20,
    }


def build_factor_discovery(
    training_rows: Callable[[str, str, str], list[dict[str, Any]]],
    groups: tuple[str, ...],
) -> dict[str, Any]:
    """Summarize which frozen inputs are associated with matured returns."""
    result: dict[str, dict[str, Any]] = {}
    for group in groups:
        market = group.split("_", 1)[0]
        result[group] = {}
        for horizon_code in HORIZONS:
            rows = training_rows(market, group, horizon_code)
            sessions = {str(row.get("session_date") or "") for row in rows}
            metrics = [_factor_metrics(rows, name) for name in MODEL_FEATURES]
            eligible = [row for row in metrics if row["sufficient_bucket_samples"]]
            enough_history = len(rows) >= 100 and len(sessions) >= 20
            rising = sorted(
                (row for row in eligible if _number(row.get("high_minus_low_return_pct")) > 0),
                key=lambda row: (-_number(row["high_minus_low_return_pct"]), row["factor"]),
            )[:5] if enough_history else []
            falling = sorted(
                (row for row in eligible if _number(row.get("high_minus_low_return_pct")) < 0),
                key=lambda row: (_number(row["high_minus_low_return_pct"]), row["factor"]),
            )[:5] if enough_history else []
            ready = enough_history and bool(eligible)
            result[group][horizon_code] = {
                "status": "preliminary_associations_ready" if ready else "collecting_matured_outcomes",
                "sample_count": len(rows),
                "session_count": len(sessions),
                "minimum_samples": 100,
                "minimum_sessions": 20,
                "rising_associations": rising,
                "falling_associations": falling,
                "wording": "只表示歷史樣本外關聯，不宣稱因果；60／126日後再複核",
            }
    return {
        "mode": "forward_only_matured_outcome_association",
        "markets_separate": True,
        "asset_groups_separate": True,
        "horizons_separate": True,
        "future_data_forbidden": True,
        "formal_v6_unchanged": True,
        "formal_rankings_unchanged": True,
        "automatic_orders": False,
        "groups": result,
    }
