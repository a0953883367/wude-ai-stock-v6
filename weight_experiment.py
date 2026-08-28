"""Five-session TW institutional-weight shadow comparison.

The three portfolios use the same completed-session universe, safety tier,
capital, entry and exit prices.  Only the accumulation weight differs.  This
module never imports a broker path and cannot place an order.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


VERSION = 1
START_DATE = "2026-08-24"
TARGET_DAYS = 5
CAPITAL_TWD = 1_000_000
PICKS = 10
ALLOCATION_TWD = CAPITAL_TWD // PICKS
ROUND_TRIP_COST_PCT = 0.685
MODELS = {
    "base_0": {"label": "100/0｜原模型", "accumulation_weight": 0.0},
    "moderate_10": {"label": "90/10｜法人10%", "accumulation_weight": 0.10},
    "accumulation_20": {"label": "80/20｜法人20%", "accumulation_weight": 0.20},
}


def _finite(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number and abs(number) != float("inf") else fallback


def _is_tw_stock(row: dict[str, Any]) -> bool:
    return (
        str(row.get("market") or "").upper() == "TW"
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _session_date(rows: Iterable[dict[str, Any]]) -> str:
    dates = [
        str(row.get("official_session_date") or "")
        for row in rows
        if _is_tw_stock(row) and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _price_snapshot(row: dict[str, Any] | None) -> tuple[str, float, float]:
    if not row:
        return "", 0.0, 0.0
    return (
        str(row.get("official_session_date") or ""),
        _finite(row.get("official_open_price")),
        _finite(row.get("official_close_price")),
    )


def _pending_readiness(
    pending: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Require every frozen pick to share one later, complete official session."""
    picks = pending.get("picks") or []
    signal_date = str(pending.get("signal_session_date") or "")
    row_map = {str(row.get("symbol") or ""): row for row in rows if _is_tw_stock(row)}
    observations: dict[str, tuple[str, float, float]] = {
        str(pick.get("symbol") or ""): _price_snapshot(
            row_map.get(str(pick.get("symbol") or ""))
        )
        for pick in picks
    }
    eligible_dates = [
        session_date
        for session_date, open_price, close_price in observations.values()
        if session_date >= START_DATE and session_date > signal_date
        and open_price > 0 and close_price > 0
    ]
    target_session = Counter(eligible_dates).most_common(1)[0][0] if eligible_dates else ""
    missing_symbols = [
        symbol for symbol, (session_date, open_price, close_price) in observations.items()
        if not target_session or session_date != target_session
        or open_price <= 0 or close_price <= 0
    ]
    required = PICKS
    complete = bool(
        len(picks) == required
        and target_session
        and not missing_symbols
        and len(set(eligible_dates)) == 1
    )
    return {
        "settlement_status": "ready" if complete else "waiting_for_official_prices",
        "session_date": target_session if complete else None,
        "available_positions": required - len(missing_symbols) if len(picks) == required else 0,
        "required_positions": required,
        "missing_symbols": missing_symbols,
        "observed_session_dates": sorted({date for date, _, _ in observations.values() if date}),
    }


def _day_completeness(day: dict[str, Any]) -> dict[str, Any]:
    positions = day.get("positions") or []
    missing_symbols = [
        str(position.get("symbol") or "")
        for position in positions
        if not position.get("data_available")
        or _finite(position.get("open_price")) <= 0
        or _finite(position.get("sell_price")) <= 0
    ]
    if len(positions) < PICKS:
        missing_symbols.extend(["未列出標的"] * (PICKS - len(positions)))
    available = sum(
        bool(
            position.get("data_available")
            and _finite(position.get("open_price")) > 0
            and _finite(position.get("sell_price")) > 0
        )
        for position in positions
    )
    complete = bool(
        len(positions) == PICKS
        and available == PICKS
        and math.isclose(_finite(day.get("invested_twd")), CAPITAL_TWD, abs_tol=0.01)
    )
    return {
        "data_complete": complete,
        "available_positions": available,
        "required_positions": PICKS,
        "missing_symbols": missing_symbols,
    }


def _sanitize_existing_days(model: dict[str, Any], updated_at: str) -> None:
    """Quarantine legacy partial days so they cannot inflate completed samples."""
    valid_days: list[dict[str, Any]] = []
    invalid_days = model.setdefault("invalid_days", [])
    invalid_keys = {
        str(day.get("ranking_snapshot_id") or f"{day.get('signal_session_date')}|{day.get('session_date')}")
        for day in invalid_days
    }
    for day in model.get("days") or []:
        completeness = _day_completeness(day)
        if completeness["data_complete"]:
            day.update(completeness)
            day["rank_return_spearman"] = _day_rank_return_spearman(day)
            valid_days.append(day)
            continue
        invalid_key = str(
            day.get("ranking_snapshot_id")
            or f"{day.get('signal_session_date')}|{day.get('session_date')}"
        )
        if invalid_key not in invalid_keys:
            invalid_days.append({
                **day,
                **completeness,
                "status": "data_incomplete",
                "invalid_reason": "未取得10/10同一交易日官方開盤與收盤價",
                "invalidated_at": updated_at,
            })
            invalid_keys.add(invalid_key)
    for index, day in enumerate(valid_days, 1):
        day["day"] = index
    model["days"] = valid_days
    model["completed_days"] = len(valid_days)
    if model.get("status") == "complete" and len(valid_days) < TARGET_DAYS:
        model["status"] = "running"


def _rank_desc(values: list[float]) -> list[float]:
    """Return average ranks where the largest value is rank one."""
    ordered = sorted(enumerate(values), key=lambda item: (-item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for offset in range(start, end):
            ranks[ordered[offset][0]] = average_rank
        start = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _day_rank_return_spearman(day: dict[str, Any]) -> float | None:
    positions = [
        position for position in day.get("positions") or []
        if position.get("data_available")
    ]
    if len(positions) < 2:
        return None
    predicted_ranks = [_finite(position.get("rank")) for position in positions]
    realized_ranks = _rank_desc([
        _finite(position.get("gross_return_pct")) for position in positions
    ])
    correlation = _pearson(predicted_ranks, realized_ranks)
    return round(correlation, 4) if correlation is not None else None


def _capture_metrics(
    pick_symbols: set[str], rows: list[dict[str, Any]], session_date: str
) -> dict[str, Any]:
    outcomes: list[tuple[str, float]] = []
    for row in rows:
        if not _is_tw_stock(row):
            continue
        row_date, open_price, close_price = _price_snapshot(row)
        if row_date != session_date or open_price <= 0 or close_price <= 0:
            continue
        outcomes.append((
            str(row.get("symbol") or ""),
            (close_price / open_price - 1) * 100,
        ))
    outcomes.sort(key=lambda item: (-item[1], item[0]))
    result: dict[str, Any] = {}
    for size in (10, 20):
        actual = {symbol for symbol, _ in outcomes[:size]}
        captured = len(pick_symbols & actual)
        total = len(actual)
        result[f"actual_top{size}_capture_count"] = captured
        result[f"actual_top{size}_total"] = total
        result[f"actual_top{size}_capture_rate_pct"] = (
            round(captured / total * 100, 2) if total else 0.0
        )
    return result


def _tier(row: dict[str, Any]) -> int:
    blocked = bool(row.get("trade_guard_blocked") or row.get("market_contract_valid") is False)
    if blocked:
        return 0
    explicit = row.get("short_term_rank_tier")
    if explicit is not None:
        return max(0, min(2, int(_finite(explicit))))
    return 2 if row.get("short_term_eligible") is True else 1


def ranking_score(row: dict[str, Any], accumulation_weight: float) -> float:
    """Rebuild a short rank with one frozen weight and no missing-data fill."""
    base_score = _finite(row.get("short_term_base_score"), _finite(row.get("short_term_score")))
    base_rank = _finite(row.get("short_term_base_ranking_score"))
    if base_rank <= 0:
        base_rank = _finite(row.get("short_term_ranking_score"), base_score)
    if not row.get("tw_accumulation_available") or base_score <= 0:
        return round(base_rank, 2)
    multiplier = base_rank / base_score
    accumulation = _finite(row.get("tw_accumulation_score"))
    blended = base_score * (1 - accumulation_weight) + accumulation * accumulation_weight
    return round(blended * multiplier, 2)


def select_picks(
    rows: Iterable[dict[str, Any]], accumulation_weight: float
) -> list[dict[str, Any]]:
    ranked = sorted(
        (dict(row) for row in rows if _is_tw_stock(row)),
        key=lambda row: (
            -_tier(row),
            -ranking_score(row, accumulation_weight),
            -_finite(row.get("short_term_score")),
            str(row.get("symbol") or ""),
        ),
    )
    return [{
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "rank": index,
        "rank_tier": _tier(row),
        "ranking_score": ranking_score(row, accumulation_weight),
        "base_ranking_score": round(_finite(
            row.get("short_term_base_ranking_score"),
            _finite(row.get("short_term_ranking_score")),
        ), 2),
        "accumulation_score": (
            round(_finite(row.get("tw_accumulation_score")), 1)
            if row.get("tw_accumulation_available") else None
        ),
        "allocation_twd": ALLOCATION_TWD,
    } for index, row in enumerate(ranked[:PICKS], 1)]


def _empty_model(key: str) -> dict[str, Any]:
    config = MODELS[key]
    return {
        "key": key,
        **config,
        "capital_twd": CAPITAL_TWD,
        "completed_days": 0,
        "status": "waiting",
        "days": [],
        "invalid_days": [],
        "pending": None,
        "last_pick_symbols": [],
        "metrics": {
            "net_profit_twd": 0.0,
            "net_return_pct": 0.0,
            "gross_profit_twd": 0.0,
            "win_rate_pct": 0.0,
            "avg_position_net_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_rank_turnover_pct": 0.0,
            "evaluated_positions": 0,
            "avg_rank_return_spearman": 0.0,
            "avg_top10_capture_rate_pct": 0.0,
            "avg_top20_capture_rate_pct": 0.0,
            "rank_order_evaluated_days": 0,
            "capture_evaluated_days": 0,
        },
    }


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "web_shadow_only",
        "status": "waiting",
        "policy": {
            "start_date": START_DATE,
            "target_trading_days": TARGET_DAYS,
            "capital_per_model_twd": CAPITAL_TWD,
            "picks_per_model": PICKS,
            "allocation_per_pick_twd": ALLOCATION_TWD,
            "entry": "前一完成交易日排名；下一交易日官方開盤價",
            "exit": "同日官方收盤價，作為收盤前賣出代理",
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "cost_assumption": "台股買賣手續費0.285%＋證交稅0.3%＋滑價0.1%",
            "orders": "純網頁影子試走，不連接券商、不送單",
            "selection": "安全資格分層優先；每組前10名等權",
            "valid_session_rule": "10檔均須取得同一個較晚交易日的官方開盤與收盤價",
            "incomplete_policy": "任一檔缺價即等待補抓；既有不完整日移入無效樣本，不增加完成天數",
            "decision_rule": "5日只做第一次檢查，不自動改權重；20日樣本後再決定正式調整",
        },
        "models": {key: _empty_model(key) for key in MODELS},
        "winner_model": None,
    }


def _load(path: Path, updated_at: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty_state(updated_at)
    if state.get("version") != VERSION or not isinstance(state.get("models"), dict):
        return empty_state(updated_at)
    for key in MODELS:
        state["models"].setdefault(key, _empty_model(key))
        state["models"][key].setdefault("invalid_days", [])
    policy = state.setdefault("policy", {})
    for name, value in empty_state(updated_at)["policy"].items():
        policy.setdefault(name, value)
    return state


def _new_pending(
    rows: list[dict[str, Any]], model: dict[str, Any], updated_at: str
) -> dict[str, Any] | None:
    signal_date = _session_date(rows)
    picks = select_picks(rows, _finite(model.get("accumulation_weight")))
    if not signal_date or len(picks) < PICKS:
        return None
    row_map = {str(row.get("symbol") or ""): row for row in rows if _is_tw_stock(row)}
    pick_dates = {
        str((row_map.get(pick["symbol"]) or {}).get("official_session_date") or "")
        for pick in picks
    }
    if pick_dates != {signal_date}:
        return None
    previous = set(model.get("last_pick_symbols") or [])
    current = {pick["symbol"] for pick in picks}
    turnover = None if not previous else round((1 - len(previous & current) / PICKS) * 100, 1)
    snapshot_payload = "|".join(
        f"{pick['rank']}:{pick['symbol']}:{pick['ranking_score']}:{pick['rank_tier']}"
        for pick in picks
    )
    snapshot_id = hashlib.sha256(
        f"{model.get('key')}|{signal_date}|{snapshot_payload}".encode()
    ).hexdigest()[:12]
    return {
        "signal_session_date": signal_date,
        "created_at": updated_at,
        "snapshot_id": snapshot_id,
        "execution_session_date": None,
        "rank_turnover_pct": turnover,
        "picks": picks,
        "signal_data_complete": True,
        "settlement_status": "waiting_for_official_prices",
        "available_positions": 0,
        "required_positions": PICKS,
        "missing_symbols": [pick["symbol"] for pick in picks],
        "observed_session_dates": [],
    }


def _settle_pending(model: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    pending = model.get("pending")
    if not pending:
        return False
    signal_date = str(pending.get("signal_session_date") or "")
    if not pending.get("snapshot_id"):
        payload = "|".join(
            f"{pick.get('rank')}:{pick.get('symbol')}:{pick.get('ranking_score')}:{pick.get('rank_tier')}"
            for pick in pending.get("picks") or []
        )
        pending["snapshot_id"] = hashlib.sha256(
            f"{model.get('key')}|{signal_date}|{payload}".encode()
        ).hexdigest()[:12]
    readiness = _pending_readiness(pending, rows)
    pending.update(readiness)
    session_date = str(readiness.get("session_date") or "")
    if readiness["settlement_status"] != "ready" or not session_date:
        return False
    row_map = {str(row.get("symbol") or ""): row for row in rows if _is_tw_stock(row)}
    positions = []
    gross_profit = net_profit = invested = 0.0
    for pick in pending.get("picks") or []:
        row = row_map.get(str(pick.get("symbol") or ""))
        open_price = _finite(row.get("official_open_price")) if row else 0.0
        close_price = _finite(row.get("official_close_price")) if row else 0.0
        allocation = _finite(pick.get("allocation_twd"), ALLOCATION_TWD)
        available = bool(row and str(row.get("official_session_date") or "") == session_date
                         and open_price > 0 and close_price > 0)
        gross_return = (close_price / open_price - 1) * 100 if available else 0.0
        net_return = gross_return - ROUND_TRIP_COST_PCT if available else 0.0
        position_gross = allocation * gross_return / 100 if available else 0.0
        position_net = allocation * net_return / 100 if available else 0.0
        if available:
            invested += allocation
            gross_profit += position_gross
            net_profit += position_net
        positions.append({
            **pick,
            "open_price": round(open_price, 4) if available else None,
            "sell_price": round(close_price, 4) if available else None,
            "gross_return_pct": round(gross_return, 4) if available else None,
            "net_return_pct": round(net_return, 4) if available else None,
            "gross_profit_twd": round(position_gross, 2),
            "net_profit_twd": round(position_net, 2),
            "data_available": available,
        })
    capture = _capture_metrics(
        {str(pick.get("symbol") or "") for pick in pending.get("picks") or []},
        rows,
        session_date,
    )
    day = {
        "day": len(model.get("days") or []) + 1,
        "signal_session_date": signal_date,
        "session_date": session_date,
        "rank_turnover_pct": pending.get("rank_turnover_pct"),
        "ranking_snapshot_id": pending.get("snapshot_id"),
        "invested_twd": round(invested, 2),
        "gross_profit_twd": round(gross_profit, 2),
        "net_profit_twd": round(net_profit, 2),
        "net_return_pct": round(net_profit / CAPITAL_TWD * 100, 4),
        "positions": positions,
        "data_complete": True,
        "available_positions": PICKS,
        "required_positions": PICKS,
        "missing_symbols": [],
        **capture,
    }
    day["rank_return_spearman"] = _day_rank_return_spearman(day)
    model.setdefault("days", []).append(day)
    model["last_pick_symbols"] = [str(pick.get("symbol") or "") for pick in pending.get("picks") or []]
    model["pending"] = None
    model["completed_days"] = len(model["days"])
    return True


def _refresh_metrics(model: dict[str, Any]) -> None:
    days = model.get("days") or []
    positions = [
        position for day in days for position in day.get("positions") or []
        if position.get("data_available")
    ]
    gross_profit = sum(_finite(day.get("gross_profit_twd")) for day in days)
    net_profit = sum(_finite(day.get("net_profit_twd")) for day in days)
    turnovers = [
        _finite(day.get("rank_turnover_pct")) for day in days
        if day.get("rank_turnover_pct") is not None
    ]
    equity = peak = CAPITAL_TWD
    max_drawdown = 0.0
    for day in days:
        equity += _finite(day.get("net_profit_twd"))
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100 if peak else 0.0)
    net_returns = [_finite(position.get("net_return_pct")) for position in positions]
    wins = sum(value > 0 for value in net_returns)
    rank_correlations = [
        _finite(day.get("rank_return_spearman")) for day in days
        if day.get("rank_return_spearman") is not None
    ]
    top10_capture = [
        _finite(day.get("actual_top10_capture_rate_pct")) for day in days
        if day.get("actual_top10_capture_rate_pct") is not None
    ]
    top20_capture = [
        _finite(day.get("actual_top20_capture_rate_pct")) for day in days
        if day.get("actual_top20_capture_rate_pct") is not None
    ]
    model["metrics"] = {
        "net_profit_twd": round(net_profit, 2),
        "net_return_pct": round(net_profit / CAPITAL_TWD * 100, 4),
        "gross_profit_twd": round(gross_profit, 2),
        "win_rate_pct": round(wins / len(net_returns) * 100, 2) if net_returns else 0.0,
        "avg_position_net_return_pct": round(sum(net_returns) / len(net_returns), 4) if net_returns else 0.0,
        "max_drawdown_pct": round(max_drawdown, 4),
        "avg_rank_turnover_pct": round(sum(turnovers) / len(turnovers), 2) if turnovers else 0.0,
        "evaluated_positions": len(net_returns),
        "avg_rank_return_spearman": round(
            sum(rank_correlations) / len(rank_correlations), 4
        ) if rank_correlations else 0.0,
        "avg_top10_capture_rate_pct": round(
            sum(top10_capture) / len(top10_capture), 2
        ) if top10_capture else 0.0,
        "avg_top20_capture_rate_pct": round(
            sum(top20_capture) / len(top20_capture), 2
        ) if top20_capture else 0.0,
        "rank_order_evaluated_days": len(rank_correlations),
        "capture_evaluated_days": len(top20_capture),
    }


def _overlap(left: set[str], right: set[str]) -> dict[str, Any]:
    count = len(left & right)
    denominator = max(len(left), len(right), 1)
    return {"count": count, "pct": round(count / denominator * 100, 1)}


def _model_symbols(model: dict[str, Any], *, session_date: str | None = None) -> set[str]:
    if session_date is None:
        positions = (model.get("pending") or {}).get("picks") or []
    else:
        day = next((
            item for item in model.get("days") or []
            if str(item.get("session_date") or "") == session_date
        ), {})
        positions = day.get("positions") or []
    return {str(item.get("symbol") or "") for item in positions if item.get("symbol")}


def _overlap_snapshot(state: dict[str, Any], session_date: str | None = None) -> dict[str, Any]:
    models = state.get("models") or {}
    base = _model_symbols(models.get("base_0", {}), session_date=session_date)
    moderate = _model_symbols(models.get("moderate_10", {}), session_date=session_date)
    accumulation = _model_symbols(models.get("accumulation_20", {}), session_date=session_date)
    complete = bool(len(base) == len(moderate) == len(accumulation) == PICKS)
    all_same = complete and base == moderate == accumulation
    return {
        "base_vs_moderate": _overlap(base, moderate),
        "base_vs_accumulation": _overlap(base, accumulation),
        "moderate_vs_accumulation": _overlap(moderate, accumulation),
        "all_models_same_constituents": all_same,
        "weight_effect_identifiable_by_equal_weight_profit": complete and not all_same,
    }


def _refresh_diagnostics(state: dict[str, Any]) -> None:
    models = state.get("models") or {}
    session_dates = sorted({
        str(day.get("session_date") or "")
        for model in models.values()
        for day in model.get("days") or []
        if day.get("session_date")
    })
    daily = []
    for session_date in session_dates:
        if not all(any(
            str(day.get("session_date") or "") == session_date
            for day in model.get("days") or []
        ) for model in models.values()):
            continue
        daily.append({"session_date": session_date, **_overlap_snapshot(state, session_date)})
    pending_dates = {
        str((model.get("pending") or {}).get("signal_session_date") or "")
        for model in models.values()
    }
    pending = _overlap_snapshot(state) if len(pending_dates) == 1 and "" not in pending_dates else {}
    indistinguishable = sum(bool(item["all_models_same_constituents"]) for item in daily)
    state["diagnostics"] = {
        "daily_membership_overlap": daily,
        "latest_pending_membership_overlap": pending,
        "indistinguishable_valid_sessions": indistinguishable,
        "valid_sessions_compared": len(daily),
        "interpretation": (
            "三組持股完全相同時，等權損益無法辨別法人權重；另以排名與次日漲幅相關性及強勢股捕捉率比較。"
        ),
    }


def update_state(
    state: dict[str, Any], rows: list[dict[str, Any]], *,
    period: str, updated_at: str, intraday: bool = False,
) -> dict[str, Any]:
    state["updated_at"] = updated_at
    if intraday or period != "evening":
        return state
    for key, model in state["models"].items():
        _sanitize_existing_days(model, updated_at)
        if model.get("status") == "complete":
            continue
        _settle_pending(model, rows)
        _refresh_metrics(model)
        if model.get("completed_days", 0) >= TARGET_DAYS:
            model["status"] = "complete"
            model["pending"] = None
        elif model.get("pending") is None:
            model["pending"] = _new_pending(rows, model, updated_at)
            if model["pending"]:
                model["status"] = "running"
    base_metrics = state["models"].get("base_0", {}).get("metrics", {})
    base_profit = _finite(base_metrics.get("net_profit_twd"))
    base_return = _finite(base_metrics.get("net_return_pct"))
    for key, model in state["models"].items():
        metrics = model.get("metrics", {})
        model["comparison_vs_base"] = {
            "incremental_net_profit_twd": round(_finite(metrics.get("net_profit_twd")) - base_profit, 2),
            "incremental_net_return_pct": round(_finite(metrics.get("net_return_pct")) - base_return, 4),
            "interpretation": "原模型基準" if key == "base_0" else "法人權重相較原模型的額外效益",
        }
    completed = all(model.get("status") == "complete" for model in state["models"].values())
    state["status"] = "complete" if completed else "running"
    state["winner_model"] = None
    if completed:
        state["winner_model"] = max(
            state["models"],
            key=lambda name: _finite(state["models"][name].get("metrics", {}).get("net_return_pct")),
        )
    _refresh_diagnostics(state)
    return state


def update_weight_experiment(
    reports_dir: Path, rows: list[dict[str, Any]], *,
    period: str, updated_at: str, intraday: bool = False,
) -> Path:
    path = reports_dir / "tw_weight_experiment.json"
    state = update_state(
        _load(path, updated_at), rows, period=period,
        updated_at=updated_at, intraday=intraday,
    )
    tmp = reports_dir / "tw_weight_experiment.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path
