"""Five-session, one-million-TWD shadow portfolios for Taiwan and US stocks.

The experiment is intentionally separate from every live-trading component.
Selections are frozen from a completed-session ranking snapshot, bought at the
next official session open, and sold using that session's official close as the
verifiable proxy for a pre-close exit.  No broker order path imports this file.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable


VERSION = 1
CAPITAL_PER_MARKET = 1_000_000
ALLOCATION_PER_PICK = 50_000
PICKS_PER_STRATEGY = 10
TARGET_TRADING_DAYS = 5
START_DATE = "2026-08-24"
STRATEGIES = ("overall", "short")
MARKETS = ("TW", "US")
MARKET_LABELS = {"TW": "台股", "US": "美股"}
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}


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


def _tier(row: dict[str, Any], prefix: str) -> int:
    explicit = row.get(f"{prefix}_rank_tier")
    if explicit is not None:
        return max(0, min(2, int(_finite(explicit))))
    eligible = row.get(f"{prefix}_eligible") is True
    blocked = bool(row.get("trade_guard_blocked") or row.get("market_contract_valid") is False)
    return 0 if blocked else 2 if eligible else 1


def _sort_key(row: dict[str, Any], strategy: str) -> tuple[Any, ...]:
    if strategy == "short":
        score = _finite(row.get("short_term_ranking_score"), _finite(row.get("short_term_score")))
        secondary = _finite(row.get("short_term_score"))
        tier = _tier(row, "short_term")
    else:
        score = _finite(row.get("overall_ranking_score"), _finite(row.get("score")))
        secondary = _finite(row.get("entry_score"))
        tier = _tier(row, "overall")
    return (-tier, -score, -secondary, str(row.get("symbol") or ""))


def select_picks(rows: Iterable[dict[str, Any]], market: str, strategy: str) -> list[dict[str, Any]]:
    """Return the displayed top ten for one market and ranking strategy.

    This is a ranking experiment, so it deliberately keeps the displayed top
    ten even when a row is observation-only.  Its tier is persisted so the UI
    can show that fact instead of pretending every simulated pick was eligible.
    """
    market = market.upper()
    if market not in MARKETS or strategy not in STRATEGIES:
        raise ValueError("unsupported market or strategy")
    ranked = sorted((dict(row) for row in rows if _is_stock(row, market)), key=lambda row: _sort_key(row, strategy))
    prefix = "short_term" if strategy == "short" else "overall"
    picks = []
    for index, row in enumerate(ranked[:PICKS_PER_STRATEGY], 1):
        picks.append({
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or row.get("symbol") or ""),
            "rank": index,
            "rank_tier": _tier(row, prefix),
            "ranking_score": round(_finite(
                row.get("short_term_ranking_score") if strategy == "short" else row.get("overall_ranking_score"),
                _finite(row.get("short_term_score") if strategy == "short" else row.get("score")),
            ), 2),
            "allocation_twd": ALLOCATION_PER_PICK,
        })
    return picks


def _empty_market(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "label": MARKET_LABELS[market],
        "capital_twd": CAPITAL_PER_MARKET,
        "target_trading_days": TARGET_TRADING_DAYS,
        "completed_days": 0,
        "status": "waiting",
        "cumulative_gross_profit_twd": 0.0,
        "cumulative_gross_return_pct": 0.0,
        "days": [],
        "pending": None,
    }


def empty_state(updated_at: str = "") -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "web_shadow_only",
        "policy": {
            "capital_per_market_twd": CAPITAL_PER_MARKET,
            "allocation_per_pick_twd": ALLOCATION_PER_PICK,
            "picks_per_strategy": PICKS_PER_STRATEGY,
            "strategies": ["綜合排名前10名", "短線排名前10名"],
            "target_trading_days": TARGET_TRADING_DAYS,
            "start_date": START_DATE,
            "entry": "前一個完成交易日排名快照；下一交易日官方開盤價",
            "exit": "同一交易日官方收盤價，作為收盤前賣出代理",
            "costs": "第一階段顯示毛損益；未扣手續費、證交稅與匯差",
            "orders": "純網頁影子試走，不連接券商、不送單",
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


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    dates = [
        str(row.get("official_session_date") or "")
        for row in rows
        if _is_stock(row, market) and row.get("official_session_date")
    ]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _new_pending(rows: list[dict[str, Any]], market: str, updated_at: str) -> dict[str, Any] | None:
    signal_date = _session_date(rows, market)
    strategies = {strategy: select_picks(rows, market, strategy) for strategy in STRATEGIES}
    if not signal_date or any(len(strategies[strategy]) < PICKS_PER_STRATEGY for strategy in STRATEGIES):
        return None
    return {
        "signal_session_date": signal_date,
        "created_at": updated_at,
        "execution_session_date": None,
        "strategies": strategies,
    }


def _settle_strategy(
    picks: list[dict[str, Any]],
    row_map: dict[str, dict[str, Any]],
    session_date: str,
) -> dict[str, Any]:
    positions = []
    profit = 0.0
    invested = 0.0
    for pick in picks:
        row = row_map.get(str(pick.get("symbol") or ""))
        open_price = _finite(row.get("official_open_price")) if row else 0.0
        close_price = _finite(row.get("official_close_price")) if row else 0.0
        allocation = _finite(pick.get("allocation_twd"), ALLOCATION_PER_PICK)
        available = bool(
            row
            and str(row.get("official_session_date") or "") == session_date
            and open_price > 0
            and close_price > 0
        )
        return_pct = ((close_price / open_price) - 1) * 100 if available else 0.0
        position_profit = allocation * return_pct / 100 if available else 0.0
        if available:
            invested += allocation
            profit += position_profit
        positions.append({
            **pick,
            "open_price": round(open_price, 4) if available else None,
            "sell_price": round(close_price, 4) if available else None,
            "gross_return_pct": round(return_pct, 4) if available else None,
            "gross_profit_twd": round(position_profit, 2) if available else 0.0,
            "data_available": available,
        })
    return {
        "planned_capital_twd": PICKS_PER_STRATEGY * ALLOCATION_PER_PICK,
        "invested_twd": round(invested, 2),
        "idle_twd": round(PICKS_PER_STRATEGY * ALLOCATION_PER_PICK - invested, 2),
        "gross_profit_twd": round(profit, 2),
        "gross_return_pct": round(profit / (PICKS_PER_STRATEGY * ALLOCATION_PER_PICK) * 100, 4),
        "positions": positions,
    }


def _settle_pending(market_state: dict[str, Any], rows: list[dict[str, Any]], market: str) -> bool:
    pending = market_state.get("pending")
    if not pending:
        return False
    session_date = _session_date(rows, market)
    signal_date = str(pending.get("signal_session_date") or "")
    if not session_date or session_date < START_DATE or session_date <= signal_date:
        return False
    row_map = {
        str(row.get("symbol") or ""): row
        for row in rows
        if _is_stock(row, market)
    }
    strategies = {
        strategy: _settle_strategy(pending.get("strategies", {}).get(strategy, []), row_map, session_date)
        for strategy in STRATEGIES
    }
    total_profit = sum(_finite(result.get("gross_profit_twd")) for result in strategies.values())
    day = {
        "day": len(market_state.get("days") or []) + 1,
        "signal_session_date": signal_date,
        "session_date": session_date,
        "buy_price": "official_open_price",
        "sell_price": "official_close_price",
        "gross_profit_twd": round(total_profit, 2),
        "gross_return_pct": round(total_profit / CAPITAL_PER_MARKET * 100, 4),
        "ending_capital_twd": round(CAPITAL_PER_MARKET + total_profit, 2),
        "strategies": strategies,
    }
    market_state.setdefault("days", []).append(day)
    market_state["pending"] = None
    market_state["completed_days"] = len(market_state["days"])
    cumulative = sum(_finite(item.get("gross_profit_twd")) for item in market_state["days"])
    market_state["cumulative_gross_profit_twd"] = round(cumulative, 2)
    market_state["cumulative_gross_return_pct"] = round(cumulative / CAPITAL_PER_MARKET * 100, 4)
    market_state["status"] = "complete" if market_state["completed_days"] >= TARGET_TRADING_DAYS else "running"
    return True


def update_state(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> dict[str, Any]:
    state["updated_at"] = updated_at
    for market in MARKETS:
        market_state = state["markets"].setdefault(market, _empty_market(market))
        if intraday or period != CLOSED_PERIOD[market]:
            continue
        if market_state.get("status") == "complete":
            continue
        _settle_pending(market_state, rows, market)
        if market_state.get("completed_days", 0) >= TARGET_TRADING_DAYS:
            market_state["status"] = "complete"
            market_state["pending"] = None
            continue
        if market_state.get("pending") is None:
            pending = _new_pending(rows, market, updated_at)
            if pending:
                market_state["pending"] = pending
                market_state["status"] = "running"
    return state


def update_million_simulation(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> Path:
    path = reports_dir / "million_simulation.json"
    state = update_state(_load(path, updated_at), rows, period=period, updated_at=updated_at, intraday=intraday)
    tmp = reports_dir / "million_simulation.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update the five-session million-TWD web shadow test")
    parser.add_argument("--rankings", default="reports/rankings.json")
    parser.add_argument("--period", choices=("morning", "noon", "evening"), default="evening")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()
    payload = json.loads(Path(args.rankings).read_text(encoding="utf-8"))
    stamp = str(payload.get("updated_at") or datetime.now().isoformat(timespec="seconds"))
    output = update_million_simulation(
        Path(args.reports_dir), list(payload.get("data") or []), period=args.period, updated_at=stamp
    )
    print(output)
