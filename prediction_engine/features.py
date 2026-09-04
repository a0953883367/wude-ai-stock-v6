"""Point-in-time feature extraction with explicit leakage boundaries."""

from __future__ import annotations

import copy
from typing import Any


FEATURE_NAMES = (
    "trend", "volume", "capital_flow", "positioning", "sector",
    "market_regime", "fundamental", "valuation", "news", "entry",
    "shadow_consensus", "industry_lifecycle", "quality",
)

FORBIDDEN_INPUT_FRAGMENTS = (
    "actual_", "outcome", "future", "target_session", "realized_",
    "profit_loss", "prediction_correct", "day5_return", "day45_return",
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) != float("inf") else default


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, finite(value, low)))


def score01(value: Any, default: float = 50.0) -> float:
    return round(clamp(value, 0.0, 100.0) / 100.0, 6) if value is not None else default / 100.0


def group_name(row: dict[str, Any]) -> str:
    market = str(row.get("market") or "").upper()
    asset = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
    return f"{market}_{asset}"


def session_date(row: dict[str, Any]) -> str:
    return str(
        row.get("official_session_date")
        or row.get("next_session_source_session_date")
        or row.get("cached_at")
        or ""
    )[:10]


def _clean_technical(row: dict[str, Any]) -> tuple[float, float]:
    change = finite(row.get("change_pct"))
    direct = max(-20.0, min(20.0, change * 4.0))
    technical = clamp(finite(row.get("technical_score"), 50.0) - direct)
    return technical, round(direct, 2)


def _capital_flow_score(flow: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not flow:
        return 50.0, ["大量買賣／資金流未取得，維持中性且降低品質"]
    ratio = finite(flow.get("net_directional_ratio_pct"))
    persistence = clamp(flow.get("persistence_pct"), 0.0, 100.0)
    confidence = clamp(flow.get("confidence"), 0.0, 100.0)
    directional = 50.0 + max(-35.0, min(35.0, ratio * 0.55))
    score = 50.0 + (directional - 50.0) * persistence / 100.0 * confidence / 100.0
    return clamp(score), [
        f"大量資金方向 {ratio:+.1f}%・持續 {persistence:.0f}%・可信 {confidence:.0f}%"
    ]


def _shadow_score(row: dict[str, Any], shadow: dict[str, Any] | None) -> tuple[float, list[str]]:
    values: list[float] = []
    reasons: list[str] = []
    direction = str(row.get("next_session_direction") or "")
    confidence = clamp(row.get("next_session_confidence"), 0.0, 100.0)
    if direction:
        values.append(50.0 + (confidence - 50.0) * (1 if "看漲" in direction else -1 if "看跌" in direction else 0))
        reasons.append(f"既有隔日影子 {direction}・信心 {confidence:.0f}%")
    for item in (shadow or {}).get("evidence") or []:
        if not isinstance(item, dict) or not item.get("time_aligned", True):
            continue
        strength = clamp(item.get("strength"), 0.0, 100.0)
        confidence = clamp(item.get("confidence"), 0.0, 100.0)
        direction = str(item.get("direction") or "")
        signed = (strength - 50.0) * confidence / 100.0
        if direction == "oppose":
            signed = -abs(signed)
        elif direction != "support":
            continue
        values.append(50.0 + signed)
        reasons.append(str(item.get("reason") or item.get("source_id") or "影子證據"))
    return (sum(values) / len(values) if values else 50.0), reasons[:4]


def chase_risk(row: dict[str, Any]) -> tuple[float, list[str]]:
    """Current return never predicts direction; it only affects buyability."""
    change = finite(row.get("change_pct"))
    rsi = finite(row.get("rsi"), 50.0)
    distance = finite(row.get("ma20_distance_pct"))
    penalty = 0.0
    reasons: list[str] = []
    if abs(change) >= 8:
        penalty += 8
        reasons.append("當日漲跌達8%，隔日執行風險較高")
    elif abs(change) >= 5:
        penalty += 5
        reasons.append("當日漲跌達5%，降低追價／接刀可買性")
    elif abs(change) >= 3:
        penalty += 2
        reasons.append("當日漲跌達3%，保留執行折減")
    if rsi >= 78:
        penalty += 6
        reasons.append("RSI過熱")
    elif rsi >= 72:
        penalty += 3
        reasons.append("RSI偏熱")
    if distance >= 12:
        penalty += 5
        reasons.append("偏離20日線12%以上")
    elif distance >= 8:
        penalty += 2
        reasons.append("偏離20日線8%以上")
    return min(15.0, penalty), reasons


def extract_features(
    row: dict[str, Any],
    *,
    capital_flow: dict[str, Any] | None = None,
    shadow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact point-in-time vector; future fields are unreachable."""
    safe = {
        key: copy.deepcopy(value)
        for key, value in row.items()
        if not any(fragment in key.lower() for fragment in FORBIDDEN_INPUT_FRAGMENTS)
    }
    technical, removed = _clean_technical(safe)
    flow_score, flow_reasons = _capital_flow_score(capital_flow)
    shadow_score, shadow_reasons = _shadow_score(safe, shadow)
    news_score = 100.0 - clamp(finite(safe.get("news_penalty")) * 7.0)
    fundamental = safe.get("financial_quality_score")
    if fundamental is None:
        fundamental = safe.get("fundamental_score")
    market_context = (
        safe.get("tw_market_context_score")
        if safe.get("market") == "TW" and safe.get("tw_market_context_available")
        else safe.get("macro_score")
    )
    sector = (
        safe.get("tw_sector_context_score")
        if safe.get("market") == "TW" and safe.get("tw_sector_context_available")
        else safe.get("group_score")
    )
    features = {
        "trend": score01(technical),
        "volume": score01(safe.get("volume_score")),
        "capital_flow": score01(flow_score),
        "positioning": score01(safe.get("positioning_score", safe.get("institution_score"))),
        "sector": score01(sector),
        "market_regime": score01(market_context),
        "fundamental": score01(fundamental),
        "valuation": score01(safe.get("valuation_score")),
        "news": score01(news_score),
        "entry": score01(safe.get("entry_score")),
        "shadow_consensus": score01(shadow_score),
        "quality": score01(safe.get("market_data_quality_score"), 0.0),
    }
    chase, chase_reasons = chase_risk(safe)
    available = sum(1 for value in features.values() if value not in (0.0, 0.5))
    quality = clamp(safe.get("market_data_quality_score"), 0.0, 100.0)
    if not safe.get("news_data_available"):
        quality = max(0.0, quality - 5.0)
    if not capital_flow:
        quality = max(0.0, quality - 5.0)
    evidence = {
        "same_session_change_pct": finite(safe.get("change_pct")),
        "same_session_change_direct_points_removed": removed,
        "same_session_change_prediction_bonus": 0,
        "same_session_change_prediction_penalty": 0,
        "chase_risk_points": chase,
        "chase_reasons": chase_reasons,
        "capital_flow": flow_reasons,
        "news": str(safe.get("news_summary") or "新聞未提供"),
        "shadow": shadow_reasons,
        "available_feature_count": available,
        "future_fields_forbidden": True,
    }
    return {"features": features, "evidence": evidence, "data_quality_pct": round(quality, 1)}
