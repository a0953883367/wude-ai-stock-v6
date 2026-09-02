"""Build the read-only Central AI Decision Hub evidence report.

This module is deliberately downstream of the formal V6 rankings.  It turns
existing model outputs into comparable evidence, explains conflicts, and
leaves the final choice to the owner.  It never changes a score/rank and never
creates broker instructions.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
from typing import Any

from comprehensive_shadow_ranking import update_comprehensive_shadow_ranking
from evidence_contract import build_unified_evidence_report, make_evidence
from next_session_ranking import update_next_session_ranking
from portfolio_control import build_portfolio_control


SCHEMA_VERSION = 6
MODEL_VERSION = "CENTRAL-DECISION-HUB-V6"
CHUNK_SIZE = 50
EVIDENCE_CHUNK_SIZE = 400
CAPITAL_FLOW_MIN_CONFIDENCE = 60.0
CAPITAL_FLOW_MIN_PERSISTENCE = 50.0
CAPITAL_FLOW_MAX_SHORT_ADJUSTMENT = 3.0
POLICY = {
    "formal_ranking_locked": True,
    "shadow_models_evidence_only": True,
    "missing_data_never_imputed": True,
    "automatic_weight_changes": False,
    "automatic_orders": False,
    "horizons_separate": True,
    "user_final_decision_required": True,
    "markets_separate": True,
    "tw_institution_never_applied_to_us": True,
    "page_views_do_not_change_calculation": True,
    "next_session_shadow_isolated": True,
    "next_session_future_outcomes_forbidden": True,
    "current_strength_ranking_auxiliary_only": True,
    "independent_next_session_continuation_allowed": True,
    "prediction_engine_read_only": True,
    "prediction_engine_database_private": True,
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _bounded(value: Any, default: float = 0.0) -> float:
    number = _number(value, default)
    return round(max(0.0, min(100.0, float(number))), 1)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _prediction_engine_index(payload: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    symbols = (payload or {}).get("symbols")
    if not isinstance(symbols, dict):
        return {}
    result = {}
    for row in symbols.values():
        if not isinstance(row, dict) or not row.get("symbol") or not row.get("market"):
            continue
        result[(str(row["market"]).upper(), str(row["symbol"]).upper())] = row
    return result


def _load_prediction_engine_contract(reports_dir: Path) -> dict[str, Any] | None:
    """Reassemble local lazy chunks for Central AI without exposing the DB."""
    index = _read_json(reports_dir / "prediction_engine.json")
    if not isinstance(index, dict):
        return None
    files = index.get("data_files")
    if not isinstance(files, dict):
        return index
    merged = copy.deepcopy(index)
    merged["rankings"] = {}
    merged["symbols"] = {}
    for group, horizons in files.items():
        if not isinstance(horizons, dict):
            continue
        merged["rankings"][group] = {}
        for horizon, filename in horizons.items():
            chunk = _read_json(reports_dir / str(filename))
            if not isinstance(chunk, dict):
                continue
            merged["rankings"][group][horizon] = chunk.get("rankings") or []
            for row in chunk.get("predictions") or []:
                if not isinstance(row, dict) or not row.get("symbol") or not row.get("market"):
                    continue
                key = f'{str(row["market"]).upper()}:{str(row["symbol"]).upper()}'
                symbol = merged["symbols"].setdefault(key, {
                    "symbol": row["symbol"], "name": row.get("name"),
                    "market": row["market"], "asset_group": row.get("asset_group"),
                    "session_date": row.get("session_date"), "horizons": {},
                })
                symbol["horizons"][horizon] = {
                    field: value for field, value in row.items()
                    if field not in {
                        "symbol", "name", "market", "asset_group",
                        "session_date", "horizon_code",
                    }
                }
    return merged


def _prediction_engine_answer(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Give Central AI one compact answer per market and forecast horizon."""
    if not isinstance(payload, dict):
        return {
            "status": "unavailable",
            "headline": "獨立預判引擎尚未產生結果",
            "by_market": {},
            "automatic_orders": False,
        }
    rankings = payload.get("rankings") if isinstance(payload.get("rankings"), dict) else {}
    by_market: dict[str, dict[str, Any]] = {"TW": {}, "US": {}}
    for market in ("TW", "US"):
        for horizon in ("NEXT_1D", "UP_5D", "UP_45D", "UP_126D", "DOWN_14D", "DOWN_21D"):
            candidates = []
            for group in (f"{market}_STOCK", f"{market}_ETF"):
                values = (rankings.get(group) or {}).get(horizon) or []
                candidates.extend(item for item in values if isinstance(item, dict))
            candidates.sort(key=lambda item: (-float(item.get("ranking_score") or 0), str(item.get("symbol") or "")))
            if candidates:
                item = candidates[0]
                by_market[market][horizon] = {
                    key: item.get(key) for key in (
                        "symbol", "name", "asset_group", "session_date", "target_side",
                        "probability_pct", "expected_return_pct", "buyability_score",
                        "downside_risk_pct", "data_quality_pct", "ranking_score",
                    )
                }
    return {
        "status": payload.get("status") or "collecting",
        "model_version": payload.get("model_version"),
        "headline": "依各週期分開預判最大漲幅／下跌風險；正式強勢排名僅作輔助",
        "by_market": by_market,
        "paper_portfolios": (payload.get("paper_portfolios") or {}).get("capital_policy") or {},
        "formal_v6_unchanged": True,
        "automatic_orders": False,
    }


def _evidence(
    source_id: str,
    source_label: str,
    horizon: str,
    direction: str,
    strength: Any,
    confidence: Any,
    as_of: str,
    status: str,
    reason: str,
    *,
    affects_decision: bool = True,
    provenance: str | None = None,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_label": source_label,
        "horizon": horizon,
        "direction": direction,
        "strength": _bounded(strength),
        "confidence": _bounded(confidence),
        "as_of": as_of or None,
        "status": status,
        "reason": reason,
        "affects_decision": bool(affects_decision),
        "provenance": provenance or source_id,
    }


def _direction(score: Any, *, high: float = 65.0, low: float = 45.0) -> str:
    value = _number(score)
    if value is None:
        return "missing"
    if value >= high:
        return "support"
    if value < low:
        return "oppose"
    return "neutral"


def _recommendation(score: float, eligible: bool, blocked: bool) -> str:
    if blocked:
        return "data_insufficient"
    if score < 48:
        return "avoid"
    if not eligible:
        return "wait_pullback" if score >= 60 else "watch"
    if score >= 72:
        return "can_scale"
    if score >= 60:
        return "wait_pullback"
    return "watch"


def _action_label(code: str) -> str:
    return {
        "can_scale": "可分批評估",
        "wait_pullback": "等待買點",
        "watch": "列入觀察",
        "avoid": "暫不考慮",
        "data_insufficient": "資料不足，不判斷",
    }[code]


def _execution_state(plan: dict[str, Any], price: float | None) -> dict[str, str]:
    """Translate an existing plan into a clear read-only entry/exit state."""
    recommendation = str(plan.get("recommendation") or "data_insufficient")
    entry_low = _number(plan.get("entry_low"))
    entry_high = _number(plan.get("entry_high"))
    stop = _number(plan.get("stop"))
    target1 = _number(plan.get("target1"))
    target2 = _number(plan.get("target2"))
    if recommendation == "data_insufficient" or price is None:
        return {"code": "no_data", "label": "資料不足，不操作", "reason": "缺少可驗證價格或進出場資料"}
    if recommendation == "avoid":
        return {"code": "exit_or_avoid", "label": "不進場；若持有則評估退出", "reason": "中央風險或至少一個時段不合格"}
    if stop is not None and price <= stop:
        return {"code": "stop_exit", "label": "跌破停損，若持有應退出", "reason": f"現價 {price:g} 已低於停損 {stop:g}"}
    if target2 is not None and price >= target2:
        return {"code": "take_profit_2", "label": "到達第二目標，分批出場", "reason": f"現價 {price:g} 已達第二目標 {target2:g}"}
    if target1 is not None and price >= target1:
        return {"code": "take_profit_1", "label": "到達第一目標，先分批獲利", "reason": f"現價 {price:g} 已達第一目標 {target1:g}"}
    if entry_low is not None and entry_high is not None:
        if entry_low <= price <= entry_high:
            if recommendation == "can_scale":
                return {"code": "entry_confirm", "label": "進入買進區，等確認後分批", "reason": "需再確認15～30分鐘量價轉強且支撐未破"}
            return {"code": "entry_wait_confirmation", "label": "價格到買進區，但條件尚未成熟", "reason": "中央結論仍是等待買點，不視為已可買"}
        if price > entry_high:
            return {"code": "no_chase", "label": "高於買進區，不追價", "reason": f"等回到 {entry_low:g}～{entry_high:g} 再判斷"}
        return {"code": "wait_stabilize", "label": "低於買進區，等待止穩", "reason": f"未止穩前不因價格較低而直接買進"}
    return {"code": "watch", "label": "只有觀察，尚無進場價", "reason": "進場區資料尚未完整，不推測價格"}


def _quality_confidence(row: dict[str, Any], base: Any) -> float:
    confidence = _number(base, 0.0) or 0.0
    quality = _number(row.get("market_data_quality_score"))
    if quality is not None:
        confidence = min(confidence, quality)
    coverage = _number(row.get("entry_data_coverage"))
    total = _number(row.get("entry_data_total"))
    if coverage is not None and total and total > 0:
        confidence = min(confidence, coverage / total * 100)
    return _bounded(confidence)


def _institution_link(
    row: dict[str, Any], updated_at: str, *, coverage_eligible: bool
) -> dict[str, Any]:
    """Expose verified TW institutional evidence without double-counting it."""
    market = str(row.get("market") or "").upper()
    if market != "TW":
        return {
            "applicable": False,
            "available": False,
            "status": "not_applicable",
            "direction": "missing",
            "reason": "美股使用美股資金流證據，不套用台灣法人資料",
            "affects_central_decision": False,
            "already_counted_in_formal_v6": False,
            "additional_central_adjustment_points": 0.0,
        }
    available = bool(row.get("institution_available"))
    session_date = str(row.get("institution_date") or "")
    source = str(row.get("institution_source") or "")
    total = _number(row.get("institution_net"), _number(row.get("institution_1d")))
    foreign = _number(row.get("foreign_net"))
    trust = _number(row.get("trust_net"))
    dealer = _number(row.get("dealer_net"))
    verified = bool(
        coverage_eligible and available and session_date and source
        and total is not None
    )
    direction = "support" if verified and total > 0 else "oppose" if verified and total < 0 else "neutral" if verified else "missing"
    return {
        "applicable": True,
        "available": verified,
        "status": "linked_formal_once" if verified else "coverage_blocked_or_missing",
        "direction": direction,
        "session_date": session_date or None,
        "source": source or None,
        "total_net_shares": total if verified else None,
        "foreign_net_shares": foreign if verified else None,
        "trust_net_shares": trust if verified else None,
        "dealer_net_shares": dealer if verified else None,
        "reason": (
            "官方法人證據已在正式 V6 單次計入；中央只讀取並顯示，不重複加分"
            if verified else "官方法人資料未通過覆蓋率或日期驗證，中央不使用也不補 0"
        ),
        "affects_central_decision": verified,
        "already_counted_in_formal_v6": verified,
        "additional_central_adjustment_points": 0.0,
    }


def _share_text(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+,.0f} 股"


def _valuation_index(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not report:
        return {}
    return {
        str(row.get("symbol") or "").upper(): row
        for row in report.get("data", [])
        if isinstance(row, dict) and row.get("symbol")
    }


def _rotation_index(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for market, market_row in (report or {}).get("markets", {}).items():
        snapshots = market_row.get("snapshots", []) if isinstance(market_row, dict) else []
        if not snapshots:
            continue
        for sector in snapshots[-1].get("sectors", []):
            if isinstance(sector, dict) and sector.get("industry"):
                result[f"{market}:{sector['industry']}"] = sector
    return result


def _capital_flow_index(
    report: dict[str, Any] | None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index only complete closed-session quality rankings by stock.

    Net-flow amount remains descriptive. Eligibility and central influence
    come from the original confidence/persistence model, never amount size.
    """
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not isinstance(report, dict):
        return result
    if (report.get("policy") or {}).get("intraday_exposed") is not False:
        return result
    for market, sessions in (report.get("markets") or {}).items():
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if not isinstance(session, dict) or not (
                session.get("closed") is True
                and session.get("complete") is True
                and session.get("session_scope") == "regular_hours_only"
                and session.get("ranking_basis") == "signal_quality"
            ):
                continue
            session_date = str(session.get("session_date") or "")
            for field, direction in (
                ("top_inflows", "support"), ("top_outflows", "oppose")
            ):
                for row in session.get(field) or []:
                    if not isinstance(row, dict) or not row.get("symbol"):
                        continue
                    key = (
                        str(market).upper(), session_date,
                        str(row.get("symbol") or "").upper(),
                    )
                    candidate = {
                        **row,
                        "flow_direction": direction,
                        "session_date": session_date,
                        "source": session.get("source"),
                    }
                    current = result.get(key)
                    if current is None or _number(candidate.get("confidence"), 0) > _number(
                        current.get("confidence"), 0
                    ):
                        result[key] = candidate
    return result


def _inverse_indexes(
    database: dict[str, Any] | None,
    shadow: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    mappings = {
        str(row.get("symbol") or "").upper(): row
        for row in (database or {}).get("mappings", [])
        if isinstance(row, dict) and row.get("symbol")
    }
    candidates: dict[str, dict[str, Any]] = {}
    for market, market_row in (shadow or {}).get("markets", {}).items():
        if not isinstance(market_row, dict):
            continue
        for row in market_row.get("current_candidates", []):
            if isinstance(row, dict) and row.get("group"):
                candidates[f"{market}:{row['group']}"] = row
    return mappings, candidates


def _inverse_market_ready(report: dict[str, Any] | None, market: str) -> bool:
    summary = (((report or {}).get("markets") or {}).get(market) or {}).get("summary") or {}
    return bool(summary) and all(
        isinstance(row, dict) and int(row.get("samples") or 0) >= 20
        for row in summary.values()
    )


def _model_readiness(
    reports: dict[str, dict[str, Any] | None],
    institution_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    accuracy = reports.get("accuracy") or {}
    calibration = accuracy.get("calibration", {})
    valuation = reports.get("valuation") or {}
    inverse = reports.get("inverse") or {}
    rotation = reports.get("rotation") or {}
    holding = reports.get("holding") or {}
    million = reports.get("million") or {}
    guard = reports.get("system_guard") or {}
    stockq = reports.get("stockq") or {}
    validation = reports.get("validation_60d") or {}
    graduation = reports.get("graduation") or {}
    tw_financial = reports.get("tw_financial") or {}
    capital_flow = reports.get("capital_flow") or {}
    institution_status = institution_status or {}
    valuation_coverage = valuation.get("coverage", {})
    inverse_samples = sum(
        len((inverse.get("markets", {}).get(market) or {}).get("cohorts", []))
        for market in ("TW", "US")
    )
    return {
        "forward_validation": {
            "collected_trading_days": calibration.get("trading_days_collected", 0),
            "target_trading_days": calibration.get("minimum_trading_days", 60),
            "remaining_trading_days": calibration.get("remaining_trading_days", 60),
            "ready": bool(calibration.get("ready_for_model_selection")),
            "status": calibration.get("status") or "missing",
        },
        "valuation": {
            "TW": valuation_coverage.get("TW", {}),
            "US": valuation_coverage.get("US", {}),
            "weight_review_ready": bool(
                (valuation.get("validation") or {}).get("weight_review_ready")
            ),
        },
        "rotation": {
            market: (rotation.get("markets", {}).get(market) or {}).get("status", "missing")
            for market in ("TW", "US")
        },
        "inverse": {
            "forward_cohorts": inverse_samples,
            "ready": inverse_samples > 0 and all(
                any((entry or {}).get("samples", 0) >= 20 for entry in (
                    (inverse.get("markets", {}).get(market) or {}).get("summary", {}).values()
                ))
                for market in ("TW", "US")
            ),
        },
        "holding_validation": {
            "medium_45d": {
                market: (holding.get("medium", {}).get(market) or {}).get("status", "missing")
                for market in ("TW", "US")
            },
            "long_6m": (holding.get("long") or {}).get("status", "missing"),
            "updated_at": holding.get("updated_at"),
        },
        "five_day_simulation": {
            market: {
                "status": (million.get("markets", {}).get(market) or {}).get("status", "missing"),
                "completed_days": (million.get("markets", {}).get(market) or {}).get("completed_days", 0),
                "net_return_pct": (million.get("markets", {}).get(market) or {}).get("cumulative_net_return_pct"),
            }
            for market in ("TW", "US")
        },
        "system_guard": {
            "status": guard.get("status", "missing"),
            "warnings": [
                {
                    "code": item.get("code"),
                    "title": item.get("title"),
                    "detail": item.get("detail"),
                    "action": item.get("action"),
                }
                for item in guard.get("checks", [])
                if isinstance(item, dict) and item.get("level") in ("warning", "critical")
            ],
        },
        "stockq_market_context": {
            "status": stockq.get("status", "missing"),
            "indicator_count": stockq.get("indicator_count", 0),
            "cache_status": stockq.get("cache_status", "missing"),
            "market_signal": stockq.get("market_signal", {}),
            "affects_formal_ranking": False,
        },
        "validation_60d": {
            "status": validation.get("status", "missing"),
            "collected_trading_days": validation.get("trading_days_collected", 0),
            "target_trading_days": validation.get("target_trading_days", 60),
            "remaining_trading_days": validation.get("remaining_trading_days", 60),
            "ready": bool(validation.get("ready_for_model_selection")),
        },
        "model_graduation": {
            "status": graduation.get("status", "missing"),
            "summary": graduation.get("summary", {}),
            "models": graduation.get("models", []),
            "automatic_promotion": False,
        },
        "tw_official_financial": {
            "status": "ready" if tw_financial else "missing",
            "available": int(tw_financial.get("available_count") or 0),
            "requested": int(tw_financial.get("requested_count") or 0),
            "coverage_pct": tw_financial.get("coverage_pct"),
            "missing": tw_financial.get("missing_symbols", []),
        },
        "tw_institutional": {
            "status": (
                "ready" if institution_status.get("ranking_eligible")
                else str(institution_status.get("reason") or "missing")
            ),
            "available": bool(institution_status.get("ranking_eligible")),
            "ai_eligible": bool(institution_status.get("ai_eligible")),
            "session_date": institution_status.get("session_date"),
            "returned_count": int(institution_status.get("returned_count") or 0),
            "expected_count": int(institution_status.get("expected_count") or 0),
            "coverage_pct": _number(institution_status.get("coverage_pct")),
            "minimum_coverage_pct": _number(
                institution_status.get("minimum_coverage_pct"), 95.0
            ),
            "markets_separate": True,
            "applies_to": "TW",
            "never_applies_to": "US",
        },
        "capital_flow": {
            "status": "ready" if capital_flow else "missing",
            "closed_complete_sessions": sum(
                1
                for sessions in (capital_flow.get("markets") or {}).values()
                for session in (sessions if isinstance(sessions, list) else [])
                if isinstance(session, dict)
                and session.get("closed") is True
                and session.get("complete") is True
            ),
            "intraday_used": False,
            "ranking_basis": "signal_quality",
            "amount_affects_decision": False,
        },
    }


def _resolved_institution_status(
    rows: list[dict[str, Any]],
    institution_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep standalone hub refreshes from dropping valid TW coverage metadata."""
    if isinstance(institution_status, dict) and institution_status:
        return dict(institution_status)
    tw_rows = [row for row in rows if str(row.get("market") or "") == "TW"]
    expected_dates = sorted({
        str(row.get("official_session_date")) for row in tw_rows
        if row.get("official_session_date")
    })
    candidate_dates = sorted({
        str(row.get("institution_date")) for row in tw_rows
        if row.get("institution_date")
    })
    target_date = expected_dates[-1] if expected_dates else (
        candidate_dates[-1] if candidate_dates else None
    )
    available = [
        row for row in tw_rows
        if row.get("institution_available")
        and str(row.get("institution_date") or "") == str(target_date or "")
        and row.get("institution_source")
        and row.get("institution_net") is not None
    ]
    expected_count = len(tw_rows)
    returned_count = len(available)
    coverage_pct = (
        round(returned_count / expected_count * 100, 2)
        if expected_count else None
    )
    eligible = bool(expected_count and coverage_pct is not None and coverage_pct >= 95.0)
    return {
        "ranking_eligible": eligible,
        "ai_eligible": eligible,
        "session_date": target_date,
        "returned_count": returned_count,
        "expected_count": expected_count,
        "coverage_pct": coverage_pct,
        "minimum_coverage_pct": 95.0,
        "reason": "derived_from_analysis_rows" if available else "missing",
        "provenance": "all_analysis_rows_fallback",
    }


def _build_decision(
    row: dict[str, Any],
    *,
    valuation: dict[str, Any] | None,
    rotation: dict[str, Any] | None,
    inverse_mapping: dict[str, Any] | None,
    inverse_candidate: dict[str, Any] | None,
    inverse_ready: bool,
    capital_flow: dict[str, Any] | None,
    institution_eligible: bool,
    updated_at: str,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "")
    is_etf = "ETF" in str(row.get("type") or "").upper()
    session_date = str(row.get("official_session_date") or row.get("cached_at") or updated_at)
    market_valid = row.get("market_contract_valid") is not False
    price = _number(row.get("price"))
    # A risk rejection is a valid decision, not missing data.  Keep the two
    # states separate so a complete row is never mislabeled as insufficient.
    risk_block = bool(row.get("trade_guard_blocked"))
    data_block = not market_valid or not price or price <= 0
    data_missing = list(row.get("market_data_missing") or [])
    if not price or price <= 0:
        data_missing.append("current_price")
    if not market_valid:
        data_missing.append("market_contract")
    institutional_link = _institution_link(
        row, updated_at, coverage_eligible=institution_eligible
    )
    if institutional_link["applicable"] and not institutional_link["available"]:
        data_missing.append("tw_official_institution")

    short_score = _bounded(row.get("short_term_score"))
    short_confidence = _quality_confidence(row, row.get("short_term_confidence"))
    short_code = _recommendation(
        short_score,
        bool(row.get("short_term_eligible")),
        data_block,
    )

    # The existing 3-12 month plan is split into two read-only decision views.
    # Medium emphasizes trend/positioning; long emphasizes fundamentals/risk.
    technical = _bounded(row.get("technical_score"))
    position = _number(row.get("positioning_score"))
    mid_long = _bounded(row.get("mid_long_score"))
    medium_parts = [mid_long, technical]
    if position is not None:
        medium_parts.append(_bounded(position))
    medium_score = round(sum(medium_parts) / len(medium_parts), 1)
    medium_conf = _quality_confidence(row, row.get("mid_long_confidence"))
    medium_code = _recommendation(
        medium_score,
        bool(row.get("mid_long_eligible")),
        data_block,
    )

    financial = _number(row.get("financial_quality_score"))
    growth = _number(row.get("growth_score"))
    fundamental = _number(row.get("fundamental_score"))
    long_parts = [mid_long]
    if is_etf:
        long_parts.append(technical)
    else:
        long_parts.extend(_bounded(item) for item in (financial, growth, fundamental) if item is not None)
    long_score = round(sum(long_parts) / len(long_parts), 1)
    long_conf = medium_conf
    shadow_baseline = {
        "short": {"score": short_score, "confidence": short_confidence},
        "medium": {"score": medium_score, "confidence": medium_conf},
        "long": {"score": long_score, "confidence": long_conf},
    }
    valuation_unit_valid = not bool(
        valuation
        and str(row.get("market") or "").upper() == "TW"
        and str(valuation.get("market_cap_source") or "").startswith("price_times_inferred_shares")
        and valuation.get("financial_statement_unit") != "TWD"
    )
    valuation_ready = bool(
        valuation and valuation.get("status") == "ready" and valuation_unit_valid
    )
    valuation_pressure = _number((valuation or {}).get("valuation_pressure_score"))
    if valuation_ready and valuation_pressure is not None:
        # Risk-only deduction in this downstream decision; formal rank is untouched.
        long_score = round(max(0.0, long_score - max(0.0, valuation_pressure - 55) * 0.20), 1)

    flow_confidence = _bounded((capital_flow or {}).get("confidence"))
    flow_persistence = _bounded((capital_flow or {}).get("persistence_pct"))
    flow_direction = str((capital_flow or {}).get("flow_direction") or "missing")
    flow_ready = bool(
        capital_flow
        and flow_direction in ("support", "oppose")
        and flow_confidence >= CAPITAL_FLOW_MIN_CONFIDENCE
        and flow_persistence >= CAPITAL_FLOW_MIN_PERSISTENCE
    )
    flow_adjustment = 0.0
    if flow_ready:
        quality_margin = min(
            (flow_confidence - CAPITAL_FLOW_MIN_CONFIDENCE)
            / (100.0 - CAPITAL_FLOW_MIN_CONFIDENCE),
            (flow_persistence - CAPITAL_FLOW_MIN_PERSISTENCE)
            / (100.0 - CAPITAL_FLOW_MIN_PERSISTENCE),
        )
        flow_adjustment = round(
            max(0.5, min(CAPITAL_FLOW_MAX_SHORT_ADJUSTMENT,
                         0.5 + quality_margin * 2.5)),
            1,
        )
        if flow_direction == "oppose":
            flow_adjustment *= -1
        short_score = round(max(0.0, min(100.0, short_score + flow_adjustment)), 1)
        short_code = _recommendation(
            short_score, bool(row.get("short_term_eligible")), data_block
        )

    inverse_bear_score = _number((inverse_candidate or {}).get("bear_score"))
    inverse_active = bool(inverse_ready and inverse_bear_score is not None and inverse_bear_score >= 60)
    if inverse_active:
        # This changes only the downstream central view after the isolated
        # inverse model passes its own forward gate.  Formal V6 scores/ranks
        # remain untouched.
        inverse_penalty = min(5.0, max(0.0, inverse_bear_score - 60.0) * 0.125)
        short_score = round(max(0.0, short_score - inverse_penalty), 1)
        medium_score = round(max(0.0, medium_score - inverse_penalty), 1)
        short_code = _recommendation(
            short_score, bool(row.get("short_term_eligible")), data_block
        )
        medium_code = _recommendation(
            medium_score, bool(row.get("mid_long_eligible")), data_block
        )
    long_code = _recommendation(
        long_score,
        bool(row.get("mid_long_eligible")) and (
            is_etf or financial is not None or fundamental is not None
        ),
        data_block,
    )

    evidence: list[dict[str, Any]] = []
    evidence.append(_evidence(
        "formal_v6", "正式 V6 排名", "all", _direction(row.get("overall_ranking_score")),
        row.get("overall_ranking_score"), row.get("overall_confidence", 70), session_date,
        "locked", f"正式第 {row.get('overall_rank') or row.get('rank') or '-'} 名；只讀取，不重排",
    ))
    evidence.append(_evidence(
        "short_plan", "1～5 日短線模型", "short", _direction(short_score), short_score,
        short_confidence, session_date,
        "data_blocked" if data_block else "risk_blocked" if risk_block else "ready",
        str(row.get("short_term_reason") or row.get("short_term_status") or "短線資料未提供"),
    ))
    evidence.append(_evidence(
        "medium_45d", "45 日中期拆分", "medium", _direction(medium_score), medium_score,
        medium_conf, session_date, "derived_from_existing_fields",
        "沿用中長線計畫，另以技術趨勢與籌碼定位形成45日觀察，不回寫正式分數",
    ))
    evidence.append(_evidence(
        "long_6m", "6 個月長期拆分", "long", _direction(long_score), long_score,
        long_conf, session_date, "derived_from_existing_fields",
        "沿用中長線計畫，另以財務品質、成長與估值風險形成6個月觀察，不回寫正式分數",
    ))
    if institutional_link["available"]:
        institution_strength = _number(row.get("institution_score"), 50.0)
        evidence.append(_evidence(
            "tw_official_institution", "台股官方法人", "market",
            institutional_link["direction"], institution_strength,
            _quality_confidence(row, row.get("overall_confidence", 70)),
            str(institutional_link.get("session_date") or updated_at),
            "linked_formal_once",
            "三大法人 " + _share_text(institutional_link.get("total_net_shares"))
            + "；外資 " + _share_text(institutional_link.get("foreign_net_shares"))
            + "；投信 " + _share_text(institutional_link.get("trust_net_shares"))
            + "；自營商 " + _share_text(institutional_link.get("dealer_net_shares"))
            + "。已在正式 V6 單次計入，中央不重複加分。",
            affects_decision=True,
            provenance=str(institutional_link.get("source") or "TWSE/TPEx"),
        ))
    elif institutional_link["applicable"]:
        evidence.append(_evidence(
            "tw_official_institution", "台股官方法人", "market", "missing", 0, 0,
            str(institutional_link.get("session_date") or updated_at),
            "coverage_blocked_or_missing", institutional_link["reason"],
            affects_decision=False,
            provenance=str(institutional_link.get("source") or "TWSE/TPEx"),
        ))
    else:
        evidence.append(_evidence(
            "tw_official_institution", "台股官方法人", "market", "missing", 0, 100,
            updated_at, "not_applicable", institutional_link["reason"],
            affects_decision=False, provenance="market_separation_rule",
        ))
    if valuation_ready:
        evidence.append(_evidence(
            "valuation_shadow", "估值風險雷達", "risk",
            "oppose" if (valuation_pressure or 0) >= 65 else "neutral",
            valuation_pressure or 0, 70, str(valuation.get("session_date") or updated_at),
            "shadow_only", str(valuation.get("valuation_pressure_label") or valuation.get("reason") or "估值資料可用"),
        ))
    elif not is_etf:
        valuation_reason = (
            "台股市值／財報單位尚未完成一致化，暫停影響中央判斷"
            if not valuation_unit_valid
            else str((valuation or {}).get("reason") or "估值核心資料不足")
        )
        evidence.append(_evidence(
            "valuation_shadow", "估值風險雷達", "risk", "missing", 0, 0, updated_at,
            "unit_guard" if not valuation_unit_valid else "insufficient", valuation_reason,
            affects_decision=False,
        ))
        data_missing.append("valuation_risk")
    else:
        evidence.append(_evidence(
            "valuation_shadow", "估值風險雷達", "risk", "neutral", 0, 100,
            updated_at, "not_applicable", "ETF 不套用營運公司估值模型",
            affects_decision=False,
        ))
    if inverse_mapping and inverse_candidate:
        inverse_status = str(inverse_candidate.get("status") or "waiting")
        inverse_symbol = str(inverse_mapping.get("inverse_symbol") or "—")
        inverse_name = str(inverse_mapping.get("inverse_name") or inverse_symbol)
        mapping_status = str(inverse_mapping.get("mapping_data_status") or "waiting")
        evidence.append(_evidence(
            "inverse_etf_shadow", "反向 ETF 影子", "risk",
            "oppose" if (inverse_bear_score or 0) >= 60 else "neutral",
            inverse_bear_score or 0,
            70 if inverse_ready else 30,
            str((inverse_candidate.get("evidence") or {}).get("session_date") or updated_at),
            "validated_shadow" if inverse_ready else "collecting_only",
            f"{inverse_name}（{inverse_symbol}）對應；空方壓力 {inverse_bear_score or 0:.1f}；"
            f"狀態 {inverse_status}；配對 {mapping_status}",
            affects_decision=inverse_active,
        ))
    else:
        evidence.append(_evidence(
            "inverse_etf_shadow", "反向 ETF 影子", "risk", "missing", 0, 0,
            updated_at, "insufficient", "沒有可對齊的反向 ETF 映射或市場候選",
            affects_decision=False,
        ))
    if rotation:
        rotation_score = _number(rotation.get("rotation_score"))
        evidence.append(_evidence(
            "rotation_shadow", "族群輪動影子", "market", _direction(rotation_score),
            rotation_score or 0, 45, session_date, "collecting_only",
            f"{row.get('industry') or '未分類'}：{rotation.get('stage_reason') or '仍在累積向前資料'}",
            affects_decision=False,
        ))
    else:
        evidence.append(_evidence(
            "rotation_shadow", "族群輪動影子", "market", "missing", 0, 0, updated_at,
            "insufficient", "目前沒有可對齊的族群輪動樣本", affects_decision=False,
        ))
    if capital_flow:
        currency = "NT$" if str(row.get("market") or "").upper() == "TW" else "US$"
        net_flow = _number(capital_flow.get("net_flow"), 0.0) or 0.0
        flow_label = "淨流入" if net_flow >= 0 else "淨流出"
        evidence.append(_evidence(
            "capital_flow_shadow", "大量買賣資金流", "short", flow_direction,
            flow_confidence, flow_confidence,
            str(capital_flow.get("session_date") or updated_at),
            "validated_closed_session" if flow_ready else "low_quality_observation",
            f"完整收盤品質排名；{flow_label} {currency}{abs(net_flow):,.0f}；"
            f"信心 {flow_confidence:.0f}；持續 {flow_persistence:.0f}%；"
            f"中央短線調整 {flow_adjustment:+.1f} 點。金額只顯示、不參與加分。",
            affects_decision=flow_ready,
        ))
    else:
        evidence.append(_evidence(
            "capital_flow_shadow", "大量買賣資金流", "short", "missing", 0, 0,
            updated_at, "not_linked", "沒有同市場、同交易日的完整收盤品質訊號",
            affects_decision=False,
        ))
    news_available = bool(row.get("news_data_available"))
    news_penalty = _number(row.get("news_penalty"), 0) or 0
    evidence.append(_evidence(
        "verified_news", "已驗證新聞風險", "risk",
        "oppose" if news_penalty > 0 else "neutral" if news_available else "missing",
        min(100, abs(news_penalty) * 10), 100 if row.get("news_verified") else 30,
        str(row.get("news_scanned_at") or updated_at),
        "verified" if row.get("news_verified") else "available" if news_available else "missing",
        str(row.get("news_summary") or row.get("news_risk_level") or "未取得新聞風險資料"),
        affects_decision=news_available,
    ))
    if not news_available:
        data_missing.append("verified_news")

    # Convert every model adapter to one canonical evidence contract.  The
    # source-specific values remain intact, while provenance and symbol keys
    # become machine-verifiable and comparable across all models.
    evidence = [
        make_evidence(
            source_id=item["source_id"],
            source_label=item["source_label"],
            horizon=item["horizon"],
            direction=item["direction"],
            strength=item["strength"],
            confidence=item["confidence"],
            as_of=item.get("as_of"),
            status=item["status"],
            reason=item["reason"],
            affects_decision=item["affects_decision"],
            symbol=symbol,
            market=str(row.get("market") or ""),
            provenance=item.get("provenance") or item["source_id"],
        )
        for item in evidence
    ]

    conflicts: list[dict[str, Any]] = []
    if data_block and short_score >= 60:
        conflicts.append({
            "code": "positive_signal_vs_data_block",
            "severity": "hard",
            "sources": ["short_plan", "market_contract"],
            "reason": "模型訊號偏多，但交易資料契約或風控條件不完整",
            "resolution_rule": "資料完整性優先，不提供進場判斷",
            "resolution": "data_insufficient",
        })
    if risk_block and not data_block and short_score >= 60:
        conflicts.append({
            "code": "positive_signal_vs_risk_block",
            "severity": "hard",
            "sources": ["short_plan", "trade_guard"],
            "reason": "模型訊號偏多，但已觸發可驗證的交易風險條件",
            "resolution_rule": "風險條件優先；資料仍保留，中央結論改為暫不考慮",
            "resolution": "avoid",
        })
    if valuation_ready and (valuation_pressure or 0) >= 65 and short_score >= 65:
        conflicts.append({
            "code": "trend_vs_valuation",
            "severity": "medium",
            "sources": ["short_plan", "valuation_shadow"],
            "reason": "短線動能偏強，但估值風險偏高",
            "resolution_rule": "保留短線觀察；6個月判斷降級，兩個時段不互相覆蓋",
            "resolution": "short_watch_long_wait",
        })
        if long_code == "can_scale":
            long_code = "wait_pullback"
    if inverse_active and (inverse_bear_score or 0) >= 75 and short_code in ("can_scale", "wait_pullback"):
        conflicts.append({
            "code": "positive_signal_vs_validated_inverse_risk",
            "severity": "medium",
            "sources": ["short_plan", "inverse_etf_shadow"],
            "reason": "正向個股訊號與已通過驗證的反向 ETF 空方壓力衝突",
            "resolution_rule": "只降低中央影子分數並等待確認，不回寫正式排名",
            "resolution": "wait_for_confirmation",
        })
        if short_code == "can_scale":
            short_code = "wait_pullback"
    if short_code in ("can_scale", "wait_pullback") and long_code in ("avoid", "data_insufficient"):
        conflicts.append({
            "code": "horizon_disagreement",
            "severity": "medium",
            "sources": ["short_plan", "long_6m"],
            "reason": "短線與6個月方向不一致",
            "resolution_rule": "不合併投票；分時段顯示並以較保守方案作中央摘要",
            "resolution": "separate_horizons",
        })
    if news_penalty > 0 and row.get("news_verified"):
        conflicts.append({
            "code": "verified_material_risk",
            "severity": "hard",
            "sources": ["verified_news", "formal_v6"],
            "reason": "存在已驗證重大負面事件",
            "resolution_rule": "已驗證重大風險優先於正向訊號",
            "resolution": "avoid",
        })
        short_code = medium_code = long_code = "avoid"

    if risk_block and not data_block:
        short_code = medium_code = long_code = "avoid"

    codes = [short_code, medium_code, long_code]
    if data_block:
        final_code = "data_insufficient"
        final_reason = "缺少現價或市場資料契約無效，中央層暫不判斷"
    elif risk_block:
        final_code = "avoid"
        final_reason = str(row.get("trade_guard_reason") or "已觸發交易風險條件，暫不考慮")
    elif "avoid" in codes:
        final_code = "avoid"
        final_reason = "至少一個獨立時段或重大風險不合格，採保守結論"
    elif "wait_pullback" in codes:
        final_code = "wait_pullback"
        final_reason = "模型有支持證據，但買點或跨時段條件尚未同時成熟"
    elif all(code == "can_scale" for code in codes):
        final_code = "can_scale"
        final_reason = "三個獨立時段均達條件；仍需由你確認價格與風險"
    else:
        final_code = "watch"
        final_reason = "證據未形成一致進場條件，先列入觀察"
    confidence = round(min(short_confidence, medium_conf, long_conf), 1)
    if data_missing:
        confidence = max(0.0, round(confidence - min(25, len(set(data_missing)) * 5), 1))

    horizons = {
        "short": {
            "label": "1～5 日",
            "recommendation": short_code,
            "action": _action_label(short_code),
            "score": short_score,
            "confidence": short_confidence,
            "entry_low": _number(row.get("short_term_entry_low")),
            "entry_high": _number(row.get("short_term_entry_high")),
            "stop": _number(row.get("short_term_stop")),
            "target1": _number(row.get("short_term_target1")),
            "target2": _number(row.get("short_term_target2")),
            "entry_rule": "進入買進區後，等待15～30分鐘量價轉強且支撐未破",
            "exit_rule": "跌破停損價退出；第一目標先分批，第二目標再分批",
            "reason": row.get("short_term_reason") or row.get("short_term_status"),
        },
        "medium": {
            "label": "45 日",
            "recommendation": medium_code,
            "action": _action_label(medium_code),
            "score": medium_score,
            "confidence": medium_conf,
            "entry_low": _number(row.get("mid_long_batch1_low")),
            "entry_high": _number(row.get("mid_long_batch1_high")),
            "stop": _number(row.get("mid_long_stop")),
            "target1": _number(row.get("mid_long_target1")),
            "target2": _number(row.get("mid_long_target2")),
            "entry_rule": "進入買進區且中央結論仍合格，才分批評估",
            "exit_rule": "跌破停損價退出；目標價分批獲利，不一次押滿",
            "reason": "中長線原始計畫＋45日技術與籌碼拆分",
        },
        "long": {
            "label": "6 個月",
            "recommendation": long_code,
            "action": _action_label(long_code),
            "score": long_score,
            "confidence": long_conf,
            "entry_low": _number(row.get("mid_long_batch2_low") or row.get("mid_long_batch1_low")),
            "entry_high": _number(row.get("mid_long_batch2_high") or row.get("mid_long_batch1_high")),
            "stop": _number(row.get("mid_long_stop")),
            "target1": _number(row.get("mid_long_target2") or row.get("mid_long_target1")),
            "target2": None,
            "entry_rule": "只在長期條件與估值風險都合格時分批評估",
            "exit_rule": "基本面失效或跌破停損退出；達長期目標分批獲利",
            "reason": "中長線原始計畫＋6個月財務、成長與估值拆分",
        },
    }
    for plan in horizons.values():
        plan["execution"] = _execution_state(plan, price)
    return {
        "symbol": symbol,
        "name": row.get("name") or symbol,
        "market": row.get("market"),
        "asset_type": row.get("type"),
        "industry": row.get("industry"),
        "price": price,
        "session_date": row.get("official_session_date"),
        "formal_rank": row.get("overall_rank") or row.get("rank"),
        "formal_score": _number(row.get("overall_ranking_score") or row.get("score")),
        "formal_ranking_unchanged": True,
        "shadow_baseline": shadow_baseline,
        "institutional_link": institutional_link,
        "inverse_shadow": {
            "group": (inverse_mapping or {}).get("group"),
            "inverse_symbol": (inverse_mapping or {}).get("inverse_symbol"),
            "inverse_name": (inverse_mapping or {}).get("inverse_name"),
            "mapping_strength": (inverse_mapping or {}).get("mapping_strength"),
            "mapping_quality_score": (inverse_mapping or {}).get("mapping_quality_score"),
            "bear_score": inverse_bear_score,
            "status": (inverse_candidate or {}).get("status"),
            "validated_for_decision": inverse_ready,
            "affects_central_decision": inverse_active,
            "affects_formal_ranking": False,
        },
        "capital_flow_shadow": {
            "linked": bool(capital_flow),
            "validated_for_decision": flow_ready,
            "direction": flow_direction,
            "confidence": flow_confidence if capital_flow else None,
            "persistence_pct": flow_persistence if capital_flow else None,
            "net_flow": _number((capital_flow or {}).get("net_flow")),
            "short_adjustment_points": flow_adjustment,
            "amount_affects_decision": False,
            "affects_formal_ranking": False,
        },
        "horizons": horizons,
        "evidence": evidence,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "resolved_conflict_count": len(conflicts),
        "unresolved_conflict_count": 0,
        "core_data_missing": sorted(set(
            item for item in ("current_price" if not price or price <= 0 else None,
                              "market_contract" if not market_valid else None)
            if item
        )),
        "data_missing": sorted(set(str(item) for item in data_missing if item)),
        "data_quality": row.get("overall_data_quality") or row.get("market_data_quality") or "未標示",
        "risk_blocks": [row.get("trade_guard_reason")] if row.get("trade_guard_blocked") else [],
        "final": {
            "recommendation": final_code,
            "action": _action_label(final_code),
            "confidence": confidence,
            "reason": final_reason,
            "position_guidance": "只供研究與人工決定；不會自動下單",
        },
        "user_choices": ["watch", "wait_entry", "skip"],
    }


def update_decision_hub(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
    institution_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate the hub without mutating ``rows`` or formal report files."""
    reports_dir = Path(reports_dir)
    frozen_rows = copy.deepcopy(rows)
    institution_status = _resolved_institution_status(
        frozen_rows, institution_status
    )
    source_reports = {
        "valuation": _read_json(reports_dir / "valuation_risk_shadow.json"),
        "rotation": _read_json(reports_dir / "market_rotation_shadow.json"),
        "inverse": _read_json(reports_dir / "inverse_etf_shadow.json"),
        "inverse_database": _read_json(reports_dir / "inverse_etf_database.json"),
        "accuracy": _read_json(reports_dir / "accuracy.json"),
        "holding": _read_json(reports_dir / "holding_simulation.json"),
        "million": _read_json(reports_dir / "million_simulation.json"),
        "system_guard": _read_json(reports_dir / "system_guard.json"),
        "stockq": _read_json(reports_dir / "stockq_market_context.json"),
        "validation_60d": _read_json(reports_dir / "validation_60d.json"),
        "graduation": _read_json(reports_dir / "model_graduation.json"),
        "tw_financial": _read_json(reports_dir / "tw_financial_official_cache.json"),
        "capital_flow": _read_json(reports_dir / "capital_flow_daily.json"),
        "prediction_engine": _load_prediction_engine_contract(reports_dir),
    }
    news_cache = _read_json(reports_dir / "news_risk_cache.json") or {}
    news_by_symbol = (
        news_cache.get("symbols")
        if isinstance(news_cache.get("symbols"), dict)
        else {}
    )
    valuation_by_symbol = _valuation_index(source_reports["valuation"])
    rotation_by_sector = _rotation_index(source_reports["rotation"])
    inverse_by_symbol, inverse_by_group = _inverse_indexes(
        source_reports["inverse_database"], source_reports["inverse"]
    )
    capital_flow_by_symbol = _capital_flow_index(source_reports["capital_flow"])
    prediction_by_symbol = _prediction_engine_index(source_reports["prediction_engine"])
    decisions = []
    for row in frozen_rows:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        decision_row = dict(row)
        cached_news = news_by_symbol.get(str(row.get("symbol") or ""))
        if isinstance(cached_news, dict):
            decision_row.update(cached_news)
        symbol_key = str(row.get("symbol") or "").upper()
        inverse_mapping = inverse_by_symbol.get(symbol_key)
        inverse_group = (inverse_mapping or {}).get("group")
        market = str(row.get("market") or "")
        decisions.append(_build_decision(
            decision_row,
            valuation=valuation_by_symbol.get(symbol_key),
            rotation=rotation_by_sector.get(f"{row.get('market')}:{row.get('industry')}"),
            inverse_mapping=inverse_mapping,
            inverse_candidate=inverse_by_group.get(f"{market}:{inverse_group}"),
            inverse_ready=_inverse_market_ready(source_reports["inverse"], market),
            capital_flow=capital_flow_by_symbol.get((
                market.upper(),
                str(row.get("official_session_date") or ""),
                symbol_key,
            )),
            institution_eligible=bool(
                institution_status.get("ranking_eligible")
                and institution_status.get("ai_eligible")
            ),
            updated_at=updated_at,
        ))
    decisions.sort(key=lambda item: (
        str(item.get("market") or ""),
        item.get("formal_rank") if isinstance(item.get("formal_rank"), (int, float)) else 999999,
        str(item.get("symbol") or ""),
    ))
    for item in decisions:
        engine_row = prediction_by_symbol.get((
            str(item.get("market") or "").upper(),
            str(item.get("symbol") or "").upper(),
        ))
        item["prediction_engine"] = engine_row or {
            "symbol": item.get("symbol"),
            "market": item.get("market"),
            "status": "waiting_for_completed_market_checkpoint",
            "horizons": {},
        }
    # Build the owner's requested next-session ranking as a separate,
    # forward-only layer.  It receives the full fixed universe plus the
    # already-normalized shadow evidence, but cannot mutate formal V6 rows or
    # any existing five-day/60-session ledger.
    try:
        next_session_shadow = update_next_session_ranking(
            reports_dir,
            frozen_rows,
            decisions,
            period=period,
            updated_at=updated_at,
            intraday=intraday,
        )
    except Exception:  # noqa: BLE001 - isolated research must not block hub
        logging.exception("下一交易日隔離排名失敗；正式排名與中央原結論繼續")
        next_session_shadow = {
            "status": "error",
            "model_version": "NEXT-SESSION-RANKING-V1-SHADOW",
            "summary": {},
            "groups": {},
            "validation": {},
        }
    next_by_symbol = {
        str(item.get("symbol") or ""): item
        for group in (next_session_shadow.get("groups") or {}).values()
        for item in (group.get("forecast") or [])
        if isinstance(item, dict) and item.get("symbol")
    }
    for item in decisions:
        forecast = next_by_symbol.get(str(item.get("symbol") or ""))
        if forecast:
            item["next_session_prediction"] = forecast
        else:
            item["next_session_prediction"] = {
                "symbol": item.get("symbol"),
                "direction": "WAITING",
                "buyability_status": "data_insufficient",
                "formal_ranking_unchanged": True,
                "places_orders": False,
            }
    comprehensive_shadow = update_comprehensive_shadow_ranking(
        reports_dir,
        decisions,
        period=period,
        updated_at=updated_at,
        intraday=intraday,
    )
    try:
        from model_graduation import update_model_graduation
        from validation_60d import update_validation_60d

        source_reports["validation_60d"] = update_validation_60d(
            reports_dir, updated_at=updated_at
        )
        source_reports["graduation"] = update_model_graduation(
            reports_dir, updated_at=updated_at
        )
    except Exception:  # noqa: BLE001 - shadow graduation must not block the hub
        pass
    portfolio = build_portfolio_control(decisions)
    for item in decisions:
        item["portfolio"] = portfolio["by_symbol"].get(item["symbol"], {})
    unified_evidence = build_unified_evidence_report(decisions, updated_at=updated_at)
    evidence_files = []
    evidence_rows = unified_evidence.get("evidence", [])
    for offset in range(0, len(evidence_rows), EVIDENCE_CHUNK_SIZE):
        chunk_number = offset // EVIDENCE_CHUNK_SIZE + 1
        filename = f"unified_evidence_{chunk_number:02d}.json"
        _write_json(reports_dir / filename, {
            "schema_version": unified_evidence["schema_version"],
            "updated_at": updated_at,
            "chunk": chunk_number,
            "evidence": evidence_rows[offset:offset + EVIDENCE_CHUNK_SIZE],
        })
        evidence_files.append(filename)
    for stale_path in reports_dir.glob("unified_evidence_[0-9][0-9].json"):
        if stale_path.name not in evidence_files:
            stale_path.unlink()
    unified_evidence_index = {
        key: value for key, value in unified_evidence.items() if key != "evidence"
    }
    unified_evidence_index["evidence_files"] = evidence_files
    _write_json(reports_dir / "unified_evidence.json", unified_evidence_index)
    missing_sources = [name for name, report in source_reports.items() if report is None]
    summary = {
        "decision_count": len(decisions),
        "conflict_count": sum(1 for item in decisions if item["conflict_count"]),
        "detected_conflict_count": sum(1 for item in decisions if item["conflict_count"]),
        "resolved_conflict_count": sum(
            1 for item in decisions if item["resolved_conflict_count"]
        ),
        "unresolved_conflict_count": sum(
            1 for item in decisions if item["unresolved_conflict_count"]
        ),
        "data_insufficient_count": sum(
            1 for item in decisions if item["final"]["recommendation"] == "data_insufficient"
        ),
        "core_data_complete_count": sum(
            1 for item in decisions if not item["core_data_missing"]
        ),
        "risk_blocked_count": sum(1 for item in decisions if item["risk_blocks"]),
        "news_coverage_count": sum(
            1 for item in decisions if "verified_news" not in item["data_missing"]
        ),
        "inverse_linked_count": sum(
            1 for item in decisions if (item.get("inverse_shadow") or {}).get("group")
        ),
        "inverse_validated_count": sum(
            1 for item in decisions
            if (item.get("inverse_shadow") or {}).get("validated_for_decision")
        ),
        "capital_flow_linked_count": sum(
            1 for item in decisions
            if (item.get("capital_flow_shadow") or {}).get("linked")
        ),
        "capital_flow_active_count": sum(
            1 for item in decisions
            if (item.get("capital_flow_shadow") or {}).get("validated_for_decision")
        ),
        "institution_linked_count": sum(
            1 for item in decisions
            if (item.get("institutional_link") or {}).get("available")
        ),
        "institution_linked_by_market": {
            market: sum(
                1 for item in decisions
                if item.get("market") == market
                and (item.get("institutional_link") or {}).get("available")
            )
            for market in ("TW", "US")
        },
        "by_recommendation": {
            code: sum(1 for item in decisions if item["final"]["recommendation"] == code)
            for code in ("can_scale", "wait_pullback", "watch", "avoid", "data_insufficient")
        },
        "by_market": {
            market: sum(1 for item in decisions if item.get("market") == market)
            for market in ("TW", "US")
        },
    }
    allocatable_count = sum(
        1 for item in decisions
        if any((entry or {}).get("status") == "allocatable" for entry in item["portfolio"].values())
    )
    waiting_count = sum(
        1 for item in decisions
        if any((entry or {}).get("status") == "reserved_waiting_entry" for entry in item["portfolio"].values())
    )
    if allocatable_count:
        single_answer = {
            "code": "review_allocations",
            "headline": f"有 {allocatable_count} 檔進入部位評估",
            "detail": "依短、中、長期分開配置；下單前仍需人工確認價格、停損與總曝險。",
        }
    elif waiting_count:
        single_answer = {
            "code": "wait_for_entry",
            "headline": "目前不直接進場，等待買點",
            "detail": f"有 {waiting_count} 檔保留觀察，其餘維持現金；等待條件不視為已投資。",
        }
    else:
        single_answer = {
            "code": "hold_cash",
            "headline": "目前沒有符合部位條件的標的",
            "detail": "維持現金，不因模型資料不足或衝突而勉強建立部位。",
        }
    next_session_answer = {
        "status": next_session_shadow.get("status") or "collecting",
        "model_version": next_session_shadow.get("model_version"),
        "headline": "下一交易日預判仍為隔離影子，不取代正式V6",
        "groups": {
            group_name: [
                {
                    "rank": item.get("buyability_rank"),
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "direction": item.get("direction"),
                    "up_probability_estimate_pct": item.get("up_probability_estimate_pct"),
                    "buyability_score": item.get("buyability_score"),
                    "buyability_status": item.get("buyability_status"),
                }
                for item in (group_payload.get("buyable") or [])[:10]
            ]
            for group_name, group_payload in (next_session_shadow.get("groups") or {}).items()
        },
        "formal_ranking_unchanged": True,
        "automatic_orders": False,
    }
    prediction_engine_answer = _prediction_engine_answer(
        source_reports["prediction_engine"]
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "mode": "central_ai_decision_hub",
        "updated_at": updated_at,
        "period": period,
        "run_mode": "intraday_refresh" if intraday else "scheduled_report",
        "status": "warning" if missing_sources else "ready",
        "policy": dict(POLICY),
        "decision_rules": [
            "資料契約與重大風險優先，缺資料不推測",
            "下一交易日預判使用完整固定候選池；禁止先篩當日上漲股，當日漲幅不直接推高或壓低明日機率；趨勢、量價與資金流若獨立支持續漲仍可入榜，追高／落刀風險只調整可買性",
            "獨立預判引擎按下一日、5日、14日下跌、1個月下跌、45日與6個月分開運算；AI中央只讀其結果，不讀私有資料庫也不改寫模型答案",
            "十個隔日影子模型依趨勢、量能資金、環境風險、進場反轉與平衡動能五個證據家族各計一次，避免中央重複投票",
            "1～5日、45日、6個月分開判斷，不以多數決互相覆蓋",
            "未完成向前驗證的影子模型只能提供證據，不能提高正式排名",
            "台股官方法人覆蓋達95%才連動；已在正式V6單次計入，中央不重複加分，美股永不套用台股法人",
            "大量買賣依原訊號品質連動；淨流金額只顯示，完整收盤且品質達標才以最多±3點影響短線中央判斷",
            "同來源、同標的、同時段、同交易日證據只計一次；族群輪動中的資金流不再重複加權",
            "短線強但估值過高時，保留短線觀察並降低6個月判斷",
            "盤中不抓、不補、不結算正式價格；收盤後才更新同交易日資料",
            "StockQ只在收盤後補主要來源缺少的市場指標，不覆蓋個股資料、不直接改分",
            "部位控制在中央結論後執行；風險擋下與資料不足一律配置為零",
            "模型畢業結論自動產生，但升級、改權重與合併仍需人工決定",
            "最終按鈕只保存你的人工選擇，不連券商、不下單",
        ],
        "source_status": {
            "news_full_universe": {
                "available": bool(news_by_symbol),
                "updated_at": news_cache.get("updated_at"),
                "symbols": len(news_by_symbol),
            },
            "tw_institutional_official": {
                "available": bool((institution_status or {}).get("ranking_eligible")),
                "updated_at": (institution_status or {}).get("session_date"),
                "coverage_pct": _number((institution_status or {}).get("coverage_pct")),
                "minimum_coverage_pct": _number(
                    (institution_status or {}).get("minimum_coverage_pct"), 95.0
                ),
                "ai_eligible": bool((institution_status or {}).get("ai_eligible")),
                "applies_to": "TW",
                "never_applies_to": "US",
            },
            "comprehensive_shadow": {
                "available": True,
                "updated_at": comprehensive_shadow.get("updated_at"),
                "model_version": comprehensive_shadow.get("model_version"),
                "formal_ranking_unchanged": True,
            },
            "next_session_shadow": {
                "available": next_session_shadow.get("status") != "error",
                "updated_at": next_session_shadow.get("updated_at"),
                "model_version": next_session_shadow.get("model_version"),
                "formal_ranking_unchanged": True,
                "future_outcomes_forbidden": True,
            },
            **{
            name: {
                "available": report is not None,
                "updated_at": (report or {}).get("updated_at"),
            }
            for name, report in source_reports.items()
            },
        },
        "missing_sources": missing_sources,
        "readiness": _model_readiness(source_reports, institution_status),
        "comprehensive_shadow": {
            "report": "comprehensive_shadow_ranking.json",
            "history": "comprehensive_shadow_history.json",
            "status": comprehensive_shadow.get("status"),
            "model_version": comprehensive_shadow.get("model_version"),
            "validation": comprehensive_shadow.get("validation"),
            "markets": {
                market: {
                    "session_date": (comprehensive_shadow.get("markets", {}).get(market) or {}).get("session_date"),
                    "ranking_count": (comprehensive_shadow.get("markets", {}).get(market) or {}).get("ranking_count", 0),
                }
                for market in ("TW", "US")
            },
        },
        "single_answer": single_answer,
        "prediction_engine_answer": prediction_engine_answer,
        "prediction_engine": {
            "report": "prediction_engine.json",
            "status": (source_reports["prediction_engine"] or {}).get("status"),
            "model_version": (source_reports["prediction_engine"] or {}).get("model_version"),
            "market_status": (source_reports["prediction_engine"] or {}).get("market_status") or {},
            "run_summary": (source_reports["prediction_engine"] or {}).get("run_summary") or {},
            "database_health": (source_reports["prediction_engine"] or {}).get("database") or {},
            "formal_v6_unchanged": True,
            "automatic_orders": False,
        },
        "next_session_answer": next_session_answer,
        "next_session_shadow": {
            "report": "next_session_shadow_ranking.json",
            "history": "next_session_ranking_history.json",
            "status": next_session_shadow.get("status"),
            "model_version": next_session_shadow.get("model_version"),
            "summary": next_session_shadow.get("summary") or {},
            "validation": next_session_shadow.get("validation") or {},
            "formal_ranking_unchanged": True,
        },
        "portfolio_control": {
            key: value for key, value in portfolio.items() if key != "by_symbol"
        },
        "unified_evidence": {
            "status": unified_evidence["status"],
            "evidence_count": unified_evidence["evidence_count"],
            "invalid_count": unified_evidence["invalid_count"],
            "integrity_sha256": unified_evidence["integrity_sha256"],
            "report": "unified_evidence.json",
        },
        "summary": summary,
        "decisions": decisions,
        "disclaimer": "資料整理、衝突說明與風險輔助，不保證獲利，也不是代客下單建議。",
    }
    decision_files = []
    for offset in range(0, len(decisions), CHUNK_SIZE):
        chunk = decisions[offset:offset + CHUNK_SIZE]
        chunk_number = offset // CHUNK_SIZE + 1
        filename = f"decision_hub_{chunk_number:02d}.json"
        _write_json(reports_dir / filename, {
            "schema_version": SCHEMA_VERSION,
            "model_version": MODEL_VERSION,
            "updated_at": updated_at,
            "chunk": chunk_number,
            "decisions": chunk,
        })
        decision_files.append(filename)
    for stale_path in reports_dir.glob("decision_hub_[0-9][0-9].json"):
        if stale_path.name not in decision_files:
            stale_path.unlink()
    index_payload = {key: value for key, value in payload.items() if key != "decisions"}
    index_payload["decision_files"] = decision_files
    _write_json(reports_dir / "decision_hub.json", index_payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Central AI Decision Hub report")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    analysis = _read_json(reports_dir / "all_analysis.json") or {}
    rows = analysis.get("data") if isinstance(analysis.get("data"), list) else []
    payload = update_decision_hub(
        reports_dir,
        rows,
        period=str(analysis.get("period") or "evening"),
        updated_at=str(analysis.get("updated_at") or ""),
        intraday=analysis.get("run_mode") == "intraday_refresh",
        institution_status=(
            analysis.get("institution_status")
            if isinstance(analysis.get("institution_status"), dict) else None
        ),
    )
    print(
        f"Decision hub: {payload['summary']['decision_count']} rows, "
        f"{payload['summary']['conflict_count']} conflicts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
