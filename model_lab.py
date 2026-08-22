"""Auditable market-specific scorecards for the next exchange session.

Ranking and the 1--5 day outlook answer different questions.  This module uses
only short-horizon evidence, predicts overnight/session/full-day separately,
and abstains when the evidence is weak or contradictory.  Taiwan and US
markets intentionally use different evidence weights; missing evidence keeps
its weight neutral and lowers coverage instead of being reassigned elsewhere.
"""
from __future__ import annotations
from typing import Any

TRACK_NAMES = ("overnight", "session", "full_day")
MODEL_NAMES = (
    "balanced_next", "price_trend", "volume_confirmation", "market_flow",
    "macro_risk", "exhaustion_guard", "mean_reversion",
    "momentum_confirmed", "relative_strength", "entry_timing",
)

# These are research-forecast thresholds, not trading thresholds and not a
# claimed realised win rate.  Requiring every model to reach 75 previously
# made the production universe abstain almost completely, which prevented any
# forward validation.  Strong signals remain a stricter, separately labelled
# subset of the research forecasts.
MIN_EVIDENCE_COVERAGE = 75.0
MODEL_DIRECTION_THRESHOLD = 60.0
MIN_RESEARCH_VOTES = 6
MIN_STRONG_VOTES = 7
MIN_RESEARCH_STRENGTH = 60.0
MIN_STRONG_STRENGTH = 75.0

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

def _inputs(row: dict[str, Any]) -> dict[str, Any]:
    market = str(row.get("market") or "TW").upper()
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
    extended_change = _float(
        row.get("extended_change_pct"), _float(row.get("extended_change"))
    )
    # Taiwan has no comparable public pre/after-market session.  Keeping it at
    # neutral prevents US extended-hours data from leaking into the TW model.
    extended = (
        max(0.0, min(100.0, 50.0 + extended_change * 6.0))
        if market == "US" and row.get("extended_hours_available")
        else 50.0
    )
    news = max(0.0, 100.0 - _float(row.get("news_penalty")) * 7.0)
    kline = _number(row.get("kline_score"))
    pattern = str(row.get("kline_pattern") or "")
    exhaustion = 75.0
    if rsi >= 78: exhaustion -= 45.0
    elif rsi >= 72: exhaustion -= 28.0
    elif rsi <= 25: exhaustion -= 25.0
    if market == "TW" and change >= 9: exhaustion -= 48.0
    elif market == "US" and change >= 8: exhaustion -= 40.0
    elif change >= 5: exhaustion -= 32.0
    elif change >= 3: exhaustion -= 18.0
    # A large-volume upper wick is often distribution, not confirmation.  It
    # must never receive the same treatment as a strong close on heavy volume.
    if "上影壓力" in pattern:
        exhaustion -= 24.0 if relative_volume >= 1.5 else 10.0
    if relative_volume >= 3.0 and change >= 3.0:
        exhaustion -= 12.0
    if row.get("breakout20") and relative_volume < 1.15: exhaustion -= 28.0
    if row.get("breakdown20"): exhaustion -= 30.0
    reversion = 68.0 if 38 <= rsi <= 55 else 55.0 if 32 <= rsi < 38 else 42.0
    momentum = technical + (10.0 if row.get("breakout20") else 0.0)
    momentum += max(-12.0, min(8.0, change * 1.5))
    if relative_volume < .8: momentum -= 10.0
    return {
        "market": market, "technical": technical, "volume": volume, "flow": flow,
        "positioning": positioning, "group": group, "entry": entry,
        "macro": macro, "attack": attack, "extended": extended, "news": news,
        "kline": kline,
        "exhaustion": max(0.0, min(100.0, exhaustion)), "reversion": reversion,
        "momentum": max(0.0, min(100.0, momentum)),
    }

def _track_scores(row: dict[str, Any], track: str) -> dict[str, float]:
    if track not in TRACK_NAMES: raise ValueError(f"unknown return track: {track}")
    x = _inputs(row)
    if x["market"] == "US":
        # US overnight moves are driven more by index/sector regime, verified
        # news and pre/after-market action. FINRA/SIP/OPRA are represented in
        # flow, while company fundamentals remain a risk filter, not a vote.
        overnight = {
            "balanced_next": _blend([(x["macro"],25),(x["extended"],20),(x["flow"],20),(x["news"],20),(x["exhaustion"],15)]),
            "price_trend": _blend([(x["technical"],25),(x["extended"],20),(x["macro"],20),(x["group"],15),(x["exhaustion"],20)]),
            "volume_confirmation": _blend([(x["volume"],20),(x["flow"],20),(x["extended"],20),(x["macro"],20),(x["exhaustion"],20)]),
            "market_flow": _blend([(x["flow"],35),(x["macro"],20),(x["extended"],15),(x["news"],15),(x["exhaustion"],15)]),
            "macro_risk": _blend([(x["macro"],45),(x["news"],25),(x["extended"],15),(x["exhaustion"],15)]),
            "exhaustion_guard": _blend([(x["exhaustion"],45),(x["macro"],20),(x["extended"],15),(x["flow"],20)]),
            "mean_reversion": _blend([(x["reversion"],30),(x["entry"],20),(x["extended"],15),(x["macro"],20),(x["exhaustion"],15)]),
            "momentum_confirmed": _blend([(x["momentum"],25),(x["flow"],20),(x["extended"],20),(x["macro"],20),(x["exhaustion"],15)]),
            "relative_strength": _blend([(x["group"],30),(x["macro"],25),(x["flow"],20),(x["extended"],10),(x["exhaustion"],15)]),
            "entry_timing": _blend([(x["entry"],25),(x["extended"],20),(x["macro"],20),(x["flow"],15),(x["exhaustion"],20)]),
        }
        session = {
            "balanced_next": _blend([(x["technical"],25),(x["volume"],20),(x["flow"],20),(x["macro"],15),(x["group"],10),(x["exhaustion"],10)]),
            "price_trend": _blend([(x["technical"],35),(x["momentum"],20),(x["group"],15),(x["flow"],15),(x["exhaustion"],15)]),
            "volume_confirmation": _blend([(x["volume"],30),(x["attack"],20),(x["momentum"],15),(x["flow"],20),(x["exhaustion"],15)]),
            "market_flow": _blend([(x["flow"],35),(x["positioning"],20),(x["volume"],15),(x["macro"],20),(x["exhaustion"],10)]),
            "macro_risk": _blend([(x["macro"],40),(x["group"],25),(x["flow"],20),(x["news"],15)]),
            "exhaustion_guard": _blend([(x["exhaustion"],45),(x["technical"],20),(x["volume"],15),(x["macro"],20)]),
            "mean_reversion": _blend([(x["reversion"],35),(x["entry"],25),(x["volume"],10),(x["macro"],15),(x["exhaustion"],15)]),
            "momentum_confirmed": _blend([(x["momentum"],30),(x["volume"],20),(x["attack"],15),(x["flow"],20),(x["exhaustion"],15)]),
            "relative_strength": _blend([(x["group"],30),(x["technical"],20),(x["flow"],20),(x["macro"],20),(x["exhaustion"],10)]),
            "entry_timing": _blend([(x["entry"],30),(x["volume"],15),(x["technical"],15),(x["flow"],20),(x["exhaustion"],20)]),
        }
        overnight_share = .45
    else:
        # Taiwan uses completed close/volume plus local institutional, credit
        # and broker evidence. Fundamentals do not vote on tomorrow's return.
        overnight = {
            "balanced_next": _blend([(x["technical"],20),(x["kline"],10),(x["flow"],25),(x["macro"],20),(x["news"],10),(x["exhaustion"],15)]),
            "price_trend": _blend([(x["technical"],35),(x["kline"],15),(x["macro"],20),(x["flow"],15),(x["exhaustion"],15)]),
            "volume_confirmation": _blend([(x["volume"],25),(x["kline"],15),(x["flow"],25),(x["macro"],15),(x["exhaustion"],20)]),
            "market_flow": _blend([(x["flow"],45),(x["positioning"],15),(x["macro"],20),(x["news"],10),(x["exhaustion"],10)]),
            "macro_risk": _blend([(x["macro"],50),(x["news"],25),(x["flow"],15),(x["exhaustion"],10)]),
            "exhaustion_guard": _blend([(x["exhaustion"],50),(x["kline"],15),(x["flow"],20),(x["macro"],15)]),
            "mean_reversion": _blend([(x["reversion"],35),(x["entry"],20),(x["flow"],15),(x["macro"],15),(x["exhaustion"],15)]),
            "momentum_confirmed": _blend([(x["momentum"],30),(x["volume"],20),(x["flow"],20),(x["macro"],10),(x["exhaustion"],20)]),
            "relative_strength": _blend([(x["group"],30),(x["technical"],20),(x["flow"],20),(x["macro"],15),(x["exhaustion"],15)]),
            "entry_timing": _blend([(x["entry"],30),(x["technical"],15),(x["flow"],20),(x["macro"],15),(x["exhaustion"],20)]),
        }
        session = {
            "balanced_next": _blend([(x["technical"],25),(x["volume"],20),(x["flow"],20),(x["group"],15),(x["exhaustion"],20)]),
            "price_trend": _blend([(x["technical"],35),(x["momentum"],20),(x["kline"],15),(x["group"],10),(x["exhaustion"],20)]),
            "volume_confirmation": _blend([(x["volume"],30),(x["attack"],15),(x["momentum"],15),(x["flow"],20),(x["exhaustion"],20)]),
            "market_flow": _blend([(x["flow"],40),(x["positioning"],20),(x["volume"],15),(x["macro"],10),(x["exhaustion"],15)]),
            "macro_risk": _blend([(x["macro"],35),(x["group"],25),(x["flow"],25),(x["news"],15)]),
            "exhaustion_guard": _blend([(x["exhaustion"],45),(x["technical"],15),(x["kline"],15),(x["volume"],10),(x["macro"],15)]),
            "mean_reversion": _blend([(x["reversion"],35),(x["entry"],25),(x["volume"],10),(x["macro"],15),(x["exhaustion"],15)]),
            "momentum_confirmed": _blend([(x["momentum"],30),(x["volume"],20),(x["attack"],15),(x["flow"],20),(x["exhaustion"],15)]),
            "relative_strength": _blend([(x["group"],30),(x["technical"],20),(x["flow"],20),(x["macro"],15),(x["exhaustion"],15)]),
            "entry_timing": _blend([(x["entry"],30),(x["volume"],15),(x["technical"],15),(x["flow"],20),(x["exhaustion"],20)]),
        }
        overnight_share = .35
    scores = ({name: overnight[name]*overnight_share + session[name]*(1-overnight_share) for name in MODEL_NAMES}
              if track == "full_day" else overnight if track == "overnight" else session)
    hard_block = row.get("trade_guard_blocked") or row.get("market_contract_valid") is False
    weak_market = x["macro"] <= 38
    heavy_selling = x["flow"] <= 32 and x["positioning"] <= 40
    overextended = x["exhaustion"] <= 35
    distribution_spike = (
        "上影壓力" in str(row.get("kline_pattern") or "")
        and _float(row.get("daily_volume_ratio"), 1.0) >= 1.5
    )
    for name, score in list(scores.items()):
        if hard_block: score = min(score, 45.0)
        if weak_market:
            # A weak broad market must stay below the research-UP threshold.
            score = min(score, MODEL_DIRECTION_THRESHOLD - 2.0)
            if x["macro"] <= 28: score -= 10.0
        if heavy_selling: score = min(score, 58.0)
        if overextended: score = min(score, MODEL_DIRECTION_THRESHOLD - 2.0)
        if distribution_spike: score = min(score, MODEL_DIRECTION_THRESHOLD - 2.0)
        if row.get("breakdown20"): score -= 8.0
        scores[name] = round(max(0.0, min(100.0, score)), 1)
    return scores

def _evidence_coverage(row: dict[str, Any], track: str) -> float:
    """Market-aware coverage for tomorrow-only evidence (not fundamentals)."""
    market = str(row.get("market") or "TW").upper()
    technical = row.get("technical_score") is not None and row.get("price") is not None
    volume = row.get("volume_score") is not None and _float(row.get("avg_volume20")) > 0
    macro = row.get("macro_score") is not None
    news = bool(row.get("news_data_available"))
    if market == "US":
        flow = bool(
            row.get("us_live_data_available") or row.get("us_short_volume_available")
            or row.get("us_option_data_available") or row.get("intraday_available")
        )
        extended = bool(row.get("extended_hours_available"))
        checks = [(technical,20 if track == "overnight" else 30),
                  (volume,10 if track == "overnight" else 20),
                  (flow,20),(extended,20 if track == "overnight" else 5),
                  (macro,20 if track == "overnight" else 15),(news,10)]
    else:
        flow = bool(
            row.get("institution_available") or row.get("credit_available")
            or row.get("broker_available")
        )
        checks = [(technical,30),(volume,15),(flow,25),(macro,20),(news,10)]
    total = sum(weight for _, weight in checks)
    return round(sum(weight for ok, weight in checks if ok) / total * 100, 1)


def model_predictions(row: dict[str, Any], track: str = "full_day") -> dict[str, dict[str, Any]]:
    output = {}
    quality = min(
        _number(row.get("market_data_quality_score"), 0.0),
        _evidence_coverage(row, track),
    )
    for name, score in _track_scores(row, track).items():
        # The per-model direction is intentionally wider than a trade signal:
        # it creates an auditable research forecast.  Consensus strength and
        # verified forward outcomes determine whether it can later be promoted.
        direction = (
            "ABSTAIN" if quality < MIN_EVIDENCE_COVERAGE
            else "UP" if score >= MODEL_DIRECTION_THRESHOLD
            else "DOWN" if score <= 100.0 - MODEL_DIRECTION_THRESHOLD
            else "ABSTAIN"
        )
        output[name] = {
            "direction": direction,
            "score": score,
            "confidence": round(50 + abs(score-50), 1),
            "evidence_coverage_pct": quality,
        }
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
    if len(up) >= MIN_RESEARCH_VOTES and len(up) >= len(down)+3: direction, winning = "UP", up
    elif len(down) >= MIN_RESEARCH_VOTES and len(down) >= len(up)+3: direction, winning = "DOWN", down
    confidence = round(sum(float(v["confidence"]) for v in winning)/len(winning), 1) if winning else 0.0
    if confidence < MIN_RESEARCH_STRENGTH:
        direction, winning, confidence = "ABSTAIN", [], 0.0
    signal_level = (
        "STRONG"
        if direction != "ABSTAIN"
        and len(winning) >= MIN_STRONG_VOTES
        and confidence >= MIN_STRONG_STRENGTH
        else "RESEARCH" if direction != "ABSTAIN" else "ABSTAIN"
    )
    return {"direction": direction, "confidence": confidence, "up_votes": len(up),
            "down_votes": len(down), "abstain_votes": len(votes)-len(up)-len(down),
            "signal_level": signal_level}

def evaluate_direction(direction: str, return_pct: float) -> bool | None:
    if direction == "UP": return return_pct > 0
    if direction == "DOWN": return return_pct < 0
    return None
