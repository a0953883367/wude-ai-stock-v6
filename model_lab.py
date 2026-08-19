"""Transparent candidate models for auditable next-session direction tests.

These are deliberately small, deterministic scorecards rather than ten opaque
models trained on the same tiny sample.  They run in shadow mode until enough
strictly forward outcomes exist to compare them fairly.
"""

from __future__ import annotations

from typing import Any


MODEL_NAMES = (
    "balanced",
    "trend",
    "volume_breakout",
    "market_flow",
    "quality_growth",
    "risk_adjusted",
    "mean_reversion",
    "momentum",
    "relative_strength",
    "entry_timing",
)


def _number(value: Any, default: float = 50.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, result))


def _weighted(row: dict[str, Any], weights: dict[str, float]) -> float:
    values: list[tuple[float, float]] = []
    for key, weight in weights.items():
        if row.get(key) is None:
            continue
        values.append((_number(row.get(key)), weight))
    if not values:
        return 50.0
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values)


def _rsi_reversion(row: dict[str, Any]) -> float:
    rsi = _number(row.get("rsi"), 50.0)
    # A controlled pullback near neutral is preferred; oversold alone is not
    # treated as bullish because falling knives would otherwise dominate.
    if 42 <= rsi <= 58:
        return 75.0
    if 35 <= rsi < 42 or 58 < rsi <= 65:
        return 62.0
    if rsi < 25 or rsi > 78:
        return 25.0
    return 48.0


def _momentum_score(row: dict[str, Any]) -> float:
    score = _number(row.get("technical_score"))
    if row.get("breakout20"):
        score += 15
    if row.get("breakdown20"):
        score -= 20
    change = float(row.get("change_pct") or 0.0)
    score += max(-10.0, min(10.0, change * 2.0))
    return max(0.0, min(100.0, score))


def _attack_score(row: dict[str, Any]) -> float:
    attack = float(row.get("attack_volume") or 0.0)
    return max(0.0, min(100.0, 50.0 + attack * 0.8))


def _candidate_scores(row: dict[str, Any]) -> dict[str, float]:
    technical = _number(row.get("technical_score"))
    volume = _number(row.get("volume_score"))
    flow = _number(row.get("market_flow_score"), _number(row.get("institution_score")))
    positioning = _number(row.get("positioning_score"))
    quality = _number(row.get("financial_quality_score"))
    growth = _number(row.get("growth_score"))
    valuation = _number(row.get("valuation_score"))
    group = _number(row.get("group_score"))
    entry = _number(row.get("entry_score"))
    short = _number(row.get("short_term_score"))
    overall = _number(row.get("score"))
    confidence = _number(row.get("overall_confidence"))
    macro = _number(row.get("macro_score"))
    attack = _attack_score(row)
    reversion = _rsi_reversion(row)
    momentum = _momentum_score(row)
    news = max(0.0, 100.0 - float(row.get("news_penalty") or 0.0) * 6.0)

    scores = {
        "balanced": _weighted(row, {
            "technical_score": 20, "volume_score": 15,
            "market_flow_score": 15, "entry_score": 20,
            "financial_quality_score": 15, "score": 15,
        }),
        "trend": technical * .40 + momentum * .30 + volume * .15 + group * .15,
        "volume_breakout": volume * .35 + momentum * .25 + attack * .25 + technical * .15,
        "market_flow": flow * .35 + positioning * .25 + volume * .20 + technical * .20,
        "quality_growth": quality * .30 + growth * .20 + valuation * .15 + technical * .20 + entry * .15,
        "risk_adjusted": overall * .20 + entry * .20 + short * .20 + technical * .15 + confidence * .15 + news * .10,
        "mean_reversion": reversion * .35 + entry * .25 + technical * .15 + volume * .10 + quality * .15,
        "momentum": momentum * .40 + technical * .20 + volume * .20 + attack * .10 + group * .10,
        "relative_strength": group * .25 + flow * .20 + technical * .20 + volume * .15 + macro * .10 + overall * .10,
        "entry_timing": entry * .35 + short * .25 + technical * .15 + volume * .15 + positioning * .10,
    }
    if row.get("trade_guard_blocked") or row.get("market_contract_valid") is False:
        scores = {name: min(score, 44.0) for name, score in scores.items()}
    if row.get("breakdown20"):
        scores = {name: score - 8.0 for name, score in scores.items()}
    return {name: round(max(0.0, min(100.0, score)), 1) for name, score in scores.items()}


def model_predictions(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return high-confidence UP/DOWN votes; uncertain models abstain."""
    output: dict[str, dict[str, Any]] = {}
    data_quality = _number(row.get("market_data_quality_score"), 0.0)
    for name, score in _candidate_scores(row).items():
        if data_quality < 50:
            direction = "ABSTAIN"
        elif score >= 68:
            direction = "UP"
        elif score <= 32:
            direction = "DOWN"
        else:
            direction = "ABSTAIN"
        confidence = round(50.0 + abs(score - 50.0), 1)
        output[name] = {"direction": direction, "score": score, "confidence": confidence}
    return output


def consensus_prediction(votes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    up = [v for v in votes.values() if v.get("direction") == "UP"]
    down = [v for v in votes.values() if v.get("direction") == "DOWN"]
    direction = "ABSTAIN"
    winning: list[dict[str, Any]] = []
    # Require a strong majority of all ten candidates, not merely a majority
    # among models that happened to vote.
    if len(up) >= 7 and len(up) >= len(down) + 3:
        direction, winning = "UP", up
    elif len(down) >= 7 and len(down) >= len(up) + 3:
        direction, winning = "DOWN", down
    confidence = round(sum(float(v["confidence"]) for v in winning) / len(winning), 1) if winning else 0.0
    return {
        "direction": direction,
        "confidence": confidence,
        "up_votes": len(up),
        "down_votes": len(down),
        "abstain_votes": len(votes) - len(up) - len(down),
    }


def evaluate_direction(direction: str, return_pct: float) -> bool | None:
    if direction == "UP":
        return return_pct > 0
    if direction == "DOWN":
        return return_pct < 0
    return None
