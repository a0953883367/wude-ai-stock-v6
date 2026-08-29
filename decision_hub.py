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
from pathlib import Path
from typing import Any

from evidence_contract import build_unified_evidence_report, make_evidence
from portfolio_control import build_portfolio_control


SCHEMA_VERSION = 2
MODEL_VERSION = "CENTRAL-DECISION-HUB-V2"
CHUNK_SIZE = 50
EVIDENCE_CHUNK_SIZE = 400
POLICY = {
    "formal_ranking_locked": True,
    "shadow_models_evidence_only": True,
    "missing_data_never_imputed": True,
    "automatic_weight_changes": False,
    "automatic_orders": False,
    "horizons_separate": True,
    "user_final_decision_required": True,
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


def _model_readiness(reports: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
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
    }


def _build_decision(
    row: dict[str, Any],
    *,
    valuation: dict[str, Any] | None,
    rotation: dict[str, Any] | None,
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
    valuation_ready = bool(valuation and valuation.get("status") == "ready")
    valuation_pressure = _number((valuation or {}).get("valuation_pressure_score"))
    if valuation_ready and valuation_pressure is not None:
        # Risk-only deduction in this downstream decision; formal rank is untouched.
        long_score = round(max(0.0, long_score - max(0.0, valuation_pressure - 55) * 0.20), 1)
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
    if valuation_ready:
        evidence.append(_evidence(
            "valuation_shadow", "估值風險雷達", "risk",
            "oppose" if (valuation_pressure or 0) >= 65 else "neutral",
            valuation_pressure or 0, 70, str(valuation.get("session_date") or updated_at),
            "shadow_only", str(valuation.get("valuation_pressure_label") or valuation.get("reason") or "估值資料可用"),
        ))
    elif not is_etf:
        evidence.append(_evidence(
            "valuation_shadow", "估值風險雷達", "risk", "missing", 0, 0, updated_at,
            "insufficient", str((valuation or {}).get("reason") or "估值核心資料不足"),
            affects_decision=False,
        ))
        data_missing.append("valuation_risk")
    else:
        evidence.append(_evidence(
            "valuation_shadow", "估值風險雷達", "risk", "neutral", 0, 100,
            updated_at, "not_applicable", "ETF 不套用營運公司估值模型",
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
            provenance=item["source_id"],
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
) -> dict[str, Any]:
    """Generate the hub without mutating ``rows`` or formal report files."""
    reports_dir = Path(reports_dir)
    frozen_rows = copy.deepcopy(rows)
    source_reports = {
        "valuation": _read_json(reports_dir / "valuation_risk_shadow.json"),
        "rotation": _read_json(reports_dir / "market_rotation_shadow.json"),
        "inverse": _read_json(reports_dir / "inverse_etf_shadow.json"),
        "accuracy": _read_json(reports_dir / "accuracy.json"),
        "holding": _read_json(reports_dir / "holding_simulation.json"),
        "million": _read_json(reports_dir / "million_simulation.json"),
        "system_guard": _read_json(reports_dir / "system_guard.json"),
        "stockq": _read_json(reports_dir / "stockq_market_context.json"),
        "validation_60d": _read_json(reports_dir / "validation_60d.json"),
        "graduation": _read_json(reports_dir / "model_graduation.json"),
        "tw_financial": _read_json(reports_dir / "tw_financial_official_cache.json"),
    }
    news_cache = _read_json(reports_dir / "news_risk_cache.json") or {}
    news_by_symbol = (
        news_cache.get("symbols")
        if isinstance(news_cache.get("symbols"), dict)
        else {}
    )
    valuation_by_symbol = _valuation_index(source_reports["valuation"])
    rotation_by_sector = _rotation_index(source_reports["rotation"])
    decisions = []
    for row in frozen_rows:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        decision_row = dict(row)
        cached_news = news_by_symbol.get(str(row.get("symbol") or ""))
        if isinstance(cached_news, dict):
            decision_row.update(cached_news)
        decisions.append(_build_decision(
            decision_row,
            valuation=valuation_by_symbol.get(str(row.get("symbol") or "").upper()),
            rotation=rotation_by_sector.get(f"{row.get('market')}:{row.get('industry')}"),
            updated_at=updated_at,
        ))
    decisions.sort(key=lambda item: (
        str(item.get("market") or ""),
        item.get("formal_rank") if isinstance(item.get("formal_rank"), (int, float)) else 999999,
        str(item.get("symbol") or ""),
    ))
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
            "1～5日、45日、6個月分開判斷，不以多數決互相覆蓋",
            "未完成向前驗證的影子模型只能提供證據，不能提高正式排名",
            "短線強但估值過高時，保留短線觀察並降低6個月判斷",
            "StockQ只提供全球市場背景，不覆蓋個股資料、不直接改分",
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
            **{
            name: {
                "available": report is not None,
                "updated_at": (report or {}).get("updated_at"),
            }
            for name, report in source_reports.items()
            },
        },
        "missing_sources": missing_sources,
        "readiness": _model_readiness(source_reports),
        "single_answer": single_answer,
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
    )
    print(
        f"Decision hub: {payload['summary']['decision_count']} rows, "
        f"{payload['summary']['conflict_count']} conflicts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
