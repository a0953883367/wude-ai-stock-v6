"""Five-session TW institutional-weight shadow comparison.

The three portfolios use the same completed-session universe, safety tier,
capital, entry and exit prices.  Only the accumulation weight differs.  This
module never imports a broker path and cannot place an order.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
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
    return state


def _new_pending(
    rows: list[dict[str, Any]], model: dict[str, Any], updated_at: str
) -> dict[str, Any] | None:
    signal_date = _session_date(rows)
    picks = select_picks(rows, _finite(model.get("accumulation_weight")))
    if not signal_date or len(picks) < PICKS:
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
    }


def _settle_pending(model: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    pending = model.get("pending")
    if not pending:
        return False
    session_date = _session_date(rows)
    signal_date = str(pending.get("signal_session_date") or "")
    if not pending.get("snapshot_id"):
        payload = "|".join(
            f"{pick.get('rank')}:{pick.get('symbol')}:{pick.get('ranking_score')}:{pick.get('rank_tier')}"
            for pick in pending.get("picks") or []
        )
        pending["snapshot_id"] = hashlib.sha256(
            f"{model.get('key')}|{signal_date}|{payload}".encode()
        ).hexdigest()[:12]
    if not session_date or session_date < START_DATE or session_date <= signal_date:
        return False
    row_map = {str(row.get("symbol") or ""): row for row in rows if _is_tw_stock(row)}
    positions = []
    gross_profit = net_profit = invested = 0.0
    for pick in pending.get("picks") or []:
        row = row_map.get(str(pick.get("symbol") or ""))
        open_price = _finite(row.get("official_open_price")) if row else 0.0
        close_price = _finite(row.get("official_close_price")) if row else 0.0
        allocation = _finite(pick.get("allocation_twd"), ALLOCATION_TWD)
        available = bool(
            row and str(row.get("official_session_date") or "") == session_date
            and open_price > 0 and close_price > 0
        )
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
    model.setdefault("days", []).append({
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
    })
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
    model["metrics"] = {
        "net_profit_twd": round(net_profit, 2),
        "net_return_pct": round(net_profit / CAPITAL_TWD * 100, 4),
        "gross_profit_twd": round(gross_profit, 2),
        "win_rate_pct": round(wins / len(net_returns) * 100, 2) if net_returns else 0.0,
        "avg_position_net_return_pct": round(sum(net_returns) / len(net_returns), 4) if net_returns else 0.0,
        "max_drawdown_pct": round(max_drawdown, 4),
        "avg_rank_turnover_pct": round(sum(turnovers) / len(turnovers), 2) if turnovers else 0.0,
        "evaluated_positions": len(net_returns),
    }


def update_state(
    state: dict[str, Any], rows: list[dict[str, Any]], *,
    period: str, updated_at: str, intraday: bool = False,
) -> dict[str, Any]:
    state["updated_at"] = updated_at
    if intraday or period != "evening":
        return state
    for key, model in state["models"].items():
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
    if completed:
        state["winner_model"] = max(
            state["models"],
            key=lambda name: _finite(state["models"][name].get("metrics", {}).get("net_return_pct")),
        )
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
