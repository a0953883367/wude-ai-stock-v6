"""Isolated next-exchange-session rankings for stocks and ETFs.

The existing formal V6 ranking answers "what is strong now".  This module
answers two deliberately different forward-only questions:

* which symbols have the strongest next-session UP research forecast; and
* which of those forecasts are still reasonably buyable rather than chased.

It never mutates formal rows, formal rankings, the five-day ledgers, or the
60-session validation state.  Same-session return is removed from the legacy
technical composite before the ten shadow models vote.  Independent trend,
volume, flow and shadow evidence may still confirm a continuation forecast;
the realised same-session move is used only for buyability/execution risk and
never lowers the forward probability by itself.  Future outcome fields are
not part of the explicit input allow-list.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from model_lab import model_predictions


SCHEMA_VERSION = 1
MODEL_VERSION = "NEXT-SESSION-RANKING-V1-SHADOW"
MIN_DATA_QUALITY = 75.0
GROUPS = ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF")

# Every legacy shadow model appears exactly once in one evidence family.  This
# prevents ten correlated votes from being mistaken for ten independent facts.
MODEL_FAMILIES = {
    "trend_structure": ("price_trend", "relative_strength"),
    "volume_flow": ("volume_confirmation", "market_flow"),
    "market_risk": ("macro_risk", "exhaustion_guard"),
    "entry_reversion": ("mean_reversion", "entry_timing"),
    "balanced_momentum": ("balanced_next", "momentum_confirmed"),
}
FAMILY_WEIGHTS = {
    "trend_structure": 25.0,
    "volume_flow": 25.0,
    "market_risk": 20.0,
    "entry_reversion": 15.0,
    "balanced_momentum": 15.0,
}

# Only these fields may enter the forward score.  Realised returns, future
# prices, validation labels and simulation P/L are intentionally impossible to
# pass to model_predictions through this boundary.
FORECAST_INPUT_FIELDS = {
    "symbol", "name", "market", "type", "industry", "theme", "price",
    "ma5", "ma10", "ma20", "ma60", "rsi", "ma20_distance_pct",
    "technical_score", "volume_score", "market_flow_score",
    "institution_score", "positioning_score", "group_score", "entry_score",
    "tw_sector_context_score", "tw_sector_context_available",
    "tw_market_context_score", "tw_market_context_available", "macro_score",
    "avg_volume20", "daily_volume_ratio", "relative_volume", "volume_pace",
    "attack_volume", "extended_change_pct", "extended_change",
    "extended_hours_available", "news_penalty", "news_data_available",
    "kline_score", "kline_pattern", "breakout20", "breakdown20",
    "trade_guard_blocked", "trade_guard_reason", "market_contract_valid",
    "market_data_quality_score", "institution_available", "credit_available",
    "broker_available", "us_live_data_available", "us_short_volume_available",
    "us_option_data_available", "intraday_available", "buy_zone_low",
    "buy_zone_high", "short_term_entry_low", "short_term_entry_high",
    "official_session_date", "next_session_source_session_date",
    "next_session_generated_at", "next_session_data_mode",
}

SHADOW_LIMITS = {
    "valuation_shadow": 2.0,
    "inverse_etf_shadow": 3.0,
    "rotation_shadow": 2.0,
    "capital_flow_shadow": 3.0,
}


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    number = _finite(value, low)
    return round(max(low, min(high, float(number))), 2)


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _group(row: dict[str, Any]) -> str:
    market = str(row.get("market") or "").upper()
    asset = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
    return f"{market}_{asset}"


def _checkpoint_ready(row: dict[str, Any], period: str | None, intraday: bool) -> bool:
    if intraday:
        return False
    if period is None:
        return True
    market = str(row.get("market") or "").upper()
    return (market == "TW" and period == "evening") or (
        market == "US" and period == "morning"
    )


def _forecast_input(row: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """Return an allow-listed copy with direct same-day return removed."""
    clean = {
        key: copy.deepcopy(row[key])
        for key in FORECAST_INPUT_FIELDS
        if key in row
    }
    change = _finite(row.get("change_pct"), 0.0) or 0.0
    direct_change_points = _clamp(change * 4.0, -20.0, 20.0)
    technical = _finite(clean.get("technical_score"))
    if technical is not None:
        clean["technical_score"] = _clamp(technical - direct_change_points)
    # model_lab's momentum and exhaustion helpers also read change_pct.  Keep
    # it neutral here and account for the move once in _chase_penalty below.
    clean["change_pct"] = 0.0
    return clean, round(direct_change_points, 2)


def _family_scores(votes: dict[str, dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for family, model_names in MODEL_FAMILIES.items():
        scores = [
            _finite((votes.get(name) or {}).get("score"))
            for name in model_names
        ]
        available = [float(score) for score in scores if score is not None]
        result[family] = round(sum(available) / len(available), 2) if available else 50.0
    return result


def _family_blend(families: dict[str, float]) -> float:
    total = sum(FAMILY_WEIGHTS.values())
    return round(sum(families[key] * FAMILY_WEIGHTS[key] for key in FAMILY_WEIGHTS) / total, 2)


def _chase_penalty(row: dict[str, Any]) -> tuple[float, list[str]]:
    """Use today's move once, only as a bounded buyability adjustment."""
    change = _finite(row.get("change_pct"), 0.0) or 0.0
    penalty = 0.0
    reasons: list[str] = []
    if change >= 8:
        penalty += 8.0
        reasons.append("當日漲幅達8%以上，避免隔日追高")
    elif change >= 5:
        penalty += 5.0
        reasons.append("當日漲幅達5%以上，降低隔日可買性")
    elif change >= 3:
        penalty += 2.0
        reasons.append("當日漲幅達3%以上，保留追價折減")
    elif change <= -8:
        penalty += 7.0
        reasons.append("當日跌幅達8%以上，防止直接接落刀")
    elif change <= -5:
        penalty += 4.0
        reasons.append("當日跌幅達5%以上，等待止穩")
    elif change <= -3:
        penalty += 2.0
        reasons.append("當日跌幅達3%以上，加入弱勢折減")

    rsi = _finite(row.get("rsi"), 50.0) or 50.0
    distance = _finite(row.get("ma20_distance_pct"), 0.0) or 0.0
    if rsi >= 78:
        penalty += 6.0
        reasons.append("RSI過熱")
    elif rsi >= 72:
        penalty += 3.0
        reasons.append("RSI偏熱")
    if distance >= 12:
        penalty += 5.0
        reasons.append("偏離月線12%以上")
    elif distance >= 8:
        penalty += 2.0
        reasons.append("偏離月線8%以上")
    if "上影壓力" in str(row.get("kline_pattern") or ""):
        penalty += 3.0
        reasons.append("出現上影壓力")
    return round(min(15.0, penalty), 2), reasons


def _continuation_evidence(
    families: dict[str, float],
    shadow_points: float,
    *,
    ready: bool,
) -> tuple[bool, list[str]]:
    """Explain an UP continuation using evidence independent of today's return."""
    reasons: list[str] = []
    if families.get("trend_structure", 0.0) >= 58.0:
        reasons.append("趨勢結構支持")
    if families.get("volume_flow", 0.0) >= 58.0:
        reasons.append("量價／資金流支持")
    if families.get("balanced_momentum", 0.0) >= 58.0:
        reasons.append("綜合動能支持")
    if shadow_points > 0:
        reasons.append("同日影子證據支持")
    return bool(ready and len(reasons) >= 2), reasons


def _date_part(value: Any) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def _shadow_adjustments(
    decision: dict[str, Any] | None,
    session_date: str,
) -> tuple[float, list[dict[str, Any]]]:
    evidence_by_source = {
        str(item.get("source_id") or ""): item
        for item in (decision or {}).get("evidence") or []
        if isinstance(item, dict)
    }
    adjustments: list[dict[str, Any]] = []
    for source, limit in SHADOW_LIMITS.items():
        item = evidence_by_source.get(source) or {}
        direction = str(item.get("direction") or "missing")
        confidence = _clamp(item.get("confidence"))
        strength = _clamp(item.get("strength"))
        status = str(item.get("status") or "missing")
        aligned = bool(session_date and _date_part(item.get("as_of")) == session_date)
        validated = bool(
            status in {"shadow_only", "validated_shadow", "validated_closed_session"}
            or source == "rotation_shadow" and status == "collecting_only"
        )
        points = 0.0
        if aligned and validated and direction == "support":
            points = max(0.0, (strength - 50.0) / 50.0) * limit * confidence / 100.0
        elif aligned and validated and direction == "oppose":
            if source == "rotation_shadow":
                magnitude = max(0.0, (50.0 - strength) / 50.0)
            else:
                magnitude = strength / 100.0
            points = -magnitude * limit * confidence / 100.0
        points = round(max(-limit, min(limit, points)), 2)
        adjustments.append({
            "source_id": source,
            "direction": direction,
            "status": status,
            "as_of": item.get("as_of"),
            "time_aligned": aligned,
            "validated_for_forecast": validated,
            "limit_points": limit,
            "points": points,
            "reason": item.get("reason") or "目前沒有可對齊的影子證據",
        })
    return round(sum(item["points"] for item in adjustments), 2), adjustments


def _row_prediction(
    row: dict[str, Any],
    decision: dict[str, Any] | None,
    *,
    period: str | None,
    intraday: bool,
) -> dict[str, Any]:
    clean, removed_change_points = _forecast_input(row)
    source_date = str(
        row.get("official_session_date")
        or row.get("next_session_source_session_date")
        or ""
    )
    ready = _checkpoint_ready(row, period, intraday) and bool(source_date)
    votes = model_predictions(clean, "full_day") if ready else {}
    families = _family_scores(votes)
    base_score = _family_blend(families) if ready else 50.0
    chase_penalty, chase_reasons = _chase_penalty(row)
    shadow_points, shadow_evidence = _shadow_adjustments(decision, source_date)
    quality_values = [
        _finite(row.get("market_data_quality_score"), 0.0) or 0.0,
        *[
            _finite(item.get("evidence_coverage_pct"), 0.0) or 0.0
            for item in votes.values()
        ],
    ]
    quality = round(min(quality_values), 1) if quality_values else 0.0
    data_ready = bool(ready and quality >= MIN_DATA_QUALITY)
    risk_blocked = bool(
        row.get("trade_guard_blocked")
        or row.get("market_contract_valid") is False
    )
    # Today's realised move must not decide tomorrow's direction.  A symbol
    # that independently retains trend/volume/flow support can therefore rank
    # as an UP continuation even after a strong session.  Execution risk is
    # applied separately to buyability below.
    forecast_score = _clamp(base_score + shadow_points)
    if risk_blocked:
        forecast_score = min(forecast_score, 35.0)
    up_probability = _clamp(50.0 + (forecast_score - 50.0) * 0.75, 20.0, 80.0)
    direction = (
        "WAITING" if not ready
        else "INSUFFICIENT" if not data_ready
        else "UP" if forecast_score >= 58.0
        else "DOWN" if forecast_score <= 42.0
        else "ABSTAIN"
    )
    entry_score = _clamp(row.get("entry_score"), 0.0, 100.0)
    buyability = _clamp(forecast_score * 0.55 + entry_score * 0.45 - chase_penalty)
    buyability = min(buyability, quality)
    if direction != "UP":
        buyability = min(buyability, 55.0)
    if risk_blocked:
        buyability = 0.0
    if not ready:
        status = "waiting_close_snapshot"
    elif not data_ready:
        status = "data_insufficient"
    elif risk_blocked:
        status = "risk_blocked"
    elif direction == "DOWN":
        status = "forecast_down"
    elif direction == "UP" and up_probability >= 58.0 and buyability >= 60.0:
        status = "candidate_wait_live_confirmation"
    else:
        status = "observe"
    continuation_confirmed, continuation_reasons = _continuation_evidence(
        families, shadow_points, ready=data_ready and direction == "UP"
    )

    return {
        "symbol": row.get("symbol"),
        "name": row.get("name") or row.get("symbol"),
        "market": str(row.get("market") or "").upper(),
        "asset_type": row.get("type"),
        "group": _group(row),
        "session_date": source_date or None,
        "target": "next_exchange_session_close_vs_source_close",
        "direction": direction,
        "forecast_score": round(forecast_score, 1),
        "up_probability_estimate_pct": round(up_probability, 1),
        "probability_status": "research_estimate_pending_calibration",
        "buyability_score": round(buyability, 1),
        "buyability_status": status,
        "data_quality_pct": quality,
        "data_ready": data_ready,
        "risk_blocked": risk_blocked,
        "risk_reason": row.get("trade_guard_reason") or None,
        "same_session_change_pct": _finite(row.get("change_pct")),
        "same_session_change_direct_points_removed": removed_change_points,
        "same_session_change_positive_bonus_points": 0,
        "same_session_change_forecast_penalty_points": 0,
        "chase_risk_penalty_points": chase_penalty,
        "chase_risk_reasons": chase_reasons,
        "continuation_forecast_allowed": True,
        "continuation_evidence_confirmed": continuation_confirmed,
        "continuation_evidence_reasons": continuation_reasons,
        "evidence_families": families,
        "evidence_family_weights": FAMILY_WEIGHTS,
        "shadow_adjustment_points": shadow_points,
        "shadow_evidence": shadow_evidence,
        "model_votes": votes,
        "formal_ranking_unchanged": True,
        "places_orders": False,
    }


def _rank_group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    forecast = sorted(rows, key=lambda item: (
        not item["data_ready"], item["risk_blocked"],
        0 if item["direction"] == "UP" else 1 if item["direction"] == "ABSTAIN" else 2,
        -item["up_probability_estimate_pct"], -item["forecast_score"],
        str(item.get("symbol") or ""),
    ))
    buyable = sorted(rows, key=lambda item: (
        not item["data_ready"], item["risk_blocked"],
        item["buyability_status"] != "candidate_wait_live_confirmation",
        -item["buyability_score"], -item["up_probability_estimate_pct"],
        str(item.get("symbol") or ""),
    ))
    for rank, item in enumerate(forecast, 1):
        item["forecast_rank"] = rank
    buyable_rank = {str(item.get("symbol")): rank for rank, item in enumerate(buyable, 1)}
    for item in forecast:
        item["buyability_rank"] = buyable_rank[str(item.get("symbol"))]
    return {"forecast": forecast, "buyable": buyable}


def _integrity(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_history(
    reports_dir: Path,
    groups: dict[str, dict[str, list[dic