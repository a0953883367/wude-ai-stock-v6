"""Forward-only 1/2/3/5-session exit comparison for the short model.

One frozen ranking cohort is opened with official next-session prices.  The
four research portfolios share that entry and differ only by exit session.
This file has no broker import and cannot place orders.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from million_simulation import ROUND_TRIP_COST_PCT, select_picks


VERSION = 1
START_DATE = "2026-08-24"
CAPITAL_TWD = 1_000_000
ALLOCATION_TWD = 100_000
HORIZONS = (1, 2, 3, 5)
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
BENCHMARKS = {"TW": ("0050.TW", "0050"), "US": ("VOO", "VOO")}


def _finite(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number and abs(number) != float("inf") else fallback


def _is_stock(row: dict[str, Any], market: str) -> bool:
    return str(row.get("market") or "").upper() == market and "ETF" not in str(row.get("type") or "").upper()


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    dates = [
        str(row.get("official_session_date") or "")
        for row in rows if _is_stock(row, market) and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _empty_horizon(holding_sessions: int) -> dict[str, Any]:
    return {
        "holding_sessions": holding_sessions,
        "status": "waiting",
        "exit_session_date": None,
        "ranking": {"net_profit_twd": 0.0, "net_return_pct": 0.0, "executed_positions": 0},
        "strict": {"net_profit_twd": 0.0, "net_return_pct": 0.0, "executed_positions": 0, "idle_twd": CAPITAL_TWD},
        "benchmark": {"net_profit_twd": 0.0, "net_return_pct": 0.0, "data_available": False},
        "positions": [],
    }


def _empty_market(market: str) -> dict[str, Any]:
    symbol, label = BENCHMARKS[market]
    return {
        "market": market,
        "capital_twd": CAPITAL_TWD,
        "status": "waiting",
        "pending": None,
        "entry_session_date": None,
        "observed_sessions": [],
        "positions": [],
        "benchmark": {"symbol": symbol, "label": label, "entry_price": None},
        "horizons": {str(value): _empty_horizon(value) for value in HORIZONS},
    }


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "web_shadow_only",
        "status": "waiting",
        "policy": {
            "start_date": START_DATE,
            "capital_per_market_twd": CAPITAL_TWD,
            "picks": 10,
            "allocation_per_pick_twd": ALLOCATION_TWD,
            "selection": "同一份短線前10名快照；嚴格組只執行資格層2，其餘留現金",
            "entry": "訊號後下一完成交易日官方開盤",
            "horizons": list(HORIZONS),
            "exit": "分別於持有第1、2、3、5個交易日官方收盤結算",
            "costs": "每檔每個實驗組只扣一次來回成本；台股0.685%、美股0.20%",
            "capital_is_not_stacked": True,
            "orders": "純網頁前向比較，不連接券商、不送單",
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


def _new_pending(rows: list[dict[str, Any]], market: str, updated_at: str) -> dict[str, Any] | None:
    signal_date = _session_date(rows, market)
    picks = select_picks(rows, market, "short")
    if not signal_date or len(picks) < 10:
        return None
    picks = [{**pick, "allocation_twd": ALLOCATION_TWD} for pick in picks]
    payload = "|".join(f"{pick['rank']}:{pick['symbol']}:{pick['ranking_score']}:{pick['rank_tier']}" for pick in picks)
    return {
        "signal_session_date": signal_date,
        "created_at": updated_at,
        "snapshot_id": hashlib.sha256(f"EXIT|{market}|{signal_date}|{payload}".encode()).hexdigest()[:12],
        "picks": picks,
    }


def _locked_limit_up(row: dict[str, Any], market: str) -> bool:
    if market != "TW":
        return False
    open_price = _finite(row.get("official_open_price"))
    high = _finite(row.get("official_high_price"))
    low = _finite(row.get("official_low_price"))
    return bool(_finite(row.get("change_pct")) >= 9.5 and open_price > 0 and abs(open_price - high) < 1e-9 and abs(open_price - low) < 1e-9)


def _open_market(market_state: dict[str, Any], rows: list[dict[str, Any]], session_date: str) -> bool:
    pending = market_state.get("pending")
    if not pending or session_date < START_DATE or session_date <= str(pending.get("signal_session_date") or ""):
        return False
    row_map = {str(row.get("symbol") or ""): row for row in rows}
    positions = []
    for pick in pending.get("picks") or []:
        row = row_map.get(str(pick.get("symbol") or ""))
        open_price = _finite(row.get("official_open_price")) if row and str(row.get("official_session_date") or "") == session_date else 0.0
        entry_available = open_price > 0
        strict_eligible = int(_finite(pick.get("rank_tier"))) == 2
        strict_executed = bool(entry_available and strict_eligible and not _locked_limit_up(row or {}, market_state["market"]))
        positions.append({
            **pick,
            "entry_price": round(open_price, 4) if entry_available else None,
            "entry_available": entry_available,
            "strict_executed": strict_executed,
        })
    benchmark_row = row_map.get(market_state["benchmark"]["symbol"])
    benchmark_open = _finite(benchmark_row.get("official_open_price")) if benchmark_row and str(benchmark_row.get("official_session_date") or "") == session_date else 0.0
    market_state["positions"] = positions
    market_state["entry_session_date"] = session_date
    market_state["observed_sessions"] = [session_date]
    market_state["benchmark"]["entry_price"] = round(benchmark_open, 4) if benchmark_open > 0 else None
    market_state["pending"] = None
    market_state["status"] = "running"
    return True


def _settle_horizon(market_state: dict[str, Any], rows: list[dict[str, Any]], session_date: str, holding_sessions: int) -> bool:
    horizon = market_state["horizons"][str(holding_sessions)]
    if horizon.get("status") == "complete" or len(market_state.get("observed_sessions") or []) < holding_sessions:
        return False
    row_map = {str(row.get("symbol") or ""): row for row in rows}
    market = market_state["market"]
    cost_pct = ROUND_TRIP_COST_PCT[market]
    ranking_profit = strict_profit = 0.0
    ranking_positions = strict_positions = 0
    positions = []
    for position in market_state.get("positions") or []:
        row = row_map.get(str(position.get("symbol") or ""))
        close_price = _finite(row.get("official_close_price")) if row and str(row.get("official_session_date") or "") == session_date else 0.0
        entry_price = _finite(position.get("entry_price"))
        available = bool(position.get("entry_available") and entry_price > 0 and close_price > 0)
        net_return = (close_price / entry_price - 1) * 100 - cost_pct if available else 0.0
        profit = ALLOCATION_TWD * net_return / 100 if available else 0.0
        if available:
            ranking_positions += 1
            ranking_profit += profit
        if available and position.get("strict_executed"):
            strict_positions += 1
            strict_profit += profit
        positions.append({
            "symbol": position.get("symbol"), "name": position.get("name"), "rank": position.get("rank"),
            "entry_price": position.get("entry_price"), "exit_price": round(close_price, 4) if available else None,
            "net_return_pct": round(net_return, 4) if available else None,
            "net_profit_twd": round(profit, 2) if available else 0.0,
            "strict_executed": bool(position.get("strict_executed") and available), "data_available": available,
        })
    benchmark_row = row_map.get(market_state["benchmark"]["symbol"])
    benchmark_close = _finite(benchmark_row.get("official_close_price")) if benchmark_row and str(benchmark_row.get("official_session_date") or "") == session_date else 0.0
    benchmark_open = _finite(market_state["benchmark"].get("entry_price"))
    benchmark_available = benchmark_open > 0 and benchmark_close > 0
    benchmark_return = (benchmark_close / benchmark_open - 1) * 100 - cost_pct if benchmark_available else 0.0
    benchmark_profit = CAPITAL_TWD * benchmark_return / 100 if benchmark_available else 0.0
    horizon.update({
        "status": "complete", "exit_session_date": session_date,
        "ranking": {"net_profit_twd": round(ranking_profit, 2), "net_return_pct": round(ranking_profit / CAPITAL_TWD * 100, 4), "executed_positions": ranking_positions},
        "strict": {"net_profit_twd": round(strict_profit, 2), "net_return_pct": round(strict_profit / CAPITAL_TWD * 100, 4), "executed_positions": strict_positions, "idle_twd": CAPITAL_TWD - strict_positions * ALLOCATION_TWD},
        "benchmark": {"net_profit_twd": round(benchmark_profit, 2), "net_return_pct": round(benchmark_return, 4), "data_available": benchmark_available},
        "positions": positions,
    })
    return True


def update_state(state: dict[str, Any], rows: list[dict[str, Any]], *, period: str, updated_at: str, intraday: bool = False) -> dict[str, Any]:
    state["updated_at"] = updated_at
    if intraday:
        return state
    for market in MARKETS:
        if period != CLOSED_PERIOD[market]:
            continue
        market_state = state["markets"][market]
        if market_state.get("status") == "complete":
            continue
        session_date = _session_date(rows, market)
        if not session_date:
            continue
        if market_state.get("pending") is None and market_state.get("entry_session_date") is None:
            market_state["pending"] = _new_pending(rows, market, updated_at)
        opened = _open_market(market_state, rows, session_date)
        if market_state.get("entry_session_date") and not opened:
            observed = market_state.setdefault("observed_sessions", [])
            if session_date > observed[-1]:
                observed.append(session_date)
        if market_state.get("entry_session_date"):
            for holding_sessions in HORIZONS:
                _settle_horizon(market_state, rows, session_date, holding_sessions)
            if all(item.get("status") == "complete" for item in market_state["horizons"].values()):
                market_state["status"] = "complete"
    statuses = [state["markets"][market].get("status") for market in MARKETS]
    state["status"] = "complete" if all(value == "complete" for value in statuses) else ("running" if any(value == "running" for value in statuses) else "waiting")
    return state


def update_exit_horizon_experiment(reports_dir: Path, rows: list[dict[str, Any]], *, period: str, updated_at: str, intraday: bool = False) -> Path:
    path = reports_dir / "exit_horizon_experiment.json"
    state = update_state(_load(path, updated_at), rows, period=period, updated_at=updated_at, intraday=intraday)
    tmp = reports_dir / "exit_horizon_experiment.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path
