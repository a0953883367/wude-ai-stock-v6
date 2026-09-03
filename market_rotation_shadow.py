"""Forward-only market-rule and sector-rotation shadow experiment.

The production V6 ranking is an input, never an output, of this module.  Each
market uses its own evidence contract, freezes a completed-session sector
snapshot and compares the unchanged baseline TOP10 with a 15% rotation overlay
on the next completed session.  There are deliberately no broker imports.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from capital_flow_shadow import normalize_theme


VERSION = 1
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
PICKS = 10
CAPITAL_TWD = 1_000_000
ALLOCATION_TWD = CAPITAL_TWD // PICKS
ROTATION_WEIGHT = 0.15
DAILY_FLOW_WEIGHT = 0.15
MIN_SECTOR_MEMBERS = 2
MIN_ANALYZED_STOCKS = PICKS
MIN_POSITIONS_PER_MODEL = PICKS - 1
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


def _completed_checkpoint(market: str, period: str, _updated_at: str) -> bool:
    # `intraday` is rejected by the caller.  A non-intraday formal report may
    # finish late because upstream providers are slow, so its period and exact
    # official session dates are authoritative; wall-clock hour is not.
    return period == CLOSED_PERIOD[market]


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
    co_movement = sum(change >= 1.0 for change in changes)
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
        "co_movement_count": co_movement,
        "three_stock_co_movement": co_movement >= 3,
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


def _closed_daily_flow(
    daily_flow: dict[str, Any] | None, market: str, session_date: str
) -> dict[str, Any] | None:
    if not isinstance(daily_flow, dict):
        return None
    if (daily_flow.get("policy") or {}).get("intraday_exposed") is not False:
        return None
    sessions = (daily_flow.get("markets") or {}).get(market) or []
    return next((
        item for item in sessions
        if isinstance(item, dict)
        and item.get("session_date") == session_date
        and item.get("closed") is True
        and item.get("complete") is True
        and item.get("session_scope") == "regular_hours_only"
    ), None)


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
    daily_flow: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one completed-session snapshot without mutating ranking rows."""
    session_date = _session_date(rows, market)
    market_rows = [
        dict(row) for row in rows
        if _is_stock(row, market)
        and str(row.get("official_session_date") or "") == session_date
        and _finite(row.get("change_pct")) is not None
    ]
    if not session_date or len(market_rows) < MIN_ANALYZED_STOCKS:
        return None
    market_median = float(median(_number(row.get("change_pct")) for row in market_rows))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        grouped[_industry(row)].append(row)
    previous_sectors = {
        str(item.get("industry")): item
        for item in (previous_snapshot or {}).get("sectors") or []
    }
    flow_session = _closed_daily_flow(daily_flow, market, session_date)
    flow_themes = {
        normalize_theme(item.get("theme")): item
        for item in (flow_session or {}).get("themes") or []
        if isinstance(item, dict)
    }
    raw_symbol_flows = (flow_session or {}).get("symbol_flows")
    symbol_flow_available = isinstance(raw_symbol_flows, list)
    flow_symbols = {
        str(item.get("symbol") or "").upper(): item
        for item in (raw_symbol_flows or [])
        if isinstance(item, dict) and item.get("symbol")
    }
    sectors = []
    for industry, members in grouped.items():
        components = _sector_components(members, market_median, market)
        eligible = len(members) >= MIN_SECTOR_MEMBERS
        base_rotation = _rotation_score(components, market) if eligible else None
        flow_score = None
        flow_member_count = 0
        flow_link_basis = "none"
        if symbol_flow_available:
            member_flows = [
                flow_symbols[symbol]
                for symbol in {
                    str(member.get("symbol") or "").upper() for member in members
                }
                if symbol in flow_symbols
            ]
            directional_flows = [
                item for item in member_flows if _number(item.get("net_flow")) != 0
            ]
            flow_member_count = len(directional_flows)
            flow_link_basis = "member_symbols"
            flow_buy = sum(_number(item.get("buy_value")) for item in member_flows)
            flow_sell = sum(_number(item.get("sell_value")) for item in member_flows)
            flow_directional = flow_buy + flow_sell
            if eligible and flow_member_count >= MIN_SECTOR_MEMBERS and flow_directional > 0:
                positive_breadth = (
                    sum(_number(item.get("net_flow")) > 0 for item in directional_flows)
                    / flow_member_count * 100
                )
                flow_score = _clamp(
                    flow_buy / flow_directional * 100 * 0.55
                    + positive_breadth * 0.45
                )
        else:
            # Backward compatibility for already-frozen close summaries made
            # before compact per-symbol flow became available.  New snapshots
            # always use exact member symbols and never depend on label aliases.
            theme_flow = flow_themes.get(normalize_theme(industry))
            flow_member_count = int(
                _number((theme_flow or {}).get("positive_symbols"))
                + _number((theme_flow or {}).get("negative_symbols"))
            )
            if theme_flow:
                flow_link_basis = "legacy_theme"
            if eligible and theme_flow and flow_member_count >= MIN_SECTOR_MEMBERS:
                positive_breadth = (
                    _number(theme_flow.get("positive_symbols"))
                    / flow_member_count * 100
                    if flow_member_count else 50.0
                )
                flow_score = _clamp(
                    _number(theme_flow.get("buy_ratio_pct"), 50.0) * 0.55
                    + positive_breadth * 0.45
                )
        sector = {
            "industry": industry,
            "member_count": len(members),
            "eligible": eligible,
            **components,
            "base_rotation_score": base_rotation,
            "daily_flow_score": round(flow_score, 2) if flow_score is not None else None,
            "daily_flow_status": "linked" if flow_score is not None else "not_available",
            "daily_flow_member_count": flow_member_count,
            "daily_flow_expected_members": len(members),
            "daily_flow_link_basis": flow_link_basis,
            "rotation_score": round(
                base_rotation * (1 - DAILY_FLOW_WEIGHT) + flow_score * DAILY_FLOW_WEIGHT, 2
            ) if base_rotation is not None and flow_score is not None else base_rotation,
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
        "daily_flow": {
            "status": "linked" if flow_session is not None else "not_available",
            "session_date": flow_session.get("session_date") if flow_session else None,
            "source": flow_session.get("source") if flow_session else None,
            "weight_within_rotation_score": DAILY_FLOW_WEIGHT,
            "intraday_used": False,
        },
        "sectors": sectors,
    }
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["integrity_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return snapshot


def select_picks(
    rows: list[dict[str, Any]], market: str,
    snapshot: dict[str, Any], *, rotation_overlay: bool,
    qualification_mode: str | None = None,
) -> list[dict[str, Any]]:
    sector_map = {
        str(item.get("industry")): item
        for item in snapshot.get("sectors") or []
    }
    ranked = []
    snapshot_date = str(snapshot.get("session_date") or "")
    for source in rows:
        if (
            not _is_stock(source, market)
            or str(source.get("official_session_date") or "") != snapshot_date
        ):
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
        row["_tw_setup"] = _tw_setup_qualification(row, sector) if market == "TW" else None
        ranked.append(row)
    if qualification_mode == "strict_5of5":
        ranked = [row for row in ranked if (row.get("_tw_setup") or {}).get("strict_5of5") is True]
    elif qualification_mode == "practical_4of5":
        ranked = [row for row in ranked if (row.get("_tw_setup") or {}).get("practical_4of5") is True]
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
        "tw_five_condition": row.get("_tw_setup"),
        "signal_close_price": _finite(row.get("official_close_price")),
        "allocation_twd": ALLOCATION_TWD,
    } for index, row in enumerate(ranked[:PICKS], 1)]


def _tw_setup_qualification(
    row: dict[str, Any], sector: dict[str, Any]
) -> dict[str, Any]:
    """Transparent TW-only five-condition setup; never changes V6 scores."""
    change = _number(row.get("change_pct"))
    volume = _number(
        row.get("daily_volume_ratio"), _number(row.get("volume_pace"), 1.0)
    )
    pattern = " ".join((
        str(row.get("kline_pattern") or ""),
        str(row.get("volume_price_pattern") or ""),
    ))
    distribution = "上影" in pattern or "開高走低" in pattern or "爆量不漲" in pattern
    sector_strong = bool(
        sector.get("eligible")
        and _number(sector.get("rotation_score")) >= 60
        and _number(sector.get("relative_strength_score")) >= 55
        and sector.get("stage") != "ebb"
    )
    co_movement = bool(_number(sector.get("co_movement_count")) >= 3)
    volume_breakout = bool(
        row.get("breakout20") is True
        and volume >= 1.2 and change > 0 and not distribution
    )
    buy_days = _finite(row.get("institution_buy_days_5"))
    institution_5d = _finite(row.get("institution_5d"))
    institution_available = buy_days is not None and institution_5d is not None
    institution_streak = bool(
        institution_available and buy_days >= 3 and institution_5d > 0
    )
    rsi = _finite(row.get("rsi"))
    position_available = rsi is not None
    not_overextended = bool(
        position_available and rsi <= 72 and change < 7
        and sector.get("stage") != "climax" and not distribution
    )
    conditions = {
        "industry_strengthening": {"passed": sector_strong, "available": bool(sector)},
        "three_stock_co_movement": {"passed": co_movement, "available": bool(sector)},
        "volume_breakout": {"passed": volume_breakout, "available": True},
        "institution_consecutive_buy": {"passed": institution_streak, "available": institution_available},
        "not_overextended": {"passed": not_overextended, "available": position_available},
    }
    passed = sum(item["passed"] for item in conditions.values())
    available = sum(item["available"] for item in conditions.values())
    strict = bool(available == 5 and passed == 5)
    practical = bool(
        sector_strong and co_movement and not_overextended
        and (volume_breakout or institution_streak)
    )
    if strict:
        status = "strict_5of5"
    elif practical:
        status = "practical_4of5"
    elif not institution_available or not position_available:
        status = "data_insufficient"
    else:
        status = "not_qualified"
    return {
        "market": "TW",
        "passed_count": passed,
        "available_count": available,
        "strict_5of5": strict,
        "practical_4of5": practical,
        "status": status,
        "distribution_blocked": distribution,
        "conditions": conditions,
    }


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
            "daily_capital_flow": "盤中只累積；收盤完整日結後才以15%納入輪動分數，缺資料不扣分",
            "tw_strict": "台股嚴格組＝產業轉強＋3檔共振＋量增突破＋法人連買＋未過熱，5/5",
            "tw_practical": "台股實用組＝產業轉強＋3檔共振＋未過熱必須成立，量增突破／法人連買至少一項",
            "market_isolation": "台股使用法人／量價／台股日結資金流；美股使用成長財報代理／量價／美股日結資金流，互不混用",
            "settlement_coverage": "A/B原選10檔至少9檔同日正式開收盤價即可結算；缺價配置保留現金",
            "formal_checkpoint": "台股晚報／美股早報可延遲完成；依正式時段與同日行情判定，不用完成小時阻擋",
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
    coverage = {}
    blocking_missing = set()
    for key, model in (pending.get("models") or {}).items():
        picks = list(model.get("picks") or [])
        missing = sorted({
            str(pick.get("symbol") or "") for pick in picks
            if not (
                str(pick.get("symbol") or "") in row_map
                and _number(row_map[str(pick.get("symbol") or "")].get("official_open_price")) > 0
                and _number(row_map[str(pick.get("symbol") or "")].get("official_close_price")) > 0
            )
        })
        minimum = MIN_POSITIONS_PER_MODEL if len(picks) == PICKS else len(picks)
        available = len(picks) - len(missing)
        eligible = available >= minimum
        coverage[key] = {
            "required_positions": len(picks),
            "minimum_required_positions": minimum,
            "available_positions": available,
            "missing_symbols": missing,
            "eligible": eligible,
        }
        if not eligible:
            blocking_missing.update(missing)
    if blocking_missing:
        pending["settlement_status"] = "waiting_for_official_prices"
        pending["waiting_session_date"] = session_date
        pending["missing_symbols"] = sorted(blocking_missing)
        pending["model_coverage"] = coverage
        return False
    cost = ROUND_TRIP_COST_PCT[market]
    models = {}
    for key, model in (pending.get("models") or {}).items():
        positions = []
        gross_profit = net_profit = 0.0
        for pick in model.get("picks") or []:
            if pick["symbol"] not in row_map:
                continue
            row = row_map[pick["symbol"]]
            open_price = _number(row.get("official_open_price"))
            close_price = _number(row.get("official_close_price"))
            if open_price <= 0 or close_price <= 0:
                continue
            gross_return = (close_price / open_price - 1) * 100
            net_return = gross_return - cost
            signal_close = _number(pick.get("signal_close_price"))
            opening_gap = (
                (open_price / signal_close - 1) * 100 if signal_close > 0 else None
            )
            allocation = _number(pick.get("allocation_twd"), ALLOCATION_TWD)
            gross_profit += allocation * gross_return / 100
            net_profit += allocation * net_return / 100
            positions.append({
                **pick, "open_price": round(open_price, 4),
                "close_price": round(close_price, 4),
                "gross_return_pct": round(gross_return, 4),
                "net_return_pct": round(net_return, 4),
                "opening_gap_pct": round(opening_gap, 4) if opening_gap is not None else None,
                "opening_gap_invalidated": bool(opening_gap is not None and opening_gap >= 3.0),
            })
        models[key] = {
            "label": model.get("label"), "positions": positions,
            "executed_positions": len(positions),
            "planned_positions": coverage[key]["required_positions"],
            "minimum_required_positions": coverage[key]["minimum_required_positions"],
            "missing_symbols": coverage[key]["missing_symbols"],
            "nine_of_ten_settlement": bool(
                coverage[key]["required_positions"] == PICKS
                and len(positions) < PICKS
            ),
            "invested_twd": len(positions) * ALLOCATION_TWD,
            "idle_twd": CAPITAL_TWD - len(positions) * ALLOCATION_TWD,
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
    strict = sum(_number(((item.get("models") or {}).get("strict_5of5") or {}).get("net_profit_twd")) for item in outcomes)
    practical = sum(_number(((item.get("models") or {}).get("practical_4of5") or {}).get("net_profit_twd")) for item in outcomes)
    days = len(outcomes)
    model_metrics = {}
    for key in ("baseline", "rotation", "strict_5of5", "practical_4of5"):
        positions = [
            position
            for outcome in outcomes
            for position in (((outcome.get("models") or {}).get(key) or {}).get("positions") or [])
        ]
        returns = [_number(position.get("net_return_pct")) for position in positions]
        model_metrics[key] = {
            "samples": len(positions),
            "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 4) if returns else None,
            "misjudgment_rate_pct": round(sum(value <= 0 for value in returns) / len(returns) * 100, 4) if returns else None,
            "avg_net_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "opening_gap_invalidated": sum(position.get("opening_gap_invalidated") is True for position in positions),
        }
    return {
        "valid_trading_days": days,
        "invalid_trading_days": sum(item.get("status") != "valid" for item in all_outcomes),
        "baseline_net_profit_twd": round(baseline, 2),
        "rotation_net_profit_twd": round(rotation, 2),
        "rotation_incremental_net_profit_twd": round(rotation - baseline, 2),
        "baseline_net_return_pct": round(baseline / CAPITAL_TWD * 100, 4),
        "rotation_net_return_pct": round(rotation / CAPITAL_TWD * 100, 4),
        "strict_5of5_net_profit_twd": round(strict, 2) if market_state.get("market") == "TW" else None,
        "practical_4of5_net_profit_twd": round(practical, 2) if market_state.get("market") == "TW" else None,
        "review_status": "code_check_only" if days < 20 else "preliminary_review" if days < 60 else "candidate_review",
        "formal_ranking_locked": True,
        "model_metrics": model_metrics,
    }


def update_market_rotation_shadow(
    reports_dir: Path, rows: list[dict[str, Any]], *,
    period: str, updated_at: str, intraday: bool = False,
    daily_flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "market_rotation_shadow.json"
    state = _load(path, updated_at)
    state["updated_at"] = updated_at
    state.setdefault("policy", {})["settlement_coverage"] = (
        "A/B原選10檔至少9檔同日正式開收盤價即可結算；缺價配置保留現金"
    )
    state["policy"]["formal_checkpoint"] = (
        "台股晚報／美股早報可延遲完成；依正式時段與同日行情判定，不用完成小時阻擋"
    )
    state["policy"]["daily_capital_flow"] = (
        "盤中只累積；僅同市場、同交易日、正常盤完整日結可占輪動分數15%；缺漏時沿用原輪動分數"
    )
    state["policy"]["market_isolation"] = (
        "台股使用法人／量價／台股日結資金流；美股使用成長財報代理／量價／美股日結資金流，互不混用"
    )
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
            snapshot = build_market_snapshot(
                rows, market, snapshots[-1] if snapshots else None,
                daily_flow=daily_flow,
            )
            if snapshot is None:
                continue
            snapshots.append(snapshot)
            market_state["snapshots"] = snapshots[-MAX_SNAPSHOTS:]
        else:
            snapshot = existing
        if not market_state.get("pending"):
            models = {
                "baseline": {
                    "label": "A｜正式V6基準",
                    "picks": select_picks(rows, market, snapshot, rotation_overlay=False),
                },
                "rotation": {
                    "label": "B｜市場規則＋輪動15%",
                    "picks": select_picks(rows, market, snapshot, rotation_overlay=True),
                },
            }
            if market == "TW":
                models.update({
                    "strict_5of5": {
                        "label": "台股嚴格5／5研究條件符合（不等於可買）",
                        "picks": select_picks(
                            rows, market, snapshot, rotation_overlay=True,
                            qualification_mode="strict_5of5",
                        ),
                    },
                    "practical_4of5": {
                        "label": "台股實用4／5研究條件符合（不等於可買）",
                        "picks": select_picks(
                            rows, market, snapshot, rotation_overlay=True,
                            qualification_mode="practical_4of5",
                        ),
                    },
                })
            market_state["pending"] = {
                "signal_session_date": session_date,
                "created_at": updated_at,
                "snapshot_integrity_sha256": snapshot.get("integrity_sha256"),
                "settlement_status": "waiting_next_completed_session",
                "models": models,
            }
        market_state["status"] = "collecting_only"
        market_state["summary"] = _summary(market_state)
    tmp = reports_dir / "market_rotation_shadow.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return state
