"""Forward-only market-rule and sector-rotation shadow experiment.

The production V6 ranking is an input, never an output, of this module.  Each
market uses its own evidence contract, freezes a completed-session sector
snapshot and compares the unchanged baseline TOP10 with a 15% rotation overlay
on the next completed session.  There are deliberately no broker imports.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable


VERSION = 1
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
PICKS = 10
CAPITAL_TWD = 1_000_000
ALLOCATION_TWD = CAPITAL_TWD // PICKS
ROTATION_WEIGHT = 0.15
MIN_SECTOR_MEMBERS = 2
MAX_SNAPSHOTS = 90
MAX_OUTCOMES = 120
ROUND_TRIP_COST_PCT = {"TW": 0.685, "US": 0.20}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number(value: Any, fallback: float = 0.0) -> float:
    number = _finite(value)
    return number if number is not None else fallback


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _is_stock(row: dict[str, Any], market: str) -> bool:
    return (
        str(row.get("market") or "").upper() == market
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    dates = [
        str(row.get("official_session_date") or "")
        for row in rows
        if _is_stock(row, market) and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _completed_checkpoint(market: str, period: str, updated_at: str) -> bool:
    if period != CLOSED_PERIOD[market]:
        return False
    try:
        local_time = datetime.fromisoformat(updated_at.replace("/", "-"))
    except (TypeError, ValueError):
        return False
    return local_time.hour >= 14 if market == "TW" else 5 <= local_time.hour < 12


def _industry(row: dict[str, Any]) -> str:
    return str(row.get("industry") or row.get("theme") or "其他")


def _rank_tier(row: dict[str, Any]) -> int:
    if row.get("trade_guard_blocked") or row.get("market_contract_valid") is False:
        return 0
    explicit = _finite(row.get("short_term_rank_tier"))
    if explicit is not None:
        return max(0, min(2, int(explicit)))
    return 2 if row.get("short_term_eligible") is True else 1


def _base_score(row: dict[str, Any]) -> float:
    return _number(
        row.get("short_term_ranking_score"),
        _number(row.get("short_term_score")),
    )


def _sector_components(
    members: list[dict[str, Any]], market_median: float, market: str
) -> dict[str, float]:
    changes = [_number(row.get("change_pct")) for row in members]
    volumes = [
        _number(row.get("daily_volume_ratio"), _number(row.get("volume_pace"), 1.0))
        for row in members
    ]
    up_ratio = sum(value > 0 for value in changes) / len(changes) * 100
    median_change = float(median(changes))
    median_volume = float(median(volumes))
    volume_confirm = sum(value >= 1.2 for value in volumes) / len(volumes) * 100
    relative_strength = _clamp(50 + (median_change - market_median) * 8)
    volume_score = _clamp(
        _clamp(50 + (median_volume - 1.0) * 35) * 0.55 + volume_confirm * 0.45
    )
    leader_threshold = 3.0 if market == "TW" else 2.5
    leaders = sum(
        change >= leader_threshold and volume >= 1.2
        for change, volume in zip(changes, volumes)
    )
    leadership = _clamp(35 + leaders / len(members) * 100)
    if market == "TW":
        flow_values = [
            _number(row.get("institution_net"), _number(row.get("institution_1d")))
            for row in members
            if row.get("institution_available") is True
        ]
        evidence = (
            sum(value > 0 for value in flow_values) / len(flow_values) * 100
            if flow_values else 0.0
        )
    else:
        growth_values = [
            _number(row.get("growth_score"))
            for row in members if _finite(row.get("growth_score")) is not None
        ]
        positive_revenue = [
            _number(row.get("revenue_yoy_pct")) > 0
            for row in members if _finite(row.get("revenue_yoy_pct")) is not None
        ]
        growth = sum(growth_values) / len(growth_values) if growth_values else 0.0
        revenue = (
            sum(positive_revenue) / len(positive_revenue) * 100
            if positive_revenue else 0.0
        )
        evidence = growth * 0.65 + revenue * 0.35
    return {
        "median_change_pct": round(median_change, 4),
        "up_ratio_pct": round(up_ratio, 2),
        "median_volume_ratio": round(median_volume, 4),
        "volume_confirmation_pct": round(volume_confirm, 2),
        "relative_strength_score": round(relative_strength, 2),
        "breadth_score": round(up_ratio, 2),
        "volume_score": round(volume_score, 2),
        "market_specific_evidence_score": round(_clamp(evidence), 2),
        "leadership_score": round(leadership, 2),
        "leader_count": leaders,
    }


def _rotation_score(components: dict[str, float], market: str) -> float:
    weights = (
        {"relative_strength_score": .25, "breadth_score": .20,
         "volume_score": .20, "market_specific_evidence_score": .20,
         "leadership_score": .15}
        if market == "TW" else
        {"relative_strength_score": .25, "breadth_score": .20,
         "volume_score": .15, "market_specific_evidence_score": .25,
         "leadership_score": .15}
    )
    return round(sum(components[key] * weight for key, weight in weights.items()), 2)


def _stage(
    current: dict[str, Any], previous: dict[str, Any] | None, market: str
) -> tuple[str, str]:
    if previous is None:
        return "collecting", "首個完成交易日只建立基準，不倒推輪動階段"
    score = _number(current.get("rotation_score"))
    prior = _number(previous.get("rotation_score"), 50.0)
    breadth = _number(current.get("up_ratio_pct"))
    volume = _number(current.get("median_volume_ratio"), 1.0)
    change = _number(current.get("median_change_pct"))
    climax_change = 5.0 if market == "TW" else 4.0
    if score >= 80 and breadth >= 80 and volume >= 1.5 and change >= climax_change:
        return "climax", "族群普遍急漲且放量，追高風險升高"
    if prior >= 60 and score < 50 and breadth < 45:
        return "ebb", "前期強勢後廣度與輪動分數同步轉弱"
    if score >= 65 and prior >= 55 and breadth >= 55:
        return "expansion", "連續轉強且上漲家數擴散"
    if score >= 60 and prior < 55 and breadth >= 50:
        return "ignition", "輪動分數由弱轉強，領頭股開始點火"
    return "neutral", "尚未形成可確認的點火、擴散或退潮"


def build_market_snapshot(
    rows: list[dict[str, Any]], market: str,
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one completed-session snapshot without mutating ranking rows."""
    session_date = _session_date(rows, market)
    market_rows = [
        dict(row) for row in rows
        if _is_stock(row, market)
        and str(row.get("official_session_date") or "") == session_date
        and _finite(row.get("change_pct")) is not None
    ]
    if not session_date or len(market_rows) < 20:
        return None
    market_median = float(median(_number(row.get("change_pct")) for row in market_rows))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        grouped[_industry(row)].append(row)
    previous_sectors = {
        str(item.get("industry")): item
        for item in (previous_snapshot or {}).get("sectors") or []
    }
    sectors = []
    for industry, members in grouped.items():
        components = _sector_components(members, market_median, market)
        eligible = len(members) >= MIN_SECTOR_MEMBERS
        sector = {
            "industry": industry,
            "member_count": len(members),
            "eligible": eligible,
            **components,
            "rotation_score": _rotation_score(components, market) if eligible else None,
        }
        stage, reason = _stage(sector, previous_sectors.get(industry), market)
        sector["stage"] = stage if eligible else "insufficient_members"
        sector["stage_reason"] = reason if eligible else "同產業不足2檔，不使用輪動加權"
        sectors.append(sector)
    sectors.sort(key=lambda item: (-_number(item.get("rotation_score"), -1), item["industry"]))
    up_ratio = sum(_number(row.get("change_pct")) > 0 for row in market_rows) / len(market_rows) * 100
    hot = [item for item in sectors if _number(item.get("rotation_score")) >= 65]
    if up_ratio >= 65:
        state = "broad_bull"
        label = "普漲多頭"
    elif up_ratio < 40:
        state = "weak_or_ebb"
        label = "偏弱／退潮"
    elif len(hot) >= 2:
        state = "selective_rotation"
        label = "族群快速輪動"
    else:
        state = "sideways"
        label = "盤整分化"
    snapshot = {
        "market": market,
        "session_date": session_date,
        "universe_scope": "current_analyzed_stock_universe",
        "stock_count": len(market_rows),
        "market_median_change_pct": round(market_median, 4),
        "market_breadth_up_pct": round(up_ratio, 2),
        "market_state": state,
        "market_state_label": label,
        "hot_sector_count": len(hot),
        "sectors": sectors,
    }
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["integrity_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return snapshot


def select_picks(
    rows: list[dict[str, Any]], market: str,
    snapshot: dict[str, Any], *, rotation_overlay: bool,
) -> list[dict[str, Any]]:
    sector_map = {
        str(item.get("industry")): item
        for item in snapshot.get("sectors") or []
    }
    ranked = []
    for source in rows:
        if not _is_stock(source, market):
            continue
        row = dict(source)
        base = _base_score(row)
        sector = sector_map.get(_industry(row)) or {}
        rotation = _finite(sector.get("rotation_score"))
        adjusted = (
            base * (1 - ROTATION_WEIGHT) + rotation * ROTATION_WEIGHT
            if rotation_overlay and rotation is not None else base
        )
        row["_base"] = base
        row["_adjusted"] = round(adjusted, 4)
        row["_rotation"] = rotation
        row["_rotation_stage"] = sector.get("stage")
        ranked.append(row)
    ranked.sort(key=lambda row: (
        -_rank_tier(row), -_number(row.get("_adjusted")),
        -_number(row.get("short_term_score")), str(row.get("symbol") or ""),
    ))
    return [{
        "rank": index,
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "industry": _industry(row),
        "rank_tier": _rank_tier(row),
        "base_score": round(_number(row.get("_base")), 2),
        "shadow_score": round(_number(row.get("_adjusted")), 2),
        "rotation_score": round(_number(row.get("_rotation")), 2) if row.get("_rotation") is not None else None,
        "rotation_stage": row.get("_rotation_stage"),
        "allocation_twd": ALLOCATION_TWD,
    } for index, row in enumerate(ranked[:PICKS], 1)]


def _empty_market(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "status": "waiting_for_first_completed_session",
        "snapshots": [],
        "pending": None,
        "outcomes": [],
        "summary": {},
    }


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "forward_shadow_only",
        "policy": {
            "baseline": "A組＝正式V6短線排序；只讀取、不修改",
            "shadow": "B組＝正式短線分數85%＋同市場族群輪動15%",
            "market_isolation": "台股使用法人／量價；美股使用成長財報代理／量價",
            "review_gate": "5日只查程式、20日初步比較、60日後只標候選",
            "formal_ranking_locked": True,
            "automatic_merge": False,
            "broker_orders": False,
        },
        "markets": {market: _empty_market(market) for market in MARKETS},
    }


def _load(path: Path, updated_at: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty_state(updated_at)
    if state.get("version") != VERSION or not isinstance(state.get("markets"), dict):
        return empty_state(updated_at)
    for market in MARKETS:
        state["markets"].setdefault(market, _empty_market(market))
    return state


def _settle_pending(
    market_state: dict[str, Any], rows: list[dict[str, Any]],
    market: str, session_date: str,
) -> bool:
    pending = market_state.get("pending")
    if not pending or session_date <= str(pending.get("signal_session_date") or ""):
        return False
    attempted_session = str(pending.get("waiting_session_date") or "")
    if attempted_session and session_date > attempted_session:
        market_state.setdefault("outcomes", []).append({
            "status": "data_insufficient",
            "signal_session_date": pending.get("signal_session_date"),
            "session_date": attempted_session,
            "snapshot_integrity_sha256": pending.get("snapshot_integrity_sha256"),
            "missing_symbols": list(pending.get("missing_symbols") or []),
            "reason": "下一完成交易日官方價格未補齊；隔離且不以後續日期代替",
        })
        market_state["outcomes"] = market_state["outcomes"][-MAX_OUTCOMES:]
        market_state["pending"] = None
        return True
    row_map = {
        str(row.get("symbol") or ""): row for row in rows
        if _is_stock(row, market)
        and str(row.get("official_session_date") or "") == session_date
    }
    required = {
        pick["symbol"]
        for model in (pending.get("models") or {}).values()
        for pick in model.get("picks") or []
    }
    missing = sorted(symbol for symbol in required if not (
        symbol in row_map
        and _number(row_map[symbol].get("official_open_price")) > 0
        and _number(row_map[symbol].get("official_close_price")) > 0
    ))
    if missing:
        pending["settlement_status"] = "waiting_for_official_prices"
        pending["waiting_session_date"] = session_date
        pending["missing_symbols"] = missing
        return False
    cost = ROUND_TRIP_COST_PCT[market]
    models = {}
    for key, model in (pending.get("models") or {}).items():
        positions = []
        gross_profit = net_profit = 0.0
        for pick in model.get("picks") or []:
            row = row_map[pick["symbol"]]
            open_price = _number(row.get("official_open_price"))
            close_price = _number(row.get("official_close_price"))
            gross_return = (close_price / open_price - 1) * 100
            net_return = gross_return - cost
            allocation = _number(pick.get("allocation_twd"), ALLOCATION_TWD)
            gross_profit += allocation * gross_return / 100
            net_profit += allocation * net_return / 100
            positions.append({
                **pick, "open_price": round(open_price, 4),
                "close_price": round(close_price, 4),
                "gross_return_pct": round(gross_return, 4),
                "net_return_pct": round(net_return, 4),
            })
        models[key] = {
            "label": model.get("label"), "positions": positions,
            "gross_profit_twd": round(gross_profit, 2),
            "net_profit_twd": round(net_profit, 2),
            "net_return_pct": round(net_profit / CAPITAL_TWD * 100, 4),
        }
    market_state.setdefault("outcomes", []).append({
        "status": "valid",
        "signal_session_date": pending.get("signal_session_date"),
        "session_date": session_date,
        "snapshot_integrity_sha256": pending.get("snapshot_integrity_sha256"),
        "models": models,
        "incremental_net_profit_twd": round(
            _number((models.get("rotation") or {}).get("net_profit_twd"))
            - _number((models.get("baseline") or {}).get("net_profit_twd")), 2
        ),
    })
    market_state["outcomes"] = market_state["outcomes"][-MAX_OUTCOMES:]
    market_state["pending"] = None
    return True


def _summary(market_state: dict[str, Any]) -> dict[str, Any]:
    all_outcomes = market_state.get("outcomes") or []
    outcomes = [item for item in all_outcomes if item.get("status") == "valid"]
    baseline = sum(_number(((item.get("models") or {}).get("baseline") or {}).get("net_profit_twd")) for item in outcomes)
    rotation = sum(_number(((item.get("models") or {}).get("rotation") or {}).get("net_profit_twd")) for item in outcomes)
    days = len(outcomes)
    return {
        "valid_trading_days": days,
        "invalid_trading_days": sum(item.get("status") != "valid" for item in all_outcomes),
        "baseline_net_profit_twd": round(baseline, 2),
        "rotation_net_profit_twd": round(rotation, 2),
        "rotation_incremental_net_profit_twd": round(rotation - baseline, 2),
        "baseline_net_return_pct": round(baseline / CAPITAL_TWD * 100, 4),
        "rotation_net_return_pct": round(rotation / CAPITAL_TWD * 100, 4),
        "review_status": "code_check_only" if days < 20 else "preliminary_review" if days < 60 else "candidate_review",
        "formal_ranking_locked": True,
    }


def update_market_rotation_shadow(
    reports_dir: Path, rows: list[dict[str, Any]], *,
    period: str, updated_at: str, intraday: bool = False,
) -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "market_rotation_shadow.json"
    state = _load(path, updated_at)
    state["updated_at"] = updated_at
    if intraday:
        return state
    for market in MARKETS:
        if not _completed_checkpoint(market, period, updated_at):
            continue
        session_date = _session_date(rows, market)
        if not session_date:
            continue
        market_state = state["markets"][market]
        _settle_pending(market_state, rows, market, session_date)
        snapshots = market_state.get("snapshots") or []
        existing = next((item for item in snapshots if item.get("session_date") == session_date), None)
        if existing is None:
            snapshot = build_market_snapshot(rows, market, snapshots[-1] if snapshots else None)
            if snapshot is None:
                continue
            snapshots.append(snapshot)
            market_state["snapshots"] = snapshots[-MAX_SNAPSHOTS:]
        else:
            snapshot = existing
        if not market_state.get("pending"):
            market_state["pending"] = {
                "signal_session_date": session_date,
                "created_at": updated_at,
                "snapshot_integrity_sha256": snapshot.get("integrity_sha256"),
                "settlement_status": "waiting_next_completed_session",
                "models": {
                    "baseline": {
                        "label": "A｜正式V6基準",
                        "picks": select_picks(rows, market, snapshot, rotation_overlay=False),
                    },
                    "rotation": {
                        "label": "B｜市場規則＋輪動15%",
                        "picks": select_picks(rows, market, snapshot, rotation_overlay=True),
                    },
                },
            }
        market_state["status"] = "collecting_only"
        market_state["summary"] = _summary(market_state)
    tmp = reports_dir / "market_rotation_shadow.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return state
