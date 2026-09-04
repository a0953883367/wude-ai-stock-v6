"""Multi-horizon champion forecasts and controlled challenger training."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np

from .features import FEATURE_NAMES, clamp


MODEL_VERSION = "WUDE-PREDICT-ENGINE-V1-CHAMPION"
HORIZONS = {
    "NEXT_1D": {"sessions": 1, "side": "UP", "return_scale": 5.0, "label": "下一交易日"},
    "UP_5D": {"sessions": 5, "side": "UP", "return_scale": 12.0, "label": "未來5交易日"},
    "DOWN_14D": {"sessions": 14, "side": "DOWN", "return_scale": 18.0, "label": "未來14交易日下跌"},
    "DOWN_21D": {"sessions": 21, "side": "DOWN", "return_scale": 22.0, "label": "未來1個月下跌"},
    "UP_45D": {"sessions": 45, "side": "UP", "return_scale": 32.0, "label": "未來45交易日"},
    "UP_60D": {"sessions": 60, "side": "UP", "return_scale": 38.0, "label": "未來60交易日"},
    "UP_126D": {"sessions": 126, "side": "UP", "return_scale": 55.0, "label": "未來6個月"},
}

BASE_WEIGHTS = {
    "NEXT_1D": {"trend": .18, "volume": .16, "capital_flow": .17, "positioning": .10, "sector": .08, "market_regime": .09, "fundamental": .02, "valuation": .01, "news": .07, "entry": .05, "shadow_consensus": .07},
    "UP_5D": {"trend": .18, "volume": .12, "capital_flow": .13, "positioning": .10, "sector": .10, "market_regime": .10, "fundamental": .04, "valuation": .02, "news": .07, "entry": .06, "shadow_consensus": .08},
    "DOWN_14D": {"trend": .17, "volume": .09, "capital_flow": .14, "positioning": .11, "sector": .10, "market_regime": .12, "fundamental": .06, "valuation": .04, "news": .09, "entry": .02, "shadow_consensus": .06},
    "DOWN_21D": {"trend": .15, "volume": .07, "capital_flow": .12, "positioning": .10, "sector": .11, "market_regime": .13, "fundamental": .09, "valuation": .06, "news": .10, "entry": .02, "shadow_consensus": .05},
    "UP_45D": {"trend": .13, "volume": .05, "capital_flow": .08, "positioning": .10, "sector": .12, "market_regime": .13, "fundamental": .14, "valuation": .10, "news": .07, "entry": .03, "shadow_consensus": .05},
    "UP_60D": {"trend": .12, "volume": .04, "capital_flow": .07, "positioning": .09, "sector": .12, "market_regime": .13, "fundamental": .16, "valuation": .12, "news": .07, "entry": .03, "shadow_consensus": .05},
    "UP_126D": {"trend": .08, "volume": .02, "capital_flow": .04, "positioning": .07, "sector": .11, "market_regime": .13, "fundamental": .22, "valuation": .20, "news": .08, "entry": .01, "shadow_consensus": .04},
}

MIN_TRAINING_SESSIONS = {"NEXT_1D": 20, "UP_5D": 20, "DOWN_14D": 30, "DOWN_21D": 35, "UP_45D": 45, "UP_60D": 60, "UP_126D": 126}
MIN_TRAINING_SAMPLES = {key: 200 for key in HORIZONS}
MIN_HOLDOUT_DIRECTION_HIT_PCT = 52.0
MIN_DIRECTION_IMPROVEMENT_PCT = 2.0
MAX_MAE_RATIO = 0.98


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-12.0, min(12.0, value))))


def forecast(
    features: dict[str, float],
    horizon_code: str,
    *,
    chase_risk_points: float,
    data_quality_pct: float,
    trade_blocked: bool,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    spec = HORIZONS[horizon_code]
    selected = weights or BASE_WEIGHTS[horizon_code]
    total = sum(abs(value) for value in selected.values()) or 1.0
    raw = sum((features.get(name, .5) - .5) * value for name, value in selected.items()) / total
    direction_signal = raw if spec["side"] == "UP" else -raw
    expected = raw * float(spec["return_scale"])
    probability = _sigmoid(direction_signal * 5.0) * 100.0
    uncertainty = max(5.0, 100.0 - data_quality_pct)
    downside = max(1.0, float(spec["return_scale"]) * (.30 + uncertainty / 200.0))
    buyability = probability * .65 + features.get("entry", .5) * 35.0
    if spec["side"] == "UP":
        buyability -= chase_risk_points
    else:
        buyability = probability
    if trade_blocked:
        buyability = 0.0
        probability = min(probability, 35.0)
    return {
        "probability_pct": round(clamp(probability), 1),
        "expected_return_pct": round(expected, 2),
        "buyability_score": round(clamp(buyability), 1),
        "downside_risk_pct": round(downside, 2),
        "signal_strength": round(raw * 100.0, 2),
        "target_side": spec["side"],
        "horizon_sessions": spec["sessions"],
        "horizon_label": spec["label"],
    }


def learned_forecast(
    features: dict[str, float],
    horizon_code: str,
    *,
    weights: dict[str, float],
    chase_risk_points: float,
    data_quality_pct: float,
    trade_blocked: bool,
) -> dict[str, Any]:
    """Apply a validated ridge challenger without treating coefficients as votes."""
    spec = HORIZONS[horizon_code]
    expected = float(weights.get("_intercept", 0.0)) + sum(
        (float(features.get(name, .5)) - .5) * float(weights.get(name, 0.0))
        for name in FEATURE_NAMES[:-1]
    )
    scale = float(spec["return_scale"])
    expected = max(-scale, min(scale, expected))
    direction_expected = expected if spec["side"] == "UP" else -expected
    probability = _sigmoid(direction_expected / max(1.0, scale * .35) * 2.0) * 100.0
    quality = clamp(data_quality_pct) / 100.0
    probability = 50.0 + (probability - 50.0) * quality
    uncertainty = max(5.0, 100.0 - data_quality_pct)
    downside = max(1.0, scale * (.30 + uncertainty / 200.0))
    buyability = probability * .65 + features.get("entry", .5) * 35.0
    if spec["side"] == "UP":
        buyability -= chase_risk_points
    else:
        buyability = probability
    if trade_blocked:
        buyability = 0.0
        probability = min(probability, 35.0)
    return {
        "probability_pct": round(clamp(probability), 1),
        "expected_return_pct": round(expected, 2),
        "buyability_score": round(clamp(buyability), 1),
        "downside_risk_pct": round(downside, 2),
        "signal_strength": round(expected / scale * 100.0, 2),
        "target_side": spec["side"],
        "horizon_sessions": spec["sessions"],
        "horizon_label": spec["label"],
    }


def challenger_qualification(model: dict[str, Any]) -> tuple[bool, list[str]]:
    """Require mature samples, a real holdout win and sane coefficients."""
    metrics = model.get("metrics") or {}
    horizon_code = str(model.get("horizon_code") or "")
    challenger_hit = float(metrics.get("walk_forward_holdout_direction_hit_pct") or 0)
    champion_hit = float(
        metrics.get("benchmark_holdout_direction_hit_pct")
        if metrics.get("benchmark_holdout_direction_hit_pct") is not None
        else metrics.get("champion_holdout_direction_hit_pct") or 0
    )
    challenger_mae = float(metrics.get("walk_forward_holdout_mae_pct") or float("inf"))
    champion_mae = float(
        metrics.get("benchmark_holdout_mae_pct")
        if metrics.get("benchmark_holdout_mae_pct") is not None
        else metrics.get("champion_holdout_mae_pct") or 0
    )
    weights = model.get("weights") or {}
    reasons = []
    if horizon_code not in HORIZONS:
        reasons.append("未知預測期間，禁止升級")
    else:
        if int(model.get("session_count") or 0) < MIN_TRAINING_SESSIONS[horizon_code]:
            reasons.append("完成交易日尚未達此期間的升級門檻")
        if int(model.get("sample_count") or 0) < MIN_TRAINING_SAMPLES[horizon_code]:
            reasons.append("完成樣本尚未達此期間的升級門檻")
    if challenger_hit < MIN_HOLDOUT_DIRECTION_HIT_PCT:
        reasons.append("挑戰模型樣本外方向命中率未達52%")
    if challenger_hit < champion_hit + MIN_DIRECTION_IMPROVEMENT_PCT:
        reasons.append("挑戰模型未比穩定模型高出至少2個百分點")
    if champion_mae <= 0 or challenger_mae > champion_mae * MAX_MAE_RATIO:
        reasons.append("挑戰模型樣本外誤差未改善至少2%")
    if any(not math.isfinite(float(value)) or abs(float(value)) > 50 for value in weights.values()):
        reasons.append("模型係數超出安全範圍")
    return not reasons, reasons


def fit_challenger(
    rows: list[dict[str, Any]],
    *,
    market: str,
    asset_group: str,
    horizon_code: str,
    created_at: str,
    benchmark_weights: dict[str, float] | None = None,
    benchmark_model_version: str = MODEL_VERSION,
) -> dict[str, Any] | None:
    sessions = {row["session_date"] for row in rows}
    if len(rows) < MIN_TRAINING_SAMPLES[horizon_code] or len(sessions) < MIN_TRAINING_SESSIONS[horizon_code]:
        return None
    ordered_sessions = sorted(sessions)
    validation_count = max(4, len(ordered_sessions) // 5)
    validation_sessions = set(ordered_sessions[-validation_count:])
    training_rows = [row for row in rows if row["session_date"] not in validation_sessions]
    validation_rows = [row for row in rows if row["session_date"] in validation_sessions]
    if len(training_rows) < 100 or not validation_rows:
        return None

    def matrix(source: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
        features = np.array([
            [float(row["features"].get(name, .5)) - .5 for name in FEATURE_NAMES[:-1]]
            for row in source
        ], dtype=float)
        return np.column_stack([np.ones(len(features)), features]), np.array(
            [float(row["realized_return_pct"]) for row in source], dtype=float
        )

    x, y = matrix(training_rows)
    validation_x, validation_y = matrix(validation_rows)
    ridge = np.eye(x.shape[1]) * 8.0
    ridge[0, 0] = 0.0
    coefficients = np.linalg.solve(x.T @ x + ridge, x.T @ y)
    prediction = x @ coefficients
    validation_prediction = validation_x @ coefficients
    benchmark_prediction = np.array([
        (
            learned_forecast(
                row["features"], horizon_code,
                weights=benchmark_weights,
                chase_risk_points=0.0,
                data_quality_pct=float(row["features"].get("quality", .5)) * 100.0,
                trade_blocked=False,
            )
            if benchmark_weights
            else forecast(
                row["features"], horizon_code,
                chase_risk_points=0.0,
                data_quality_pct=float(row["features"].get("quality", .5)) * 100.0,
                trade_blocked=False,
            )
        )["expected_return_pct"]
        for row in validation_rows
    ], dtype=float)
    train_mae = float(np.mean(np.abs(prediction - y)))
    validation_mae = float(np.mean(np.abs(validation_prediction - validation_y)))
    validation_hit = float(np.mean((validation_prediction >= 0) == (validation_y >= 0))) * 100.0
    benchmark_mae = float(np.mean(np.abs(benchmark_prediction - validation_y)))
    benchmark_hit = float(np.mean((benchmark_prediction >= 0) == (validation_y >= 0))) * 100.0
    feature_weights = {
        name: round(float(value), 6)
        for name, value in zip(FEATURE_NAMES[:-1], coefficients[1:])
    }
    feature_weights["_intercept"] = round(float(coefficients[0]), 6)
    digest = hashlib.sha256(
        f"{market}:{asset_group}:{horizon_code}:{max(sessions)}:{len(rows)}".encode()
    ).hexdigest()[:12]
    return {
        "model_version": f"WUDE-CHALLENGER-{digest}",
        "market": market,
        "asset_group": asset_group,
        "horizon_code": horizon_code,
        "role": "challenger",
        "status": "candidate_auto_shadow_competition",
        "trained_through": max(sessions),
        "sample_count": len(rows),
        "session_count": len(sessions),
        "weights": feature_weights,
        "metrics": {
            "training_mae_pct": round(train_mae, 4),
            "walk_forward_holdout_mae_pct": round(validation_mae, 4),
            "walk_forward_holdout_direction_hit_pct": round(validation_hit, 2),
            "champion_holdout_mae_pct": round(benchmark_mae, 4),
            "champion_holdout_direction_hit_pct": round(benchmark_hit, 2),
            "benchmark_holdout_mae_pct": round(benchmark_mae, 4),
            "benchmark_holdout_direction_hit_pct": round(benchmark_hit, 2),
            "benchmark_model_version": benchmark_model_version,
            "holdout_session_count": validation_count,
            "holdout_sample_count": len(validation_rows),
            "promotion_metric": "walk_forward_holdout",
        },
        "created_at": created_at,
    }
