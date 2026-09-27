"""Forward-only practical weighted ranking isolated from formal V6.

Each holding period is a separate question with its own 100-point evidence
matrix. Scores are frozen and compared with formal V6, but never change
production weights, Central AI recommendations, or broker actions.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
MODEL_VERSION = "PRACTICAL-HORIZON-RANKING-V3-SHADOW"
HORIZONS = {
    "10d": {"sessions": 10, "label": "10日", "prediction_code": "UP_10D", "execution": "short"},
    "21d": {"sessions": 21, "label": "1個月", "prediction_code": "UP_21D", "execution": "medium"},
    "63d": {"sessions": 63, "label": "3個月", "prediction_code": "UP_63D", "execution": "long"},
    "126d": {"sessions": 126, "label": "半年", "prediction_code": "UP_126D", "execution": "long"},
}
WEIGHTS = {
    "10d": {"period_model": 20, "technical_kline": 20, "capital_volume_institution": 25, "sector_strength": 10, "fundamental_growth": 5, "valuation": 5, "entry_position": 10, "market_risk": 3, "data_quality": 2},
    "21d": {"period_model": 20, "technical_kline": 15, "capital_volume_institution": 20, "sector_strength": 15, "fundamental_growth": 10, "valuation": 5, "entry_position": 5, "market_risk": 5, "data_quality": 5},
    "63d": {"period_model": 20, "technical_kline": 10, "capital_volume_institution": 15, "sector_strength": 15, "fundamental_growth": 15, "valuation": 10, "entry_position": 5, "market_risk": 5, "data_quality": 5},
    "126d": {"period_model": 20, "technical_kline": 5, "capital_volume_institution": 10, "sector_strength": 10, "fundamental_growth": 25, "valuation": 15, "entry_position": 3, "market_risk": 7, "data_quality": 5},
}
COMPONENT_LABELS = {
    "period_model": "該期間獨立預判模型",
    "technical_kline": "技術趨勢與K線",
    "capital_volume_institution": "資金流、成交量與法人",
    "sector_strength": "族群強弱與產業位置",
    "fundamental_growth": "基本面、成長與財務品質",
    "valuation": "估值合理程度",
    "entry_position": "買進位置與風險報酬",
    "market_risk": "大盤環境與空頭風險",
    "data_quality": "資料完整度與可信度",
}
OUTCOME_HORIZONS = {key: value["sessions"] for key, value in HORIZONS.items()}
ROUND_TRIP_COST_PCT = {"TW": 0.685, "US": 0.20}
MAX_HISTORY_PER_GROUP = 180


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _bounded(value: Any, default: float = 50.0) -> float:
    number = _number(value, default)
    return round(max(0.0, min(100.0, float(number))), 2)


def _average(values: list[Any], default: float = 50.0) -> float:
    ready = [float(value) for value in (_number(item) for item in values) if value is not None]
    return round(sum(ready) / len(ready), 2) if ready else default


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _group(row: dict[str, Any]) -> str:
    market = str(row.get("market") or "").upper()
    kind = "ETF" if "ETF" in str(row.get("type") or row.get("asset_type") or "").upper() else "STOCK"
    return f"{market}_{kind}"


def _evidence(decision: dict[str, Any], source_id: str) -> dict[str, Any]:
    return next(
        (
            row for row in decision.get("evidence") or []
            if isinstance(row, dict) and row.get("source_id") == source_id
        ),
        {},
    )


def _signed_evidence_score(evidence: dict[str, Any], *, risk: bool = False) -> float | None:
    if not evidence or evidence.get("decision_usage_level") != "usable":
        return None
    direction = str(evidence.get("direction") or "missing")
    strength = _bounded(evidence.get("strength"))
    if direction == "support":
        return strength
    if direction == "oppose":
        return max(0.0, 100.0 - strength)
    if direction == "neutral":
        return 50.0
    return None


def _prediction_score(decision: dict[str, Any], horizon: str) -> float | None:
    row = ((decision.get("prediction_engine") or {}).get("horizons") or {}).get(horizon)
    if not isinstance(row, dict) or row.get("trade_blocked"):
        return None
    return _average([row.get("probability_pct"), row.get("buyability_score")])


ENTRY_SCORES = {
    "entry_confirm": 90.0,
    "entry_wait_confirmation": 62.0,
    "no_chase": 35.0,
    "wait_stabilize": 42.0,
    "take_profit_1": 30.0,
    "take_profit_2": 20.0,
    "stop_exit": 0.0,
    "exit_or_avoid": 0.0,
    "no_data": 0.0,
    "watch": 50.0,
}


def _component_scores(
    row: dict[str, Any], decision: dict[str, Any], horizon: str
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    config = HORIZONS[horizon]
    weights = WEIGHTS[horizon]
    capital = _evidence(decision, "capital_flow_shadow")
    institution = _evidence(decision, "tw_official_institution")
    valuation = _evidence(decision, "valuation_shadow")
    inverse = _evidence(decision, "inverse_etf_shadow")
    news = _evidence(decision, "verified_news")

    execution = (
        ((decision.get("horizons") or {}).get(config["execution"]) or {}).get("execution")
        or {}
    )
    entry_code = str(execution.get("code") or "watch")
    entry_score = _average([row.get("entry_score"), ENTRY_SCORES.get(entry_code, 50.0)])
    capital_score = _average([
        row.get("volume_score"), row.get("market_flow_score"),
        _signed_evidence_score(capital), _signed_evidence_score(institution),
    ])
    fundamental = _average([
        row.get("fundamental_score"), row.get("growth_score"),
        row.get("financial_quality_score"),
    ])
    valuation_score = _signed_evidence_score(valuation, risk=True)
    if valuation_score is None:
        valuation_score = 50.0
    risk_parts = [row.get("macro_score"), _signed_evidence_score(inverse, risk=True),
                  _signed_evidence_score(news, risk=True)]
    market_risk = _average(risk_parts)
    quality = _average([
        row.get("market_data_quality_score"), row.get("overall_confidence"),
        row.get("entry_data_quality_score"),
    ])
    scores = {
        "period_model": _prediction_score(decision, config["prediction_code"]),
        "technical_kline": _average([row.get("technical_score"), row.get("kline_score")]),
        "capital_volume_institution": capital_score,
        "sector_strength": _average([
            row.get("group_score"), row.get("tw_sector_context_score"), row.get("mid_long_score")
        ]),
        "fundamental_growth": fundamental,
        "valuation": valuation_score,
        "entry_position": entry_score,
        "market_risk": market_risk,
        "data_quality": quality,
    }
    components = {
        key: {
            "label": COMPONENT_LABELS[key],
            "weight_pct": weights[key],
            "score": _bounded(score),
            "weighted_points": round(_bounded(score) * weights[key] / 100.0, 2),
        }
        for key, score in scores.items()
    }
    return components, execution


def _rank_row(row: dict[str, Any], decision: dict[str, Any], horizon: str) -> dict[str, Any]:
    components, execution = _component_scores(row, decision, horizon)
    score = round(sum(item["weighted_points"] for item in components.values()), 2)
    blocked = bool(decision.get("risk_blocks") or decision.get("core_data_missing"))
    execution_code = str(execution.get("code") or "watch")
    if blocked:
        status = "blocked"
        label = "安全阻擋，不列入候選"
    elif execution_code == "no_chase":
        status = "no_chase"
        label = "排名保留，高於買進區不追價"
    elif score >= 80:
        status = "strong_shadow_candidate"
        label = "強勢影子候選，等待驗證"
    elif score >= 75:
        status = "shadow_candidate"
        label = "進入買進區後才評估"
    elif score >= 65:
        status = "watch"
        label = "觀察"
    else:
        status = "avoid"
        label = "暫不考慮"
    return {
        "symbol": row.get("symbol"),
        "name": row.get("name") or row.get("symbol"),
        "market": row.get("market"),
        "asset_group": _group(row),
        "asset_type": row.get("type"),
        "horizon": horizon,
        "horizon_label": HORIZONS[horizon]["label"],
        "horizon_sessions": HORIZONS[horizon]["sessions"],
        "session_date": row.get("official_session_date"),
        "price": _number(row.get("price")),
        "formal_rank": row.get("overall_rank") or row.get("rank"),
        "formal_score": _number(row.get("overall_ranking_score") or row.get("score")),
        "practical_shadow_score": score,
        "status": status,
        "status_label": label,
        "entry_state": execution,
        "components": components,
        "blocked": blocked,
        "decision_usage_level": "shadow_only",
        "decision_usage_label": "不可使用（驗證中）",
        "formal_v6_unchanged": True,
        "automatic_orders": False,
    }


def _settle_basket(items: list[dict[str, Any]], prices: dict[str, float], market: str) -> dict[str, Any] | None:
    returns = []
    for item in items:
        start = _number(item.get("price"))
        end = prices.get(str(item.get("symbol") or ""))
        if start is None or start <= 0 or end is None or end <= 0:
            continue
        returns.append((end / start - 1.0) * 100.0 - ROUND_TRIP_COST_PCT[market])
    if not returns:
        return None
    return {
        "samples": len(returns),
        "coverage_pct": round(len(returns) / max(1, len(items)) * 100.0, 1),
        "avg_net_return_pct": round(sum(returns) / len(returns), 3),
        "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100.0, 1),
        "worst_net_return_pct": round(min(returns), 3),
    }


def _update_history(
    reports_dir: Path,
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict[str, Any]:
    path = reports_dir / "practical_weighted_history.json"
    history = _read(path) or {
        "schema_version": 1, "sessions": {"TW": [], "US": []}, "snapshots": []
    }
    sessions = history.setdefault("sessions", {"TW": [], "US": []})
    snapshots = history.setdefault("snapshots", [])
    prices_by_market = {"TW": {}, "US": {}}
    dates_by_market = {"TW": set(), "US": set()}
    for row in rows:
        market = str(row.get("market") or "").upper()
        symbol = str(row.get("symbol") or "")
        price = _number(row.get("price"))
        date = str(row.get("official_session_date") or "")
        if market in prices_by_market and symbol and price and price > 0:
            prices_by_market[market][symbol] = price
        if market in dates_by_market and date:
            dates_by_market[market].add(date)

    checkpoint_market = "TW" if period == "evening" else "US" if period == "morning" else None
    if not intraday and checkpoint_market and len(dates_by_market[checkpoint_market]) == 1:
        session_date = next(iter(dates_by_market[checkpoint_market]))
        if session_date not in sessions.setdefault(checkpoint_market, []):
            sessions[checkpoint_market].append(session_date)
            sessions[checkpoint_market] = sorted(set(sessions[checkpoint_market]))
        existing = {
            (
                str(item.get("asset_group")),
                str(item.get("session_date")),
                str(item.get("model_version") or "V1"),
                str(item.get("horizon") or "mixed"),
            )
            for item in snapshots
        }
        rows_by_group = {
            group: [row for row in rows if _group(row) == group]
            for group in (f"{checkpoint_market}_STOCK", f"{checkpoint_market}_ETF")
        }
        for group, horizon_rankings in rankings.items():
            if not group.startswith(checkpoint_market):
                continue
            formal = sorted(
                rows_by_group.get(group, []),
                key=lambda row: (int(row.get("overall_rank") or row.get("rank") or 999999), str(row.get("symbol") or "")),
            )[:10]
            for horizon, ranked in horizon_rankings.items():
                if (group, session_date, MODEL_VERSION, horizon) in existing:
                    continue
                snapshots.append({
                    "asset_group": group,
                    "market": checkpoint_market,
                    "model_version": MODEL_VERSION,
                    "horizon": horizon,
                    "session_date": session_date,
                    "created_at": updated_at,
                    "practical_top10": [
                        {"symbol": item["symbol"], "price": item["price"]}
                        for item in ranked if not item["blocked"]
                    ][:10],
                    "formal_top10": [
                        {"symbol": item.get("symbol"), "price": _number(item.get("price"))}
                        for item in formal
                    ],
                    "outcome": None,
                })

    for snapshot in snapshots:
        market = str(snapshot.get("market") or "")
        market_sessions = sessions.get(market) or []
        signal_date = str(snapshot.get("session_date") or "")
        current_dates = dates_by_market.get(market) or set()
        if signal_date not in market_sessions or len(current_dates) != 1:
            continue
        current_date = next(iter(current_dates))
        if current_date not in market_sessions:
            continue
        elapsed = market_sessions.index(current_date) - market_sessions.index(signal_date)
        horizon = str(snapshot.get("horizon") or "")
        required = OUTCOME_HORIZONS.get(horizon)
        if required is None or elapsed < required or isinstance(snapshot.get("outcome"), dict):
            continue
        practical = _settle_basket(snapshot.get("practical_top10") or [], prices_by_market[market], market)
        formal = _settle_basket(snapshot.get("formal_top10") or [], prices_by_market[market], market)
        if practical and formal:
            snapshot["outcome"] = {
                "evaluated_session_date": current_date,
                "elapsed_sessions": elapsed,
                "practical": practical,
                "formal_v6": formal,
                "excess_return_pct": round(
                    practical["avg_net_return_pct"] - formal["avg_net_return_pct"], 3
                ),
            }

    trimmed = []
    for group in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        group_rows = [item for item in snapshots if item.get("asset_group") == group]
        legacy_rows = [item for item in group_rows if item.get("horizon") not in HORIZONS]
        trimmed.extend(legacy_rows[-MAX_HISTORY_PER_GROUP:])
        for horizon in HORIZONS:
            horizon_rows = [item for item in group_rows if item.get("horizon") == horizon]
            trimmed.extend(horizon_rows[-MAX_HISTORY_PER_GROUP:])
    history["snapshots"] = sorted(
        trimmed,
        key=lambda item: (
            str(item.get("session_date")), str(item.get("asset_group")), str(item.get("horizon"))
        ),
    )
    history["updated_at"] = updated_at
    _write(path, history)
    return history


def _validation(history: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    for group in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        group_rows = [
            row for row in history.get("snapshots") or []
            if row.get("asset_group") == group and row.get("model_version") == MODEL_VERSION
        ]
        horizons = {}
        for label in OUTCOME_HORIZONS:
            outcomes = [
                row.get("outcome") for row in group_rows
                if row.get("horizon") == label and isinstance(row.get("outcome"), dict)
            ]
            practical = [row["practical"]["avg_net_return_pct"] for row in outcomes]
            formal = [row["formal_v6"]["avg_net_return_pct"] for row in outcomes]
            excess = [row["excess_return_pct"] for row in outcomes]
            horizons[label] = {
                "status": "manual_review_available" if len(outcomes) >= 60 else "preliminary_only" if len(outcomes) >= 20 else "collecting",
                "completed_comparisons": len(outcomes),
                "practical_avg_net_return_pct": round(sum(practical) / len(practical), 3) if practical else None,
                "formal_v6_avg_net_return_pct": round(sum(formal) / len(formal), 3) if formal else None,
                "avg_excess_return_pct": round(sum(excess) / len(excess), 3) if excess else None,
                "practical_win_rate_vs_v6_pct": round(sum(value > 0 for value in excess) / len(excess) * 100.0, 1) if excess else None,
            }
        completed = min(horizons["10d"]["completed_comparisons"], horizons["21d"]["completed_comparisons"])
        groups[group] = {
            "status": "manual_review_available" if completed >= 60 else "preliminary_only" if completed >= 20 else "collecting",
            "completed_comparisons": completed,
            "minimum_preliminary": 20,
            "minimum_manual_review": 60,
            "horizons": horizons,
        }
    return {
        "groups": groups,
        "formal_promotion_automatic": False,
        "owner_approval_required": True,
    }


def update_practical_weighted_ranking(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    decision_by_symbol = {
        str(item.get("symbol") or ""): item for item in decisions if item.get("symbol")
    }
    rankings = {}
    for group in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        rankings[group] = {}
        group_rows = [row for row in rows if isinstance(row, dict) and _group(row) == group]
        for horizon in HORIZONS:
            values = [
                _rank_row(copy.deepcopy(row), decision_by_symbol[str(row.get("symbol") or "")], horizon)
                for row in group_rows
                if str(row.get("symbol") or "") in decision_by_symbol
            ]
            values.sort(
                key=lambda item: (
                    item["blocked"], -item["practical_shadow_score"], str(item["symbol"])
                )
            )
            rankings[group][horizon] = [
                dict(item, practical_rank=index + 1) for index, item in enumerate(values[:20])
            ]
    history = _update_history(
        reports_dir, rankings, rows, period=period, updated_at=updated_at, intraday=intraday
    )
    validation = _validation(history)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "updated_at": updated_at,
        "period": period,
        "status": "collecting_forward_validation",
        "horizons": HORIZONS,
        "weights_by_horizon": WEIGHTS,
        "component_labels": COMPONENT_LABELS,
        "thresholds": {
            "strong_shadow_candidate": 80,
            "shadow_candidate": 75,
            "watch": 65,
        },
        "rankings": rankings,
        "validation": validation,
        "policy": {
            "ten_day_is_real_10_session_model": True,
            "one_month_is_real_21_session_model": True,
            "three_month_is_real_63_session_model": True,
            "six_month_is_real_126_session_model": True,
            "each_horizon_has_its_own_100_pct_weight_matrix": True,
            "horizons_are_never_used_as_weights_in_one_mixed_score": True,
            "old_5d_or_45d_never_relabelled": True,
            "only_usable_external_evidence_affects_components": True,
            "limited_reference_never_adds_points": True,
            "shadow_scores_are_for_comparison_not_buy_signals": True,
            "formal_v6_unchanged": True,
            "central_ai_unchanged": True,
            "automatic_orders": False,
        },
    }
    _write(reports_dir / "practical_weighted_ranking.json", payload)
    return payload
