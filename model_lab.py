"""Auditable scorecards for the next completed exchange session.

Ranking and the 1--5 day outlook answer different questions.  This module uses
only short-horizon evidence, predicts overnight/session/full-day separately,
and abstains when the evidence is weak or contradictory.
"""
from __future__ import annotations
from typing import Any

TRACK_NAMES = ("overnight", "session", "full_day")
MODEL_NAMES = (
    "balanced_next", "price_trend", "volume_confirmation", "market_flow",
    "macro_risk", "exhaustion_guard", "mean_reversion",
    "momentum_confirmed", "relative_strength", "entry_timing",
)

def _number(value: Any, default: float = 50.0) -> float:
    try: result = float(value)
    except (TypeError, ValueError): return default
    return max(0.0, min(100.0, result))

def _float(value: Any, default: float = 0.0) -> float:
    try: return float(value)
    except (TypeError, ValueError): return default

def _blend(parts: list[tuple[float, float]]) -> float:
    total = sum(weight for _, weight in parts)
    return sum(value * weight for value, weight in parts) / total if total else 50.0

def _inputs(row: dict[str, Any]) -> dict[str, float]:
    technical = _number(row.get("technical_score"))
    volume = _number(row.get("volume_score"))
    flow = _number(row.get("market_flow_score"), _number(row.get("institution_score")))
    positioning = _number(row.get("positioning_score"))
    group = _number(row.get("group_score"))
    entry = _number(row.get("entry_score"))
    macro = _number(row.get("macro_score"))
    rsi = _number(row.get("rsi"))
    change = _float(row.get("change_pct"))
    relative_volume = _float(row.get("daily_volume_ratio"), _float(row.get("relative_volume"), 1.0))
    attack = max(0.0, min(100.0, 50.0 + _float(row.get("attack_volume")) * .8))
    extended = max(0.0, min(100.0, 50.0 + _float(row.get("extended_change")) * 6.0))
    news = max(0.0, 100.0 - _float(row.get("news_penalty")) * 7.0)
    exhaustion = 75.0
    if rsi >= 78: exhaustion -= 45.0
    elif rsi >= 72: exhaustion -= 28.0
    elif rsi <= 25: exhaustion -= 25.0
    if change >= 5: exhaustion -= 32.0
    elif change >= 3: exhaustion -= 18.0
    if row.get("breakout20") and relative_volume < 1.15: exhaustion -= 28.0
    if row.get("breakdown20"): exhaustion -= 30.0
    reversion = 68.0 if 38 <= rsi <= 55 else 55.0 if 32 <= rsi < 38 else 42.0
    momentum = technical + (10.0 if row.get("breakout20") else 0.0)
    momentum += max(-12.0, min(8.0, change * 1.5))
    if relative_volume < .8: momentum -= 10.0
    return {
        "technical": technical, "volume": volume, "flow": flow,
        "positioning": positioning, "group": group, "entry": entry,
        "macro": macro, "attack": attack, "extended": extended, "news": news,
        "exhaustion": max(0.0, min(100.0, exhaustion)), "reversion": reversion,
        "momentum": max(0.0, min(100.0, momentum)),
    }

def _track_scores(row: dict[str, Any], track: str) -> dict[str, float]:
    if track not in TRACK_NAMES: raise ValueError(f"unknown return track: {track}")
    x = _inputs(row)
    overnight = {
        "balanced_next": _blend([(x["macro"],28),(x["flow"],20),(x["news"],17),(x["extended"],15),(x["exhaustion"],20)]),
        "price_trend": _blend([(x["technical"],25),(x["macro"],30),(x["extended"],20),(x["exhaustion"],25)]),
        "volume_confirmation": _blend([(x["volume"],20),(x["flow"],20),(x["macro"],30),(x["exhaustion"],30)]),
        "market_flow": _blend([(x["flow"],40),(x["positioning"],20),(x["macro"],30),(x["news"],10)]),
        "macro_risk": _blend([(x["macro"],60),(x["news"],25),(x["extended"],15)]),
        "exhaustion_guard": _blend([(x["exhaustion"],55),(x["macro"],25),(x["flow"],20)]),
        "mean_reversion": _blend([(x["reversion"],35),(x["macro"],30),(x["exhaustion"],25),(x["flow"],10)]),
        "momentum_confirmed": _blend([(x["momentum"],25),(x["macro"],30),(x["flow"],20),(x["exhaustion"],25)]),
        "relative_strength": _blend([(x["group"],25),(x["flow"],20),(x["macro"],35),(x["exhaustion"],20)]),
        "entry_timing": _blend([(x["entry"],20),(x["macro"],30),(x["extended"],20),(x["exhaustion"],30)]),
    }
    session = {
        "balanced_next": _blend([(x["technical"],22),(x["volume"],18),(x["flow"],18),(x["macro"],17),(x["exhaustion"],25)]),
        "price_trend": _blend([(x["technical"],40),(x["momentum"],20),(x["group"],15),(x["exhaustion"],25)]),
        "volume_confirmation": _blend([(x["volume"],35),(x["attack"],25),(x["momentum"],15),(x["flow"],15),(x["exhaustion"],10)]),
        "market_flow": _blend([(x["flow"],35),(x["positioning"],25),(x["volume"],15),(x["macro"],15),(x["exhaustion"],10)]),
        "macro_risk": _blend([(x["macro"],45),(x["group"],25),(x["flow"],20),(x["news"],10)]),
        "exhaustion_guard": _blend([(x["exhaustion"],50),(x["technical"],15),(x["volume"],15),(x["macro"],20)]),
        "mean_reversion": _blend([(x["reversion"],35),(x["entry"],25),(x["volume"],10),(x["macro"],15),(x["exhaustion"],15)]),
        "momentum_confirmed": _blend([(x["momentum"],35),(x["volume"],20),(x["attack"],15),(x["flow"],15),(x["exhaustion"],15)]),
        "relative_strength": _blend([(x["group"],30),(x["technical"],20),(x["flow"],20),(x["macro"],15),(x["exhaustion"],15)]),
        "entry_timing": _blend([(x["entry"],35),(x["volume"],15),(x["technical"],15),(x["flow"],15),(x["exhaustion"],20)]),
    }
    scores = ({name: overnight[name]*.45 + session[name]*.55 for name in MODEL_NAMES}
              if track == "full_day" else overnight if track == "overnight" else session)
    hard_block = row.get("trade_guard_blocked") or row.get("market_contract_valid") is False
    weak_market = x["macro"] <= 38
    heavy_selling = x["flow"] <= 32 and x["positioning"] <= 40
    overextended = x["exhaustion"] <= 35
    for name, score in list(scores.items()):
        if hard_block: score = min(score, 45.0)
        if weak_market:
            score = min(score, 60.0)
            if x["macro"] <= 28: score -= 10.0
        if heavy_selling: score = min(score, 58.0)
        if overextended: score = min(score, 62.0)
        if row.get("breakdown20"): score -= 8.0
        scores[name] = round(max(0.0, min(100.0, score)), 1)
    return scores

def model_predictions(row: dict[str, Any], track: str = "full_day") -> dict[str, dict[str, Any]]:
    output = {}
    quality = _number(row.get("market_data_quality_score"), 0.0)
    for name, score in _track_scores(row, track).items():
        direction = "ABSTAIN" if quality < 65 else "UP" if score >= 70 else "DOWN" if score <= 30 else "ABSTAIN"
        output[name] = {"direction": direction, "score": score, "confidence": round(50 + abs(score-50), 1)}
    return output

def track_predictions(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for track in TRACK_NAMES:
        votes = model_predictions(row, track)
        result[track] = {"models": votes, "consensus": consensus_prediction(votes)}
    return result

def consensus_prediction(votes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    up = [v for v in votes.values() if v.get("direction") == "UP"]
    down = [v for v in votes.values() if v.get("direction") == "DOWN"]
    direction, winning = "ABSTAIN", []
    if len(up) >= 7 and len(up) >= len(down)+3: direction, winning = "UP", up
    elif len(down) >= 7 and len(down) >= len(up)+3: direction, winning = "DOWN", down
    confidence = round(sum(float(v["confidence"]) for v in winning)/len(winning), 1) if winning else 0.0
    return {"direction": direction, "confidence": confidence, "up_votes": len(up),
            "down_votes": len(down), "abstain_votes": len(votes)-len(up)-len(down)}

def evaluate_direction(direction: str, return_pct: float) -> bool | None:
    if direction == "UP": return return_pct > 0
    if direction == "DOWN": return return_pct < 0
    return None
