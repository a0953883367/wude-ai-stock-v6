"""Market-data adapters with graceful fallbacks.

Daily and intraday prices come from Yahoo Finance via yfinance. FinMind is
optional and enriches the model with Taiwan institutional flows.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
import yfinance as yf

from config import SETTINGS, TAIPEI


LOG = logging.getLogger(__name__)
CORE_MARKET = {
    "加權指數": "^TWII",
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "費城半導體": "^SOX",
    "NVDA": "NVDA",
    "TSM ADR": "TSM",
    "AMD": "AMD",
    "SMH": "SMH",
    "美元台幣": "TWD=X",
}


def load_taiwan_universe(path: Path = SETTINGS.search_data_path) -> list[dict[str, Any]]:
    """Load every Taiwan candidate already maintained by the V6 dashboard."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in payload.get("data", []):
        symbol = str(row.get("代號", "")).strip().upper()
        market = str(row.get("市場", ""))
        if "台灣" not in market or not symbol or symbol in seen:
            continue
        if not (symbol.endswith(".TW") or symbol.endswith(".TWO")):
            continue
        seen.add(symbol)
        rows.append({
            "symbol": symbol,
            "name": row.get("股票", symbol),
            "type": row.get("類型", "個股"),
            "theme": row.get("主題", "其他"),
            "industry": row.get("次產業", "其他"),
        })
    if not rows:
        raise RuntimeError("search_data.json 沒有可用的台股候選清單")
    return rows


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _extract_frame(raw: pd.DataFrame, symbol: str, multi: bool) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if multi:
        try:
            frame = raw[symbol].copy()
        except (KeyError, TypeError):
            return pd.DataFrame()
    else:
        frame = raw.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    return frame.dropna(how="all")


def download_history(symbols: list[str], period: str = "3mo") -> dict[str, pd.DataFrame]:
    """Download daily OHLCV in chunks so one bad ticker cannot stop the run."""
    result: dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, 35):
        try:
            raw = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                threads=True,
                progress=False,
                timeout=SETTINGS.request_timeout,
            )
            multi = isinstance(raw.columns, pd.MultiIndex)
            for symbol in chunk:
                frame = _extract_frame(raw, symbol, multi)
                if not frame.empty:
                    result[symbol] = frame
        except Exception as exc:
            LOG.warning("daily batch failed (%s): %s", ",".join(chunk[:3]), exc)
        time.sleep(0.3)
    return result


def download_intraday(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download today's 5-minute bars used for volume pace and attack-volume proxy."""
    result: dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, 25):
        try:
            raw = yf.download(
                tickers=chunk,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                prepost=False,
                threads=True,
                progress=False,
                timeout=SETTINGS.request_timeout,
            )
            multi = isinstance(raw.columns, pd.MultiIndex)
            for symbol in chunk:
                frame = _extract_frame(raw, symbol, multi)
                if not frame.empty:
                    result[symbol] = frame
        except Exception as exc:
            LOG.warning("intraday batch failed (%s): %s", ",".join(chunk[:3]), exc)
        time.sleep(0.3)
    return result


def fetch_core_market() -> dict[str, dict[str, float | None]]:
    histories = download_history(list(CORE_MARKET.values()), period="7d")
    output: dict[str, dict[str, float | None]] = {}
    for label, symbol in CORE_MARKET.items():
        frame = histories.get(symbol)
        if frame is None or frame.empty or "close" not in frame:
            output[label] = {"price": None, "change_pct": None}
            continue
        close = frame["close"].dropna()
        price = float(close.iloc[-1]) if len(close) else None
        change = float((close.iloc[-1] / close.iloc[-2] - 1) * 100) if len(close) > 1 else None
        output[label] = {"price": price, "change_pct": change}
    return output


def _aggregate_institutional_rows(
    rows: list[dict[str, Any]], stock_ids: set[str]
) -> dict[str, dict[str, float]]:
    """Aggregate institutional flows over 1/3/5/10 available sessions."""
    daily: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        sid = str(row.get("stock_id", ""))
        if sid not in stock_ids:
            continue
        trade_date = str(row.get("date", ""))
        name = str(row.get("name", ""))
        net = float(row.get("buy", 0) or 0) - float(row.get("sell", 0) or 0)
        item = daily.setdefault(sid, {}).setdefault(
            trade_date, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0}
        )
        if name == "Foreign_Investor":
            item["foreign"] += net
        elif name == "Investment_Trust":
            item["trust"] += net
        elif name in {"Dealer", "Dealer_self", "Dealer_Hedging"}:
            item["dealer"] += net

    output: dict[str, dict[str, float]] = {}
    for sid, by_date in daily.items():
        dates = sorted(by_date, reverse=True)
        if not dates:
            continue
        latest = by_date[dates[0]]
        item: dict[str, float] = {
            "foreign": latest["foreign"],
            "trust": latest["trust"],
            "dealer": latest["dealer"],
            "available": 1.0,
        }
        for window in (1, 3, 5, 10):
            selected = dates[:window]
            for group in ("foreign", "trust", "dealer"):
                item[f"{group}_{window}d"] = sum(by_date[d][group] for d in selected)
            item[f"institution_{window}d"] = sum(
                sum(by_date[d][group] for group in ("foreign", "trust", "dealer"))
                for d in selected
            )
        output[sid] = item
    return output


def fetch_institutional_flows(stock_ids: set[str]) -> dict[str, dict[str, float]]:
    """Fetch recent all-market institutional trades from FinMind in one request."""
    if not SETTINGS.finmind_token:
        return {}
    end = date.today()
    # 21 calendar days normally covers at least 10 Taiwan trading sessions.
    start = end - timedelta(days=21)
    rows = _dataset_for_ids(
        "TaiwanStockInstitutionalInvestorsBuySell", stock_ids, start, end
    )
    return _aggregate_institutional_rows(rows, stock_ids)


def _finmind_rows(dataset: str, start: date, end: date, stock_id: str | None = None) -> list[dict[str, Any]]:
    """Read one FinMind dataset and treat plan/rate failures as unavailable."""
    if not SETTINGS.finmind_token:
        return []
    params: dict[str, str] = {
        "dataset": dataset,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if stock_id:
        params["data_id"] = stock_id
    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            headers={"Authorization": f"Bearer {SETTINGS.finmind_token}"},
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in (None, 200):
            return []
        return payload.get("data", []) or []
    except Exception as exc:
        LOG.debug("FinMind %s unavailable for %s: %s", dataset, stock_id or "all", exc)
        return []


def _dataset_for_ids(dataset: str, stock_ids: set[str], start: date, end: date) -> list[dict[str, Any]]:
    """Prefer one all-market request; free plans fall back to per-stock calls."""
    rows = _finmind_rows(dataset, start, end)
    if rows:
        return [row for row in rows if str(row.get("stock_id", "")) in stock_ids]
    output: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(_finmind_rows, dataset, start, end, sid): sid for sid in stock_ids}
        for job in as_completed(jobs):
            output.extend(job.result())
    return output


def _aggregate_credit_rows(
    margin_rows: list[dict[str, Any]], short_rows: list[dict[str, Any]], stock_ids: set[str]
) -> dict[str, dict[str, float]]:
    """Build latest and 5-session changes for margin, short sale and SBL balances."""
    by_sid: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for kind, rows in (("margin", margin_rows), ("short", short_rows)):
        for row in rows:
            sid = str(row.get("stock_id", ""))
            if sid not in stock_ids:
                continue
            by_sid.setdefault(sid, {}).setdefault(kind, {})[str(row.get("date", ""))] = row

    output: dict[str, dict[str, float]] = {}
    for sid, kinds in by_sid.items():
        item: dict[str, float] = {}
        margin_dates = sorted(kinds.get("margin", {}), reverse=True)
        if margin_dates:
            latest = kinds["margin"][margin_dates[0]]
            oldest = kinds["margin"][margin_dates[min(4, len(margin_dates) - 1)]]
            margin_balance = float(latest.get("MarginPurchaseTodayBalance", 0) or 0)
            short_balance = float(latest.get("ShortSaleTodayBalance", 0) or 0)
            item.update({
                "credit_available": 1.0,
                "margin_balance": margin_balance,
                "margin_1d_change": margin_balance - float(latest.get("MarginPurchaseYesterdayBalance", 0) or 0),
                "margin_5d_change": margin_balance - float(oldest.get("MarginPurchaseYesterdayBalance", 0) or 0),
                "short_balance": short_balance,
                "short_1d_change": short_balance - float(latest.get("ShortSaleYesterdayBalance", 0) or 0),
                "short_5d_change": short_balance - float(oldest.get("ShortSaleYesterdayBalance", 0) or 0),
            })
        short_dates = sorted(kinds.get("short", {}), reverse=True)
        if short_dates:
            latest = kinds["short"][short_dates[0]]
            oldest = kinds["short"][short_dates[min(4, len(short_dates) - 1)]]
            sbl_balance = float(latest.get("SBLShortSalesCurrentDayBalance", 0) or 0)
            item.update({
                "credit_available": 1.0,
                "sbl_balance": sbl_balance,
                "sbl_1d_change": sbl_balance - float(latest.get("SBLShortSalesPreviousDayBalance", 0) or 0),
                "sbl_5d_change": sbl_balance - float(oldest.get("SBLShortSalesPreviousDayBalance", 0) or 0),
            })
        if item:
            output[sid] = item
    return output


def fetch_credit_flows(stock_ids: set[str]) -> dict[str, dict[str, float]]:
    """Fetch margin/short and securities-borrowing short-sale balances."""
    if not SETTINGS.finmind_token or not stock_ids:
        return {}
    end = date.today()
    start = end - timedelta(days=14)
    margin_rows = _dataset_for_ids("TaiwanStockMarginPurchaseShortSale", stock_ids, start, end)
    short_rows = _dataset_for_ids("TaiwanDailyShortSaleBalances", stock_ids, start, end)
    return _aggregate_credit_rows(margin_rows, short_rows, stock_ids)


def fetch_broker_branches(stock_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Fetch optional Sponsor-tier broker branch concentration for recent sessions."""
    if not SETTINGS.finmind_token or not stock_ids:
        return {}
    end = date.today()
    start = end - timedelta(days=7)

    def fetch_one(sid: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            response = requests.get(
                "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report_secid_agg",
                params={"data_id": sid, "start_date": start.isoformat(), "end_date": end.isoformat()},
                headers={"Authorization": f"Bearer {SETTINGS.finmind_token}"},
                timeout=SETTINGS.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            return sid, payload.get("data", []) or []
        except Exception:
            return sid, []

    first = sorted(stock_ids)[0]
    sid, probe = fetch_one(first)
    if not probe:  # Sponsor dataset: stop immediately when the token has no access.
        return {}
    collected = {sid: probe}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [pool.submit(fetch_one, item) for item in stock_ids if item != first]
        for job in as_completed(jobs):
            item, rows = job.result()
            if rows:
                collected[item] = rows

    output: dict[str, dict[str, Any]] = {}
    for sid, rows in collected.items():
        latest_date = max((str(row.get("date", "")) for row in rows), default="")
        latest = [row for row in rows if str(row.get("date", "")) == latest_date]
        branches = []
        for row in latest:
            net = float(row.get("buy_volume", 0) or 0) - float(row.get("sell_volume", 0) or 0)
            branches.append({"name": str(row.get("securities_trader", "")), "net": int(net)})
        buyers = sorted((b for b in branches if b["net"] > 0), key=lambda b: b["net"], reverse=True)[:3]
        sellers = sorted((b for b in branches if b["net"] < 0), key=lambda b: b["net"])[:3]
        output[sid] = {"broker_available": True, "broker_date": latest_date, "top_brokers_buy": buyers, "top_brokers_sell": sellers}
    return output


def market_session_fraction(now: datetime | None = None) -> float:
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    minute = now.hour * 60 + now.minute
    return min(1.0, max(15 / 270, (minute - 9 * 60) / 270))
