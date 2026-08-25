"""Medium- and long-horizon web-only shadow portfolios.

Medium portfolios allocate TWD 1,000,000 to the top five mid/long ranking
rows in each market and hold for 45 calendar days.  The long portfolio holds
the top Taiwan stock and top US stock, TWD 500,000 each, for six calendar
months.  Entries use the next completed session's official open; valuations
and exits use official closes.  This module never imports a broker client.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


VERSION = 1
START_DATE = "2026-08-24"
MARKETS = ("TW", "US")
MARKET_LABELS = {"TW": "台股", "US": "美股"}
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
MEDIUM_CAPITAL_TWD = 1_000_000
MEDIUM_PICK_COUNT = 5
MEDIUM_ALLOCATION_TWD = 200_000
MEDIUM_HOLD_DAYS = 45
LONG_CAPITAL_TWD = 1_000_000
LONG_PICK_COUNT_PER_MARKET = 1
LONG_ALLOCATION_TWD = 500_000
LONG_HOLD_MONTHS = 6
ROUND_TRIP_COST_PCT = {"TW": 0.685, "US": 0.20}
BENCHMARKS = {
    "TW": {"symbol": "0050.TW", "name": "0050｜台灣50"},
    "US": {"symbol": "VOO", "name": "VOO｜S&P 500"},
}


def _finite(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number and abs(number) != float("inf") else fallback


def _is_stock(row: dict[str, Any], market: str) -> bool:
    return (
        str(row.get("market") or "").upper() == market
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _rank_tier(row: dict[str, Any]) -> int:
    explicit = row.get("mid_long_rank_tier")
    if explicit is not None:
        return max(0, min(2, int(_finite(explicit))))
    blocked = bool(row.get("trade_guard_severe") or row.get("market_contract_valid") is False)
    return 0 if blocked else 2 if row.get("mid_long_eligible") is True else 1


def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -_rank_tier(row),
        -_finite(row.get("mid_long_ranking_score"), _finite(row.get("mid_long_score"))),
        -_finite(row.get("mid_long_score")),
        -_finite(row.get("score")),
        str(row.get("symbol") or ""),
    )


def select_mid_long_picks(
    rows: Iterable[dict[str, Any]],
    market: str,
    *,
    count: int,
    allocation_twd: int,
) -> list[dict[str, Any]]:
    market = market.upper()
    if market not in MARKETS:
        raise ValueError("unsupported market")
    ranked = sorted((dict(row) for row in rows if _is_stock(row, market)), key=_sort_key)
    picks = []
    for index, row in enumerate(ranked[:count], 1):
        picks.append({
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or row.get("symbol") or ""),
            "market": market,
            "rank": index,
            "rank_tier": _rank_tier(row),
            "ranking_score": round(_finite(
                row.get("mid_long_ranking_score"), _finite(row.get("mid_long_score"))
            ), 2),
            "allocation_twd": allocation_twd,
        })
    return picks


def _empty_medium_market(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "label": MARKET_LABELS[market],
        "capital_twd": MEDIUM_CAPITAL_TWD,
        "status": "waiting",
        "hold_days": MEDIUM_HOLD_DAYS,
        "pending": None,
        "invalid_entries": [],
        "positions": [],
        "benchmark_positions": [],
        "entry_session_date": None,
        "target_exit_date": None,
        "last_valuation_date": None,
        "gross_profit_twd": 0.0,
        "gross_return_pct": 0.0,
        "net_profit_twd": 0.0,
        "net_return_pct": 0.0,
        "benchmark_net_profit_twd": 0.0,
        "benchmark_net_return_pct": 0.0,
        "benchmark_realized": False,
        "realized": False,
    }


def _empty_long() -> dict[str, Any]:
    return {
        "label": "台美股長期組合",
        "capital_twd": LONG_CAPITAL_TWD,
        "status": "waiting",
        "hold_months": LONG_HOLD_MONTHS,
        "pending": {"TW": None, "US": None},
        "invalid_entries": [],
        "positions": [],
        "benchmark_positions": [],
        "last_valuation_date": {"TW": None, "US": None},
        "gross_profit_twd": 0.0,
        "gross_return_pct": 0.0,
        "net_profit_twd": 0.0,
        "net_return_pct": 0.0,
        "benchmark_net_profit_twd": 0.0,
        "benchmark_net_return_pct": 0.0,
        "benchmark_realized": False,
        "realized": False,
    }


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "web_shadow_only",
        "policy": {
            "start_date": START_DATE,
            "medium": {
                "capital_per_market_twd": MEDIUM_CAPITAL_TWD,
                "markets": ["台股", "美股"],
                "ranking": "中長線排名前5名",
                "allocation_per_pick_twd": MEDIUM_ALLOCATION_TWD,
                "hold_calendar_days": MEDIUM_HOLD_DAYS,
            },
            "long": {
                "total_capital_twd": LONG_CAPITAL_TWD,
                "ranking": "台股第1名＋美股第1名",
                "total_picks": 2,
                "allocation_per_pick_twd": LONG_ALLOCATION_TWD,
                "hold_calendar_months": LONG_HOLD_MONTHS,
            },
            "duplicates": "同一股票若同時入選中期與長期，兩個模擬帳戶都照買並分開計算",
            "entry": "2026-08-24起，使用前一完成交易日排名並按下一交易日官方開盤價",
            "entry_coverage": "中期5檔／長期各市場1檔與同期基準都取得同日官方開盤價後才建立持倉",
            "valuation_exit": "持有期間用官方收盤價估值；到期後第一個交易日官方收盤價賣出",
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "costs": "同時保留毛損益與估算淨損益；台股0.685%，美股0.20%；美股匯率另列不混入",
            "benchmark": {market: BENCHMARKS[market]["name"] for market in MARKETS},
            "benchmark_rule": "與模型同一交易日開盤進場、同一到期日收盤出場；中期各市場100萬元，長期台美各50萬元",
            "orders": "純網頁影子試走，不連接券商、不送單",
        },
        "medium": {market: _empty_medium_market(market) for market in MARKETS},
        "long": _empty_long(),
    }


def _load(path: Path, updated_at: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty_state(updated_at)
    if state.get("version") != VERSION or not isinstance(state.get("medium"), dict):
        return empty_state(updated_at)
    for market in MARKETS:
        state["medium"].setdefault(market, _empty_medium_market(market))
    state.setdefault("long", _empty_long())
    return state


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    dates = [
        str(row.get("official_session_date") or "")
        for row in rows
        if _is_stock(row, market) and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _add_months(value: str, months: int) -> str:
    source = date.fromisoformat(value)
    month_index = source.month - 1 + months
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    day = min(source.day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _new_pending(
    rows: list[dict[str, Any]],
    market: str,
    *,
    count: int,
    allocation_twd: int,
    updated_at: str,
) -> dict[str, Any] | None:
    signal_date = _session_date(rows, market)
    picks = select_mid_long_picks(rows, market, count=count, allocation_twd=allocation_twd)
    if not signal_date or len(picks) < count:
        return None
    payload = "|".join(
        f"{pick['rank']}:{pick['symbol']}:{pick['ranking_score']}:{pick['rank_tier']}"
        for pick in picks
    )
    snapshot_id = hashlib.sha256(
        f"{market}|{signal_date}|{count}|{allocation_twd}|{payload}".encode()
    ).hexdigest()[:12]
    return {
        "signal_session_date": signal_date,
        "created_at": updated_at,
        "snapshot_id": snapshot_id,
        "picks": picks,
    }


def prepare_pending(state: dict[str, Any], rows: list[dict[str, Any]], market: str, updated_at: str) -> None:
    medium = state["medium"][market]
    if medium.get("status") == "waiting" and medium.get("pending") is None:
        medium["pending"] = _new_pending(
            rows, market, count=MEDIUM_PICK_COUNT, allocation_twd=MEDIUM_ALLOCATION_TWD, updated_at=updated_at
        )
        if medium["pending"]:
            medium["status"] = "pending"
    long = state["long"]
    long.setdefault("pending", {"TW": None, "US": None})
    if long.get("status") in {"waiting", "pending"} and long["pending"].get(market) is None:
        long["pending"][market] = _new_pending(
            rows, market, count=LONG_PICK_COUNT_PER_MARKET, allocation_twd=LONG_ALLOCATION_TWD, updated_at=updated_at
        )
        if long["pending"][market]:
            long["status"] = "pending"
    for pending in (medium.get("pending"), long.get("pending", {}).get(market)):
        if pending and not pending.get("snapshot_id"):
            payload = "|".join(
                f"{pick.get('rank')}:{pick.get('symbol')}:{pick.get('ranking_score')}:{pick.get('rank_tier')}"
                for pick in pending.get("picks") or []
            )
            pending["snapshot_id"] = hashlib.sha256(
                f"{market}|{pending.get('signal_session_date')}|{payload}".encode()
            ).hexdigest()[:12]


def _enter_positions(
    pending: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    market: str,
    session_date: str,
    *,
    hold_days: int | None = None,
    hold_months: int | None = None,
) -> list[dict[str, Any]]:
    if not pending or session_date <= str(pending.get("signal_session_date") or "") or session_date < START_DATE:
        return []
    if not pending.get("snapshot_id"):
        payload = "|".join(
            f"{pick.get('rank')}:{pick.get('symbol')}:{pick.get('ranking_score')}:{pick.get('rank_tier')}"
            for pick in pending.get("picks") or []
        )
        pending["snapshot_id"] = hashlib.sha256(
            f"{market}|{pending.get('signal_session_date')}|{payload}".encode()
        ).hexdigest()[:12]
    row_map = {str(row.get("symbol") or ""): row for row in rows if _is_stock(row, market)}
    positions = []
    for pick in pending.get("picks") or []:
        row = row_map.get(str(pick.get("symbol") or ""))
        open_price = _finite(row.get("official_open_price")) if row else 0.0
        close_price = _finite(row.get("official_close_price")) if row else 0.0
        if not row or str(row.get("official_session_date") or "") != session_date or open_price <= 0:
            continue
        if hold_days is not None:
            target = (date.fromisoformat(session_date) + timedelta(days=hold_days)).isoformat()
        else:
            target = _add_months(session_date, int(hold_months or 0))
        allocation = _finite(pick.get("allocation_twd"))
        return_pct = ((close_price / open_price) - 1) * 100 if close_price > 0 else 0.0
        net_return_pct = return_pct - ROUND_TRIP_COST_PCT[market]
        positions.append({
            **pick,
            "entry_session_date": session_date,
            "entry_price": round(open_price, 4),
            "target_exit_date": target,
            "ranking_snapshot_id": pending.get("snapshot_id"),
            "last_price": round(close_price, 4) if close_price > 0 else round(open_price, 4),
            "last_valuation_date": session_date,
            "gross_return_pct": round(return_pct, 4),
            "gross_profit_twd": round(allocation * return_pct / 100, 2),
            "net_return_pct": round(net_return_pct, 4),
            "net_profit_twd": round(allocation * net_return_pct / 100, 2),
            "estimated_cost_twd": round(allocation * ROUND_TRIP_COST_PCT[market] / 100, 2),
            "exit_session_date": None,
            "exit_price": None,
            "realized": False,
        })
    return positions


def _enter_benchmark_position(
    rows: list[dict[str, Any]], market: str, session_date: str, allocation_twd: int, *,
    hold_days: int | None = None, hold_months: int | None = None,
) -> list[dict[str, Any]]:
    config = BENCHMARKS[market]
    row = next((item for item in rows if str(item.get("symbol") or "") == config["symbol"]), None)
    open_price = _finite(row.get("official_open_price")) if row else 0.0
    close_price = _finite(row.get("official_close_price")) if row else 0.0
    if not row or str(row.get("official_session_date") or "") != session_date or open_price <= 0:
        return []
    target = (
        (date.fromisoformat(session_date) + timedelta(days=hold_days)).isoformat()
        if hold_days is not None else _add_months(session_date, int(hold_months or 0))
    )
    gross_return = (close_price / open_price - 1) * 100 if close_price > 0 else 0.0
    net_return = gross_return - ROUND_TRIP_COST_PCT[market]
    return [{
        **config,
        "market": market,
        "allocation_twd": allocation_twd,
        "entry_session_date": session_date,
        "entry_price": round(open_price, 4),
        "target_exit_date": target,
        "last_price": round(close_price, 4) if close_price > 0 else round(open_price, 4),
        "last_valuation_date": session_date,
        "gross_return_pct": round(gross_return, 4),
        "gross_profit_twd": round(allocation_twd * gross_return / 100, 2),
        "net_return_pct": round(net_return, 4),
        "net_profit_twd": round(allocation_twd * net_return / 100, 2),
        "estimated_cost_twd": round(allocation_twd * ROUND_TRIP_COST_PCT[market] / 100, 2),
        "exit_session_date": None,
        "exit_price": None,
        "realized": False,
    }]


def _entry_coverage_ready(
    owner: dict[str, Any], pending: dict[str, Any], rows: list[dict[str, Any]],
    market: str, session_date: str, expected_count: int,
) -> bool:
    execution_date = str(pending.get("execution_session_date") or "")
    if execution_date and session_date > execution_date:
        owner.setdefault("invalid_entries", []).append({
            "status": "data_insufficient",
            "market": market,
            "signal_session_date": pending.get("signal_session_date"),
            "entry_session_date": execution_date,
            "snapshot_id": pending.get("snapshot_id"),
            "available_positions": pending.get("available_positions", 0),
            "required_positions": expected_count,
            "missing_symbols": pending.get("missing_symbols", []),
        })
        owner["invalid_entries"] = owner["invalid_entries"][-20:]
        return False
    pending.setdefault("execution_session_date", session_date)
    row_map = {str(row.get("symbol") or ""): row for row in rows}
    missing = []
    available = 0
    for pick in pending.get("picks") or []:
        row = row_map.get(str(pick.get("symbol") or ""))
        price = _finite(row.get("official_open_price")) if row and str(row.get("official_session_date") or "") == session_date else 0.0
        if price > 0:
            available += 1
        else:
            missing.append(str(pick.get("symbol") or ""))
    benchmark = row_map.get(BENCHMARKS[market]["symbol"])
    benchmark_open = _finite(benchmark.get("official_open_price")) if benchmark and str(benchmark.get("official_session_date") or "") == session_date else 0.0
    if benchmark_open <= 0:
        missing.append(BENCHMARKS[market]["symbol"])
    complete = available == expected_count and len(pending.get("picks") or []) == expected_count and benchmark_open > 0
    if not complete:
        pending.update({
            "settlement_status": "waiting_for_official_prices",
            "available_positions": available,
            "required_positions": expected_count,
            "missing_symbols": list(dict.fromkeys(missing)),
        })
    return complete


def _quarantine_legacy_partial_medium(portfolio: dict[str, Any]) -> None:
    positions = portfolio.get("positions") or []
    benchmark_positions = portfolio.get("benchmark_positions") or []
    if not positions or (
        len(positions) == MEDIUM_PICK_COUNT and len(benchmark_positions) == 1
    ):
        portfolio.setdefault("invalid_entries", [])
        return
    market = str(portfolio.get("market") or "US")
    invalid = list(portfolio.get("invalid_entries") or [])
    invalid.append({
        "status": "data_insufficient",
        "market": market,
        "reason": "舊版在5檔官方開盤價未完整時提前建立中期持倉，已撤銷且不列成績",
        "entry_session_date": portfolio.get("entry_session_date"),
        "available_positions": len(positions),
        "required_positions": MEDIUM_PICK_COUNT,
        "symbols_with_data": [str(position.get("symbol") or "") for position in positions],
    })
    replacement = _empty_medium_market(market)
    replacement["invalid_entries"] = invalid[-20:]
    portfolio.clear()
    portfolio.update(replacement)


def _value_positions(positions: list[dict[str, Any]], rows: list[dict[str, Any]], market: str, session_date: str) -> None:
    row_map = {str(row.get("symbol") or ""): row for row in rows if _is_stock(row, market)}
    for position in positions:
        if position.get("market") != market or position.get("realized"):
            continue
        row = row_map.get(str(position.get("symbol") or ""))
        close_price = _finite(row.get("official_close_price")) if row else 0.0
        if not row or str(row.get("official_session_date") or "") != session_date or close_price <= 0:
            continue
        entry_price = _finite(position.get("entry_price"))
        allocation = _finite(position.get("allocation_twd"))
        return_pct = ((close_price / entry_price) - 1) * 100 if entry_price > 0 else 0.0
        net_return_pct = return_pct - ROUND_TRIP_COST_PCT[market]
        position["last_price"] = round(close_price, 4)
        position["last_valuation_date"] = session_date
        position["gross_return_pct"] = round(return_pct, 4)
        position["gross_profit_twd"] = round(allocation * return_pct / 100, 2)
        position["net_return_pct"] = round(net_return_pct, 4)
        position["net_profit_twd"] = round(allocation * net_return_pct / 100, 2)
        position["estimated_cost_twd"] = round(allocation * ROUND_TRIP_COST_PCT[market] / 100, 2)
        if session_date >= str(position.get("target_exit_date") or "9999-12-31"):
            position["exit_session_date"] = session_date
            position["exit_price"] = round(close_price, 4)
            position["realized"] = True


def _value_benchmark_positions(
    positions: list[dict[str, Any]], rows: list[dict[str, Any]], market: str, session_date: str
) -> None:
    config = BENCHMARKS[market]
    row = next((item for item in rows if str(item.get("symbol") or "") == config["symbol"]), None)
    close_price = _finite(row.get("official_close_price")) if row else 0.0
    if not row or str(row.get("official_session_date") or "") != session_date or close_price <= 0:
        return
    for position in positions:
        if position.get("market") != market or position.get("realized"):
            continue
        entry_price = _finite(position.get("entry_price"))
        allocation = _finite(position.get("allocation_twd"))
        gross_return = (close_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
        net_return = gross_return - ROUND_TRIP_COST_PCT[market]
        position["last_price"] = round(close_price, 4)
        position["last_valuation_date"] = session_date
        position["gross_return_pct"] = round(gross_return, 4)
        position["gross_profit_twd"] = round(allocation * gross_return / 100, 2)
        position["net_return_pct"] = round(net_return, 4)
        position["net_profit_twd"] = round(allocation * net_return / 100, 2)
        if session_date >= str(position.get("target_exit_date") or "9999-12-31"):
            position["exit_session_date"] = session_date
            position["exit_price"] = round(close_price, 4)
            position["realized"] = True


def _summarize(portfolio: dict[str, Any], positions: list[dict[str, Any]]) -> None:
    profit = sum(_finite(position.get("gross_profit_twd")) for position in positions)
    net_profit = sum(_finite(position.get("net_profit_twd")) for position in positions)
    capital = _finite(portfolio.get("capital_twd"))
    portfolio["gross_profit_twd"] = round(profit, 2)
    portfolio["gross_return_pct"] = round(profit / capital * 100, 4) if capital else 0.0
    portfolio["net_profit_twd"] = round(net_profit, 2)
    portfolio["net_return_pct"] = round(net_profit / capital * 100, 4) if capital else 0.0
    portfolio["realized"] = bool(positions) and all(position.get("realized") is True for position in positions)
    if portfolio["realized"]:
        portfolio["status"] = "complete"
    elif positions:
        portfolio["status"] = "active"


def _summarize_benchmark(portfolio: dict[str, Any]) -> None:
    positions = portfolio.get("benchmark_positions") or []
    capital = _finite(portfolio.get("capital_twd"))
    net_profit = sum(_finite(position.get("net_profit_twd")) for position in positions)
    portfolio["benchmark_net_profit_twd"] = round(net_profit, 2)
    portfolio["benchmark_net_return_pct"] = round(net_profit / capital * 100, 4) if capital else 0.0
    portfolio["benchmark_realized"] = bool(positions) and all(
        position.get("realized") is True for position in positions
    )
    if positions and portfolio.get("realized") and not portfolio["benchmark_realized"]:
        portfolio["status"] = "active"


def _update_medium(state: dict[str, Any], rows: list[dict[str, Any]], market: str, session_date: str) -> None:
    portfolio = state["medium"][market]
    if portfolio.get("status") == "complete":
        return
    if not portfolio.get("positions") and portfolio.get("pending"):
        if session_date < START_DATE or session_date <= str(portfolio["pending"].get("signal_session_date") or ""):
            return
        if not _entry_coverage_ready(
            portfolio, portfolio["pending"], rows, market, session_date, MEDIUM_PICK_COUNT
        ):
            if session_date > str(portfolio["pending"].get("execution_session_date") or session_date):
                portfolio["pending"] = None
                portfolio["status"] = "waiting"
            else:
                portfolio["status"] = "waiting_data"
            return
        positions = _enter_positions(
            portfolio["pending"], rows, market, session_date, hold_days=MEDIUM_HOLD_DAYS
        )
        if positions:
            portfolio["positions"] = positions
            portfolio["benchmark_positions"] = _enter_benchmark_position(
                rows, market, session_date, MEDIUM_CAPITAL_TWD, hold_days=MEDIUM_HOLD_DAYS
            )
            portfolio["pending"] = None
            portfolio["entry_session_date"] = session_date
            portfolio["target_exit_date"] = positions[0]["target_exit_date"]
            portfolio["status"] = "active"
    _value_positions(portfolio.get("positions") or [], rows, market, session_date)
    _value_benchmark_positions(
        portfolio.get("benchmark_positions") or [], rows, market, session_date
    )
    if portfolio.get("positions"):
        portfolio["last_valuation_date"] = session_date
    _summarize(portfolio, portfolio.get("positions") or [])
    _summarize_benchmark(portfolio)


def _update_long(state: dict[str, Any], rows: list[dict[str, Any]], market: str, session_date: str) -> None:
    portfolio = state["long"]
    if portfolio.get("status") == "complete":
        return
    pending = portfolio.setdefault("pending", {"TW": None, "US": None}).get(market)
    has_market_position = any(position.get("market") == market for position in portfolio.get("positions") or [])
    if not has_market_position and pending:
        if session_date < START_DATE or session_date <= str(pending.get("signal_session_date") or ""):
            return
        if not _entry_coverage_ready(
            portfolio, pending, rows, market, session_date, LONG_PICK_COUNT_PER_MARKET
        ):
            if session_date > str(pending.get("execution_session_date") or session_date):
                portfolio["pending"][market] = None
            return
        positions = _enter_positions(pending, rows, market, session_date, hold_months=LONG_HOLD_MONTHS)
        if positions:
            portfolio.setdefault("positions", []).extend(positions)
            portfolio.setdefault("benchmark_positions", []).extend(_enter_benchmark_position(
                rows, market, session_date, LONG_ALLOCATION_TWD, hold_months=LONG_HOLD_MONTHS
            ))
            portfolio["pending"][market] = None
            portfolio["status"] = "active"
    _value_positions(portfolio.get("positions") or [], rows, market, session_date)
    _value_benchmark_positions(
        portfolio.get("benchmark_positions") or [], rows, market, session_date
    )
    portfolio.setdefault("last_valuation_date", {"TW": None, "US": None})[market] = session_date
    _summarize(portfolio, portfolio.get("positions") or [])
    _summarize_benchmark(portfolio)
    # The long portfolio is not complete until both intended market positions
    # have entered and both have reached their own six-month exit date.
    if len(portfolio.get("positions") or []) < 2:
        portfolio["realized"] = False
        portfolio["status"] = "active" if portfolio.get("positions") else "pending"


def update_state(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> dict[str, Any]:
    state["updated_at"] = updated_at
    state.setdefault("policy", {})["round_trip_cost_pct"] = ROUND_TRIP_COST_PCT
    state["policy"]["costs"] = (
        "同時保留毛損益與估算淨損益；台股0.685%，美股0.20%；美股匯率另列不混入"
    )
    state["policy"]["benchmark"] = {market: BENCHMARKS[market]["name"] for market in MARKETS}
    state["policy"]["benchmark_rule"] = (
        "與模型同一交易日開盤進場、同一到期日收盤出場；"
        "中期各市場100萬元，長期台美各50萬元"
    )
    state["policy"]["entry_coverage"] = (
        "中期5檔／長期各市場1檔與同期基準都取得同日官方開盤價後才建立持倉；"
        "不足即等待補抓或隔離"
    )
    for market in MARKETS:
        state["medium"].setdefault(market, _empty_medium_market(market))
        state["medium"][market].setdefault("net_profit_twd", 0.0)
        state["medium"][market].setdefault("net_return_pct", 0.0)
        state["medium"][market].setdefault("benchmark_positions", [])
        state["medium"][market].setdefault("benchmark_net_profit_twd", 0.0)
        state["medium"][market].setdefault("benchmark_net_return_pct", 0.0)
        state["medium"][market].setdefault("benchmark_realized", False)
        state["medium"][market].setdefault("invalid_entries", [])
        _quarantine_legacy_partial_medium(state["medium"][market])
        state.setdefault("long", _empty_long()).setdefault("net_profit_twd", 0.0)
        state["long"].setdefault("net_return_pct", 0.0)
        state["long"].setdefault("benchmark_positions", [])
        state["long"].setdefault("benchmark_net_profit_twd", 0.0)
        state["long"].setdefault("benchmark_net_return_pct", 0.0)
        state["long"].setdefault("benchmark_realized", False)
        state["long"].setdefault("invalid_entries", [])
        if intraday or period != CLOSED_PERIOD[market]:
            continue
        prepare_pending(state, rows, market, updated_at)
        session_date = _session_date(rows, market)
        if not session_date:
            continue
        _update_medium(state, rows, market, session_date)
        _update_long(state, rows, market, session_date)
    return state


def update_holding_simulation(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> Path:
    path = reports_dir / "holding_simulation.json"
    state = update_state(_load(path, updated_at), rows, period=period, updated_at=updated_at, intraday=intraday)
    tmp = reports_dir / "holding_simulation.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update medium/long web shadow portfolios")
    parser.add_argument("--rankings", default="reports/rankings.json")
    parser.add_argument("--period", choices=("morning", "noon", "evening"), default="evening")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()
    payload = json.loads(Path(args.rankings).read_text(encoding="utf-8"))
    stamp = str(payload.get("updated_at") or datetime.now().isoformat(timespec="seconds"))
    output = update_holding_simulation(
        Path(args.reports_dir), list(payload.get("data") or []), period=args.period, updated_at=stamp
    )
    print(output)
