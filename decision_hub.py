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
                    if current is None or _number(candidate.get("c