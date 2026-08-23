"""Independent point-in-time historical validation for the dashboard.

This module is deliberately isolated from the production ranking and shadow
ledgers.  It reconstructs only the OHLCV-based core of the short-term model;
it must never be described as an exact backtest of V6 because historical
news, institutional, intraday and trade-guard inputs are not available here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo


LOG = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
METHOD = "V6-HIST-CORE-1"
CAPITAL_TWD = 1_000_000.0
TOP_N = 10
COST_RATE = {"TW_STOCK": 0.00685, "TW_ETF": 0.00385, "US_STOCK": 0.002, "US_ETF": 0.002}
BENCHMARK = {"TW": ("0050.TW", "0050"), "US": ("VOO", "VOO")}
MIN_HISTORY_ROWS = 80
MIN_MARKET_SYMBOLS = 10


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _safe_name(symbol: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in symbol)


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Return split-adjusted OHLCV with a unique, timezone-free daily index."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    output = frame.copy()
    if isinstance(output.columns, pd.MultiIndex):
        output.columns = [str(column[-1]).lower() for column in output.columns]
    else:
        output.columns = [str(column).lower().replace(" ", "") for column in output.columns]
    aliases = {"adjclose": "adjclose", "adj_close": "adjclose"}
    output = output.rename(columns=aliases)
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(output.columns):
        return pd.DataFrame()
    index = pd.to_datetime(output.index, errors="coerce")
    if isinstance(index, pd.DatetimeIndex) and index.tz is not None:
        index = index.tz_convert(None)
    output.index = index.normalize()
    output = output.loc[~output.index.isna()].sort_index()
    output = output.loc[~output.index.duplicated(keep="last")]
    for column in required | {"adjclose"}:
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    if "adjclose" in output:
        factor = (output["adjclose"] / output["close"]).replace([np.inf, -np.inf], np.nan)
        factor = factor.where(factor > 0, 1.0).fillna(1.0)
        for column in ("open", "high", "low", "close"):
            output[column] = output[column] * factor
    return output[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build close-of-session signals using no observations after each row."""
    source = normalize_history(frame)
    if source.empty:
        return source
    close = source["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = -delta.clip(upper=0).rolling(14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).where(loss != 0, 100.0)
    result = source.copy()
    result["ma20"] = close.rolling(20, min_periods=20).mean()
    result["ma60"] = close.rolling(60, min_periods=60).mean()
    result["momentum5"] = close.pct_change(5, fill_method=None)
    result["momentum20"] = close.pct_change(20, fill_method=None)
    # Prior-session baseline prevents current volume from leaking into its own average.
    result["volume_ratio"] = source["volume"] / source["volume"].shift(1).rolling(20, min_periods=20).mean()
    result["rsi14"] = rsi
    score = pd.Series(50.0, index=result.index)
    score += np.where(close >= result["ma20"], 12.0, -12.0)
    score += np.where(result["ma20"] >= result["ma60"], 10.0, -10.0)
    score += (result["momentum5"] * 120).clip(-8, 8).fillna(0)
    score += (result["momentum20"] * 80).clip(-10, 10).fillna(0)
    score += ((result["volume_ratio"] - 1) * 10).clip(-5, 8).fillna(0)
    score += np.where(result["rsi14"].between(50, 70), 5.0, 0.0)
    score += np.where(result["rsi14"] > 78, -8.0, 0.0)
    result["core_score"] = score.clip(0, 100)
    result["eligible"] = (
        (close >= result["ma20"])
        & (result["ma20"] >= result["ma60"])
        & (result["momentum20"] > 0)
        & result["rsi14"].between(45, 75)
        & (result["volume_ratio"] >= 0.8)
        & ((close / result["ma20"]) <= 1.12)
    )
    return result


def classify_regime(benchmark_features: pd.DataFrame, signal_date: pd.Timestamp) -> str:
    if signal_date not in benchmark_features.index:
        return "unknown"
    row = benchmark_features.loc[signal_date]
    needed = (row.get("close"), row.get("ma20"), row.get("ma60"), row.get("momentum20"))
    if not all(pd.notna(value) for value in needed):
        return "unknown"
    if row["close"] > row["ma20"] > row["ma60"] and row["momentum20"] > 0.02:
        return "bull"
    if row["close"] < row["ma20"] < row["ma60"] and row["momentum20"] < -0.02:
        return "bear"
    return "sideways"


def _locked_limit_up(row: pd.Series, prior_close: float, market: str) -> bool:
    if market != "TW" or not prior_close or prior_close <= 0:
        return False
    values = [float(row.get(key, math.nan)) for key in ("open", "high", "low")]
    if not all(math.isfinite(value) for value in values):
        return False
    return max(values) - min(values) <= max(0.01, abs(values[0]) * 1e-6) and values[0] / prior_close - 1 >= 0.095


def _position_profit(allocation: float, trade_row: pd.Series, cost_rate: float) -> tuple[float, float] | None:
    open_price = float(trade_row.get("open", math.nan))
    close_price = float(trade_row.get("close", math.nan))
    if not math.isfinite(open_price) or not math.isfinite(close_price) or open_price <= 0 or close_price <= 0:
        return None
    gross = allocation * (close_price / open_price - 1)
    return gross, gross - allocation * cost_rate


def _portfolio_metrics(days: list[dict[str, Any]], key: str) -> dict[str, Any]:
    profits = [float(day[key]["net_profit_twd"]) for day in days]
    gross = [float(day[key]["gross_profit_twd"]) for day in days]
    invested = [int(day[key].get("executed_positions", 0)) for day in days]
    equity = CAPITAL_TWD + np.cumsum(profits) if profits else np.array([CAPITAL_TWD])
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity / peaks - 1) * 100
    compounded = float(np.prod([1 + value / CAPITAL_TWD for value in profits]) - 1) if profits else 0.0
    by_regime: dict[str, dict[str, Any]] = {}
    for regime in ("bull", "bear", "sideways", "unknown"):
        subset = [day for day in days if day.get("regime") == regime]
        if subset:
            regime_profit = sum(float(day[key]["net_profit_twd"]) for day in subset)
            by_regime[regime] = {
                "sessions": len(subset),
                "net_profit_twd": round(regime_profit, 2),
                "net_return_pct": round(regime_profit / CAPITAL_TWD * 100, 4),
                "win_rate_pct": round(sum(day[key]["net_profit_twd"] > 0 for day in subset) / len(subset) * 100, 2),
            }
    return {
        "evaluated_sessions": len(days),
        "gross_profit_twd": round(sum(gross), 2),
        "net_profit_twd": round(sum(profits), 2),
        "net_return_pct": round(sum(profits) / CAPITAL_TWD * 100, 4),
        "compounded_return_pct": round(compounded * 100, 4),
        "win_rate_pct": round(sum(value > 0 for value in profits) / len(profits) * 100, 2) if profits else 0.0,
        "max_drawdown_pct": round(float(drawdowns.min()), 4) if len(drawdowns) else 0.0,
        "average_positions": round(sum(invested) / len(invested), 2) if invested else 0.0,
        "cash_session_pct": round(sum(value == 0 for value in invested) / len(invested) * 100, 2) if invested else 100.0,
        "regimes": by_regime,
    }


def evaluate_market(
    histories: dict[str, pd.DataFrame],
    universe: list[dict[str, Any]],
    market: str,
    asset_class: str = "STOCK",
) -> dict[str, Any]:
    benchmark_symbol, benchmark_label = BENCHMARK[market]
    portfolio_cost_rate = COST_RATE[f"{market}_{asset_class}"]
    benchmark_cost_rate = COST_RATE[f"{market}_ETF"]
    benchmark = build_features(histories.get(benchmark_symbol, pd.DataFrame()))
    candidates: dict[str, pd.DataFrame] = {}
    names: dict[str, str] = {}
    expected = [
        row for row in universe
        if row.get("market") == market
        and (("ETF" in str(row.get("type", "")).upper()) == (asset_class == "ETF"))
    ]
    for row in expected:
        symbol = str(row["symbol"])
        features = build_features(histories.get(symbol, pd.DataFrame()))
        if len(features) >= MIN_HISTORY_ROWS:
            candidates[symbol] = features
            names[symbol] = str(row.get("name") or symbol)
    result: dict[str, Any] = {
        "market": market,
        "asset_class": asset_class,
        "group": f"{market}_{asset_class}",
        "benchmark": {"symbol": benchmark_symbol, "label": benchmark_label},
        "expected_symbols": len(expected),
        "loaded_symbols": len(candidates),
        "missing_symbols": sorted({str(row["symbol"]) for row in expected} - set(candidates)),
        "coverage_pct": round(len(candidates) / len(expected) * 100, 2) if expected else 0.0,
        "date_start": None,
        "date_end": None,
        "days": [],
    }
    if len(benchmark) < MIN_HISTORY_ROWS or len(candidates) < MIN_MARKET_SYMBOLS:
        result["status"] = "data_insufficient"
        result["reason"] = "大盤基準或可用個股不足，未計算績效"
        result["rank_portfolio"] = _portfolio_metrics([], "rank")
        result["strict_portfolio"] = _portfolio_metrics([], "strict")
        result["benchmark_portfolio"] = _portfolio_metrics([], "benchmark")
        return result

    sessions = list(benchmark.index)
    allocation = CAPITAL_TWD / TOP_N
    days: list[dict[str, Any]] = []
    for offset in range(len(sessions) - 1):
        signal_date, trade_date = sessions[offset], sessions[offset + 1]
        rows: list[tuple[str, pd.Series]] = []
        for symbol, features in candidates.items():
            if signal_date not in features.index or trade_date not in features.index:
                continue
            signal = features.loc[signal_date]
            if pd.notna(signal.get("core_score")) and pd.notna(signal.get("ma60")):
                rows.append((symbol, signal))
        rows.sort(key=lambda item: (-float(item[1]["core_score"]), item[0]))
        picks = rows[:TOP_N]
        if not picks:
            continue
        day: dict[str, Any] = {
            "signal_date": signal_date.date().isoformat(),
            "trade_date": trade_date.date().isoformat(),
            "regime": classify_regime(benchmark, signal_date),
            "candidate_count": len(rows),
            "picks": [],
            "rank": {"gross_profit_twd": 0.0, "net_profit_twd": 0.0, "executed_positions": 0, "idle_twd": CAPITAL_TWD},
            "strict": {"gross_profit_twd": 0.0, "net_profit_twd": 0.0, "executed_positions": 0, "idle_twd": CAPITAL_TWD},
            "benchmark": {"gross_profit_twd": 0.0, "net_profit_twd": 0.0, "executed_positions": 0, "idle_twd": CAPITAL_TWD},
        }
        for rank, (symbol, signal) in enumerate(picks, 1):
            features = candidates[symbol]
            trade = features.loc[trade_date]
            prior_close = float(features.loc[signal_date, "close"])
            profit = _position_profit(allocation, trade, portfolio_cost_rate)
            locked = _locked_limit_up(trade, prior_close, market)
            eligible = bool(signal["eligible"])
            pick = {
                "rank": rank,
                "symbol": symbol,
                "name": names[symbol],
                "score": round(float(signal["core_score"]), 2),
                "eligible": eligible,
                "locked_limit_up": locked,
            }
            day["picks"].append(pick)
            if profit is not None:
                day["rank"]["gross_profit_twd"] += profit[0]
                day["rank"]["net_profit_twd"] += profit[1]
                day["rank"]["executed_positions"] += 1
                day["rank"]["idle_twd"] -= allocation
                if eligible and not locked:
                    day["strict"]["gross_profit_twd"] += profit[0]
                    day["strict"]["net_profit_twd"] += profit[1]
                    day["strict"]["executed_positions"] += 1
                    day["strict"]["idle_twd"] -= allocation
        benchmark_profit = _position_profit(CAPITAL_TWD, benchmark.loc[trade_date], benchmark_cost_rate)
        if benchmark_profit is not None:
            day["benchmark"].update({
                "gross_profit_twd": benchmark_profit[0],
                "net_profit_twd": benchmark_profit[1],
                "executed_positions": 1,
                "idle_twd": 0.0,
            })
        for key in ("rank", "strict", "benchmark"):
            day[key]["gross_profit_twd"] = round(day[key]["gross_profit_twd"], 2)
            day[key]["net_profit_twd"] = round(day[key]["net_profit_twd"], 2)
            day[key]["idle_twd"] = round(day[key]["idle_twd"], 2)
        days.append(day)

    result["days"] = days
    result["date_start"] = days[0]["signal_date"] if days else None
    result["date_end"] = days[-1]["trade_date"] if days else None
    result["status"] = "complete" if len(candidates) == len(expected) else "partial"
    result["rank_portfolio"] = _portfolio_metrics(days, "rank")
    result["strict_portfolio"] = _portfolio_metrics(days, "strict")
    result["benchmark_portfolio"] = _portfolio_metrics(days, "benchmark")
    # The daily audit trail stays deterministic but the dashboard report remains compact.
    result["audit_hash"] = hashlib.sha256(json.dumps(days, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    result["recent_days"] = days[-20:]
    del result["days"]
    return result


def _read_cache(cache_dir: Path, symbol: str) -> pd.DataFrame:
    path = cache_dir / f"{_safe_name(symbol)}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception as exc:
        LOG.warning("cache read failed %s: %s", symbol, exc)
        return pd.DataFrame()


def _write_cache(cache_dir: Path, symbol: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    normalize_history(frame).to_csv(cache_dir / f"{_safe_name(symbol)}.csv")


def fetch_with_cache(symbols: list[str], period: str, cache_dir: Path, full_refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Fetch in resumable batches; cached names only request recent sessions."""
    from data_fetcher import download_history  # lazy import keeps pure tests offline

    histories: dict[str, pd.DataFrame] = {}
    cached: list[str] = []
    missing: list[str] = []
    for symbol in symbols:
        frame = pd.DataFrame() if full_refresh else _read_cache(cache_dir, symbol)
        if frame.empty:
            missing.append(symbol)
        else:
            histories[symbol] = frame
            cached.append(symbol)
    jobs = [(missing, period), (cached, "10d")]
    for job_symbols, job_period in jobs:
        remaining = list(job_symbols)
        for attempt in range(3):
            if not remaining:
                break
            downloaded: dict[str, pd.DataFrame] = {}
            batch_size = 25 if attempt == 0 else 8
            for batch in _chunks(remaining, batch_size):
                downloaded.update(download_history(batch, period=job_period))
            for symbol, new_frame in downloaded.items():
                old_frame = histories.get(symbol, pd.DataFrame())
                merged = pd.concat([normalize_history(old_frame), normalize_history(new_frame)])
                merged = merged.loc[~merged.index.duplicated(keep="last")].sort_index()
                histories[symbol] = merged
                _write_cache(cache_dir, symbol, merged)
            remaining = [symbol for symbol in remaining if symbol not in downloaded]
            if remaining and attempt < 2:
                time.sleep(2 ** attempt)
        if remaining:
            LOG.warning("history unavailable after retries: %s symbols", len(remaining))
    return histories


def _forward_archive(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / "prediction_history.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshots = payload.get("snapshots", [])
    except (OSError, json.JSONDecodeError):
        snapshots = []
    dates = sorted({str(row.get("session_date")) for row in snapshots if row.get("session_date")})
    return {
        "source": "reports/prediction_history.json",
        "snapshot_count": len(snapshots),
        "session_count": len(dates),
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "description": "完整 V6 只能由當時凍結快照向前累積；歷史核心代理不取代此正式證據。",
    }


def build_report(
    histories: dict[str, pd.DataFrame],
    universe: list[dict[str, Any]],
    reports_dir: Path,
    requested_period: str = "5y",
) -> dict[str, Any]:
    markets = {
        f"{market}_{asset_class}": evaluate_market(histories, universe, market, asset_class)
        for market in ("TW", "US")
        for asset_class in ("STOCK", "ETF")
    }
    statuses = {market["status"] for market in markets.values()}
    status = "data_insufficient" if statuses == {"data_insufficient"} else ("complete" if statuses == {"complete"} else "partial")
    methodology = {
        "version": METHOD,
        "signal_timing": "交易日收盤後，以當日及以前資料排名",
        "execution_timing": "下一交易日官方開盤買入、同日收盤賣出",
        "portfolio": "前10名各配置10萬元；嚴格組未達資格保留現金",
        "costs": "台股個股來回0.685%、台股ETF來回0.385%；美股來回0.20%；匯率另列",
        "regimes": "大盤當日收盤、MA20、MA60與20日動能分多頭／空頭／盤整",
        "methodology_hash": hashlib.sha256(METHOD.encode()).hexdigest()[:16],
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "historical_lab_only",
        "status": status,
        "requested_period": requested_period,
        "configured_universe_count": len({str(row.get("symbol")) for row in universe if row.get("symbol")}),
        "is_exact_v6_backtest": False,
        "official_ranking_affected": False,
        "official_ledgers_affected": False,
        "methodology": methodology,
        "markets": markets,
        "exact_forward_archive": _forward_archive(reports_dir),
        "limitations": [
            "這是可由OHLCV重建的短線核心代理，不是完整V6歷史回測。",
            "目前使用今日維護的股票池，存在存活者偏差；下市股與歷史成分股尚未完整納入。",
            "歷史法人、投信、新聞、盤中攻擊量與當時交易阻擋資料未加入，不能據此改寫正式權重。",
            "資料缺漏標為partial或data_insufficient，不以零報酬或假成交補值。",
            "所有訊號只使用當日收盤以前資料，並於下一交易日開盤後執行。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated V6 historical core validation")
    parser.add_argument("--period", default="5y", choices=("3y", "5y"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/historical_lab"))
    parser.add_argument("--full-refresh", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from data_fetcher import load_search_universe
    from watchlist import load_watchlist

    # Match briefing.py exactly: the official candidate source is the union of
    # search_data.json and the fixed watchlist, with watchlist metadata winning.
    combined = {str(row["symbol"]): row for row in load_search_universe()}
    combined.update({str(row["symbol"]): row for row in load_watchlist()})
    universe = list(combined.values())
    # Download the complete maintained list.  Stocks and ETFs are ranked in
    # separate groups so an ETF can never displace an individual stock.
    symbols = [str(row["symbol"]) for row in universe]
    symbols.extend(symbol for symbol, _label in BENCHMARK.values())
    symbols = sorted(set(symbols))
    histories = fetch_with_cache(symbols, args.period, args.cache_dir, args.full_refresh)
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(histories, universe, args.reports_dir, args.period)
    target = args.reports_dir / "historical_lab.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("historical lab %s: %s", report["status"], target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
