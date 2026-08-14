"""Market-data adapters with graceful fallbacks.

Daily and intraday prices come from Yahoo Finance via yfinance. FinMind is
optional and enriches the model with Taiwan institutional flows.
"""

from __future__ import annotations

import json
import logging
import time
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
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    headers = {"Authorization": f"Bearer {SETTINGS.finmind_token}"}
    try:
        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            headers=headers,
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
    except Exception as exc:
        LOG.warning("FinMind institutional data unavailable: %s", exc)
        return {}
    return _aggregate_institutional_rows(rows, stock_ids)


def market_session_fraction(now: datetime | None = None) -> float:
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    minute = now.hour * 60 + now.minute
    return min(1.0, max(15 / 270, (minute - 9 * 60) / 270))
