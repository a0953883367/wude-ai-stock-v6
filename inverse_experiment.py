"""Forward-only bear-market A/B/C shadow experiment.

A keeps the frozen V6 overall TOP10 long, B remains in cash, and C buys one
liquid daily -1x broad-market ETF.  The inverse instruments are loaded from a
dedicated watchlist and are never returned to the ranking universe.  This
module has no broker import and cannot place orders.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from million_simulation import ROUND_TRIP_COST_PCT, select_picks


VERSION = 1
CAPITAL_TWD = 1_000_000
HORIZONS = (1, 2, 3, 5)
MARKETS = ("TW", "US")
CLOSED_PERIOD = {"TW": "evening", "US": "morning"}
# Taiwan ETF tax is lower than stock tax.  Both estimates also include fees
# and a small execution allowance.  They are deliberately conservative.
INVERSE_ROUND_TRIP_COST_PCT = {"TW": 0.485, "US": 0.20}
STRATEGIES = ("A_TOP10_LONG", "B_CASH", "C_INVERSE_ETF")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_inverse_experiment_watchlist(path: Path | None = None) -> list[dict[str, Any]]:
    """Load and validate the isolated -1x experiment universe."""
    target = path or Path(__file__).with_name("inverse_experiment_watchlist.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("inverse experiment watchlist must contain data[]")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    markets: Counter[str] = Counter()
    for raw in rows:
        item = dict(raw)
        symbol = str(item.get("symbol") or "").upper()
        market = str(item.get("market") or "").upper()
        if not symbol or symbol in seen or market not in MARKETS:
            raise ValueError("inverse experiment watchlist has invalid/duplicate symbol")
        if item.get("type") != "INVERSE_ETF" or _finite(item.get("daily_target")) != -1.0:
            raise ValueError("inverse experiment instruments must be daily -1x ETFs")
        item["symbol"] = symbol
        item["market"] = market
        result.append(item)
        seen.add(symbol)
        markets[market] += 1
    if any(markets[market] != 1 for market in MARKETS):
        raise ValueError("exactly one inverse ETF is required for each market")
    return result


def _is_stock(row: dict[str, Any], market: str) -> bool:
    return (
        str(row.get("market") or "").upper() == market
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _session_date(rows: Iterable[dict[str, Any]], market: str) -> str:
    values = [
        str(row.get("official_session_date") or "")
        for row in rows
        if _is_stock(row, market) and row.get("official_session_date")
    ]
    return Counter(values).most_common(1)[0][0] if values else ""


def _history_value(row: Any, *names: str) -> float | None:
    for name in names:
        try:
            value = row.get(name)
        except AttributeError:
            value = None
        number = _finite(value)
        if number is not None:
            return number
    return None


def build_inverse_market_rows(
    watchlist: list[dict[str, Any]],
    histories: dict[str, Any],
    session_dates: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Extract only the exact completed session; never back/forward fill."""
    output: dict[str, dict[str, Any]] = {}
    for item in watchlist:
        market = item["market"]
        expected = str(session_dates.get(market) or "")
        frame = histories.get(item["symbol"])
        matched = None
        if expected and frame is not None and not getattr(frame, "empty", True):
            for index, row in frame.iterrows():
                try:
                    trade_date = index.date().isoformat()
                except AttributeError:
                    trade_date = str(index)[:10]
                if trade_date == expected:
                    matched = row
                    break
        output[market] = {
            **item,
            "session_date": expected or None,
            "open": _history_value(matched, "open", "Open") if matched is not None else None,
            "close": _history_value(matched, "close", "Close") if matched is not None else None,
            "data_complete": bool(
                matched is not None
                and (_history_value(matched, "open", "Open") or 0) > 0
                and (_history_value(matched, "close", "Close") or 0) > 0
            ),
            "price_policy": "exact_completed_session_only_no_fill",
        }
    return output


def _market_shell(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "status": "waiting_for_bear_signal",
        "cohorts": [],
        "quarantined_count": 0,
        "summary": {},
    }


def empty_state(updated_at: str = "", watchlist: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    selected = watchlist or load_inverse_experiment_watchlist()
    return {
        "version": VERSION,
        "updated_at": updated_at,
        "mode": "web_shadow_only",
        "status": "waiting_for_bear_signal",
        "title": "🔴空頭反向影子實驗",
        "policy": {
            "comparison": {"A": "原綜合排名TOP10做多", "B": "100%現金", "C": "每日-1x反向ETF"},
            "markets_separate": True,
            "signal": "各市場完成收盤後，用當日及以前資料判定空頭並凍結",
            "entry": "空頭訊號後下一個完成交易日官方開盤／ETF精確日線開盤",
            "horizons": list(HORIZONS),
            "missing_data": "任一必要價格缺失即隔離；不補前值、不硬算、不列有效樣本",
            "overlapping_cohorts": "每個空頭訊號為獨立研究樣本；累積報酬與回撤是樣本序列統計，不冒充單一可同時執行帳戶",
            "formal_v6_modified": False,
            "all_universe_modified": False,
            "top10_modified": False,
            "formal_watchlist_modified": False,
            "sixty_day_gate_modified": False,
            "automatic_merge": False,
            "broker_orders": False,
        },
        "inverse_experiment_watchlist": selected,
        "markets": {market: _market_shell(market) for market in MARKETS},
    }


def _load(path: Path, updated_at: str, watchlist: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return empty_state(updated_at, watchlist)
    if state.get("version") != VERSION or not isinstance(state.get("markets"), dict):
        return empty_state(updated_at, watchlist)
    state["inverse_experiment_watchlist"] = watchlist
    for market in MARKETS:
        state["markets"].setdefault(market, _market_shell(market))
    return state


def _snapshot_payload(cohort: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": cohort.get("market"),
        "signal_session_date": cohort.get("signal_session_date"),
        "regime_snapshot": cohort.get("regime_snapshot"),
        "top10": cohort.get("top10"),
        "inverse_etf": cohort.get("inverse_etf"),
    }


def _snapshot_hash(cohort: dict[str, Any]) -> str:
    raw = json.dumps(_snapshot_payload(cohort), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _freeze_cohort(
    rows: list[dict[str, Any]],
    market: str,
    session_date: str,
    regime: dict[str, Any],
    inverse_row: dict[str, Any] | None,
    updated_at: str,
) -> dict[str, Any]:
    picks = select_picks(rows, market, "overall")
    missing: list[str] = []
    if len(picks) != 10:
        missing.append(f"top10_count:{len(picks)}")
    row_map = {
        str(row.get("symbol") or ""): row
        for row in rows
        if str(row.get("official_session_date") or "") == session_date
    }
    frozen_picks = []
    for pick in picks:
        source_close = _finite((row_map.get(pick["symbol"]) or {}).get("official_close_price"))
        if source_close is None or source_close <= 0:
            missing.append(f"top10_signal_close:{pick['symbol']}")
        frozen_picks.append({**pick, "signal_close": round(source_close, 6) if source_close else None})
    inverse_close = _finite((inverse_row or {}).get("close"))
    if not inverse_row or inverse_row.get("session_date") != session_date or not inverse_row.get("data_complete"):
        missing.append("inverse_signal_price")
    benchmark_close = _finite(regime.get("benchmark_close"))
    if benchmark_close is None or benchmark_close <= 0:
        missing.append("benchmark_signal_close")
    cohort = {
        "cohort_id": f"{market}:{session_date}",
        "market": market,
        "created_at": updated_at,
        "signal_session_date": session_date,
        "status": "quarantined" if missing else "pending_entry",
        "regime_snapshot": {
            "regime": "bear",
            "benchmark": regime.get("benchmark"),
            "benchmark_close": benchmark_close,
            "ma20": _finite(regime.get("ma20")),
            "ma60": _finite(regime.get("ma60")),
            "return20_pct": _finite(regime.get("return20_pct")),
            "ma20_slope5_pct": _finite(regime.get("ma20_slope5_pct")),
            "classification_rule": regime.get("classification_rule"),
        },
        "top10": frozen_picks,
        "inverse_etf": {
            "symbol": (inverse_row or {}).get("symbol"),
            "name": (inverse_row or {}).get("name"),
            "daily_target": -1,
            "signal_close": round(inverse_close, 6) if inverse_close else None,
        },
        "entry": None,
        "observed_sessions": [],
        "outcomes": {},
        "quarantine_reasons": sorted(set(missing)),
    }
    cohort["integrity_sha256"] = _snapshot_hash(cohort)
    return cohort


def _quarantine(cohort: dict[str, Any], reasons: list[str]) -> None:
    cohort["status"] = "quarantined"
    cohort["quarantine_reasons"] = sorted(set((cohort.get("quarantine_reasons") or []) + reasons))


def _open_cohort(
    cohort: dict[str, Any],
    rows: list[dict[str, Any]],
    session_date: str,
    inverse_row: dict[str, Any] | None,
    benchmark: dict[str, Any],
) -> bool:
    if cohort.get("status") != "pending_entry" or session_date <= str(cohort.get("signal_session_date") or ""):
        return False
    if cohort.get("integrity_sha256") != _snapshot_hash(cohort):
        _quarantine(cohort, ["frozen_snapshot_hash_mismatch"])
        return False
    row_map = {
        str(row.get("symbol") or ""): row
        for row in rows
        if str(row.get("official_session_date") or "") == session_date
    }
    entries: dict[str, float] = {}
    missing: list[str] = []
    for pick in cohort.get("top10") or []:
        price = _finite((row_map.get(str(pick.get("symbol"))) or {}).get("official_open_price"))
        if price is None or price <= 0:
            missing.append(f"top10_entry_open:{pick.get('symbol')}")
        else:
            entries[str(pick["symbol"])] = price
    inverse_open = _finite((inverse_row or {}).get("open"))
    if (
        not inverse_row
        or inverse_row.get("session_date") != session_date
        or inverse_open is None
        or inverse_open <= 0
    ):
        missing.append("inverse_entry_open")
    benchmark_open = _finite(benchmark.get("benchmark_open"))
    if benchmark_open is None or benchmark_open <= 0:
        missing.append("benchmark_entry_open")
    if missing:
        _quarantine(cohort, missing)
        return False
    cohort["entry"] = {
        "session_date": session_date,
        "top10_open": {key: round(value, 6) for key, value in entries.items()},
        "inverse_open": round(float(inverse_open), 6),
        "benchmark_open": round(float(benchmark_open), 6),
    }
    cohort["observed_sessions"] = [session_date]
    cohort["status"] = "running"
    return True


def _invalid_outcome(cohort: dict[str, Any], horizon: int, session_date: str, reasons: list[str]) -> None:
    cohort["outcomes"][str(horizon)] = {
        "holding_sessions": horizon,
        "exit_session_date": session_date,
        "status": "invalid_data",
        "quarantine_reasons": sorted(set(reasons)),
    }


def _settle(
    cohort: dict[str, Any],
    rows: list[dict[str, Any]],
    session_date: str,
    inverse_row: dict[str, Any] | None,
    benchmark: dict[str, Any],
    horizon: int,
) -> None:
    key = str(horizon)
    if key in cohort.get("outcomes", {}) or len(cohort.get("observed_sessions") or []) < horizon:
        return
    market = str(cohort.get("market"))
    row_map = {
        str(row.get("symbol") or ""): row
        for row in rows
        if str(row.get("official_session_date") or "") == session_date
    }
    stock_returns: list[float] = []
    missing: list[str] = []
    entries = (cohort.get("entry") or {}).get("top10_open") or {}
    for pick in cohort.get("top10") or []:
        symbol = str(pick.get("symbol") or "")
        entry = _finite(entries.get(symbol))
        close = _finite((row_map.get(symbol) or {}).get("official_close_price"))
        if entry is None or entry <= 0 or close is None or close <= 0:
            missing.append(f"top10_exit_close:{symbol}")
        else:
            stock_returns.append((close / entry - 1) * 100 - ROUND_TRIP_COST_PCT[market])
    inverse_entry = _finite((cohort.get("entry") or {}).get("inverse_open"))
    inverse_close = _finite((inverse_row or {}).get("close"))
    if (
        not inverse_row
        or inverse_row.get("session_date") != session_date
        or inverse_entry is None
        or inverse_entry <= 0
        or inverse_close is None
        or inverse_close <= 0
    ):
        missing.append("inverse_exit_close")
    benchmark_entry = _finite((cohort.get("entry") or {}).get("benchmark_open"))
    benchmark_close = _finite(benchmark.get("benchmark_close"))
    if benchmark_entry is None or benchmark_entry <= 0 or benchmark_close is None or benchmark_close <= 0:
        missing.append("benchmark_exit_close")
    if len(stock_returns) != 10:
        missing.append(f"top10_valid_exit_count:{len(stock_returns)}")
    if missing:
        _invalid_outcome(cohort, horizon, session_date, missing)
        return
    a_return = sum(stock_returns) / 10
    c_return = (float(inverse_close) / float(inverse_entry) - 1) * 100 - INVERSE_ROUND_TRIP_COST_PCT[market]
    benchmark_return = (float(benchmark_close) / float(benchmark_entry) - 1) * 100
    returns = {"A_TOP10_LONG": a_return, "B_CASH": 0.0, "C_INVERSE_ETF": c_return}
    cohort["outcomes"][key] = {
        "holding_sessions": horizon,
        "exit_session_date": session_date,
        "status": "valid",
        "benchmark_return_pct": round(benchmark_return, 6),
        "strategies": {
            strategy: {
                "net_return_pct": round(value, 6),
                "net_profit_twd": round(CAPITAL_TWD * value / 100, 2),
                "win": value > 0,
                "excess_vs_benchmark_pct": round(value - benchmark_return, 6),
            }
            for strategy, value in returns.items()
        },
    }


def _metrics(values: list[tuple[str, float, float]], invalid_count: int) -> dict[str, Any]:
    ordered = sorted(values, key=lambda item: item[0])
    returns = [item[1] for item in ordered]
    excess = [item[2] for item in ordered]
    positives = sum(value for value in returns if value > 0)
    losses = abs(sum(value for value in returns if value < 0))
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1) * 100)
    return {
        "valid_samples": len(returns),
        "invalid_samples": invalid_count,
        "wins": sum(value > 0 for value in returns),
        "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 4) if returns else None,
        "average_return_pct": round(sum(returns) / len(returns), 6) if returns else None,
        "cumulative_return_pct": round((equity - 1) * 100, 6) if returns else None,
        "profit_factor": round(positives / losses, 6) if losses > 0 else None,
        "profit_factor_status": "calculated" if losses > 0 else "undefined_no_losses",
        "max_drawdown_pct": round(max_drawdown, 6) if returns else None,
        "average_excess_vs_benchmark_pct": round(sum(excess) / len(excess), 6) if excess else None,
        "data_sufficient": bool(returns),
    }


def _summarize(market_state: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    cohorts = market_state.get("cohorts") or []
    for horizon in HORIZONS:
        horizon_summary: dict[str, Any] = {}
        invalid = sum(
            1 for cohort in cohorts
            if cohort.get("status") == "quarantined"
            or (cohort.get("outcomes") or {}).get(str(horizon), {}).get("status") == "invalid_data"
        )
        for strategy in STRATEGIES:
            values: list[tuple[str, float, float]] = []
            for cohort in cohorts:
                outcome = (cohort.get("outcomes") or {}).get(str(horizon)) or {}
                result = (outcome.get("strategies") or {}).get(strategy) or {}
                value = _finite(result.get("net_return_pct"))
                excess = _finite(result.get("excess_vs_benchmark_pct"))
                if (
                    cohort.get("status") == "complete"
                    and outcome.get("status") == "valid"
                    and value is not None
                    and excess is not None
                ):
                    values.append((str(cohort.get("signal_session_date") or ""), value, excess))
            horizon_summary[strategy] = _metrics(values, invalid)
        summary[str(horizon)] = horizon_summary
    return summary


def update_state(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    inverse_rows: dict[str, dict[str, Any]],
    market_regimes: dict[str, dict[str, dict[str, Any]]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
) -> dict[str, Any]:
    if intraday:
        return state
    state["updated_at"] = updated_at
    for market in MARKETS:
        if period != CLOSED_PERIOD[market]:
            continue
        session_date = _session_date(rows, market)
        if not session_date:
            continue
        inverse_row = inverse_rows.get(market)
        regime_by_date = market_regimes.get(market) or {}
        current_benchmark = regime_by_date.get(session_date) or {}
        market_state = state["markets"][market]
        for cohort in market_state.get("cohorts") or []:
            if (
                cohort.get("status") != "quarantined"
                and cohort.get("integrity_sha256") != _snapshot_hash(cohort)
            ):
                _quarantine(cohort, ["frozen_snapshot_hash_mismatch"])
                continue
            if cohort.get("status") == "pending_entry":
                _open_cohort(cohort, rows, session_date, inverse_row, current_benchmark)
            if cohort.get("status") == "running":
                observed = cohort.setdefault("observed_sessions", [])
                if not observed or session_date > observed[-1]:
                    observed.append(session_date)
                for horizon in HORIZONS:
                    _settle(cohort, rows, session_date, inverse_row, current_benchmark, horizon)
                if all(str(value) in cohort.get("outcomes", {}) for value in HORIZONS):
                    cohort["status"] = "complete"
        cohort_id = f"{market}:{session_date}"
        exists = any(item.get("cohort_id") == cohort_id for item in market_state.get("cohorts") or [])
        if current_benchmark.get("regime") == "bear" and not exists:
            market_state.setdefault("cohorts", []).append(
                _freeze_cohort(rows, market, session_date, current_benchmark, inverse_row, updated_at)
            )
        # Keep active records plus the newest 365 terminal research samples.
        active = [item for item in market_state.get("cohorts") or [] if item.get("status") in {"pending_entry", "running"}]
        terminal = [item for item in market_state.get("cohorts") or [] if item.get("status") in {"complete", "quarantined"}][-365:]
        market_state["cohorts"] = active + terminal
        market_state["quarantined_count"] = sum(item.get("status") == "quarantined" for item in market_state["cohorts"])
        market_state["summary"] = _summarize(market_state)
        statuses = {str(item.get("status")) for item in market_state["cohorts"]}
        market_state["status"] = (
            "running" if statuses & {"pending_entry", "running"}
            else "has_results" if "complete" in statuses
            else "data_quarantined" if "quarantined" in statuses
            else "waiting_for_bear_signal"
        )
    values = {state["markets"][market].get("status") for market in MARKETS}
    state["status"] = (
        "running" if "running" in values
        else "has_results" if "has_results" in values
        else "data_quarantined" if "data_quarantined" in values
        else "waiting_for_bear_signal"
    )
    return state


def update_inverse_experiment(
    reports_dir: Path,
    rows: list[dict[str, Any]],
    inverse_rows: dict[str, dict[str, Any]],
    market_regimes: dict[str, dict[str, dict[str, Any]]],
    *,
    period: str,
    updated_at: str,
    intraday: bool = False,
    watchlist: list[dict[str, Any]] | None = None,
) -> Path:
    selected = watchlist or load_inverse_experiment_watchlist()
    path = reports_dir / "inverse_experiment.json"
    state = update_state(
        _load(path, updated_at, selected), rows, inverse_rows, market_regimes,
        period=period, updated_at=updated_at, intraday=intraday,
    )
    tmp = reports_dir / "inverse_experiment.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path
