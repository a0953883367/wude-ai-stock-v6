"""Optional authoritative US live-data adapters.

Alpaca SIP/OPRA credentials are read only from environment variables.  When
they are absent or the subscription does not cover a feed, callers receive an
empty mapping and the existing Yahoo/SEC/FINRA fallbacks remain in place.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any, Iterable

import requests

LOG = logging.getLogger(__name__)
STOCK_SNAPSHOT_URL = "https://data.alpaca.markets/v2/stocks/snapshots"
OPTION_CHAIN_URL = "https://data.alpaca.markets/v1beta1/options/snapshots/{symbol}"
OPTION_SYMBOL = re.compile(r"(\d{6})([CP])(\d{8})$")


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _credentials() -> tuple[str, str] | None:
    key = os.getenv("ALPACA_API_KEY_ID", "").strip()
    secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
    return (key, secret) if key and secret else None


def _headers(credentials: tuple[str, str]) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": credentials[0],
        "APCA-API-SECRET-KEY": credentials[1],
        "User-Agent": "wude-ai-stock-v6/6.30",
    }


def _snapshot_rows(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("snapshots")
    return nested if isinstance(nested, dict) else payload


def normalize_sip_snapshot(snapshot: dict[str, Any], feed: str = "sip") -> dict[str, Any]:
    trade = snapshot.get("latestTrade") or {}
    quote = snapshot.get("latestQuote") or {}
    minute = snapshot.get("minuteBar") or {}
    daily = snapshot.get("dailyBar") or {}
    previous = snapshot.get("prevDailyBar") or snapshot.get("previousDailyBar") or {}

    last_price = _number(trade.get("p")) or _number(minute.get("c")) or _number(daily.get("c"))
    bid = _number(quote.get("bp"))
    ask = _number(quote.get("ap"))
    bid_size = _number(quote.get("bs"))
    ask_size = _number(quote.get("as"))
    vwap = _number(daily.get("vw"))
    day_volume = _number(daily.get("v"))
    previous_volume = _number(previous.get("v"))

    spread_pct = None
    if bid is not None and ask is not None and ask >= bid and bid + ask > 0:
        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
    imbalance_pct = None
    if bid_size is not None and ask_size is not None and bid_size + ask_size > 0:
        imbalance_pct = (bid_size - ask_size) / (bid_size + ask_size) * 100
    vwap_distance_pct = None
    if last_price is not None and vwap:
        vwap_distance_pct = (last_price / vwap - 1) * 100

    available = any(value is not None for value in (last_price, bid, ask, vwap))
    if not available:
        return {}
    return {
        "us_live_data_available": True,
        "us_live_source": "Alpaca SIP" if feed == "sip" else f"Alpaca {feed.upper()}",
        "us_live_feed": feed,
        "us_live_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "us_live_price": last_price,
        "us_live_bid": bid,
        "us_live_ask": ask,
        "us_live_bid_size": bid_size,
        "us_live_ask_size": ask_size,
        "us_live_spread_pct": None if spread_pct is None else round(spread_pct, 4),
        "us_live_quote_imbalance_pct": None if imbalance_pct is None else round(imbalance_pct, 2),
        "us_live_vwap": vwap,
        "us_live_vwap_distance_pct": None if vwap_distance_pct is None else round(vwap_distance_pct, 3),
        "us_live_day_volume": day_volume,
        "us_live_previous_volume": previous_volume,
    }


def fetch_us_sip_snapshots(
    symbols: set[str] | list[str],
    timeout: int = 20,
    session: requests.Session | None = None,
) -> dict[str, dict[str, Any]]:
    credentials = _credentials()
    if not credentials:
        LOG.info("Alpaca credentials absent; US SIP layer will use existing fallbacks")
        return {}
    feed = os.getenv("ALPACA_STOCK_FEED", "sip").strip().lower() or "sip"
    client = session or requests.Session()
    output: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(sorted({str(symbol).upper() for symbol in symbols if symbol}), 100):
        try:
            response = client.get(
                STOCK_SNAPSHOT_URL,
                params={"symbols": ",".join(chunk), "feed": feed},
                headers=_headers(credentials),
                timeout=timeout,
            )
            response.raise_for_status()
            for symbol, snapshot in _snapshot_rows(response.json()).items():
                normalized = normalize_sip_snapshot(snapshot or {}, feed)
                if normalized:
                    output[str(symbol).upper()] = normalized
        except requests.RequestException as exc:
            LOG.warning("US %s snapshot batch failed (%s): %s", feed.upper(), ",".join(chunk[:3]), exc)
    return output


def normalize_option_chain(payload: Any, underlying_price: float | None) -> dict[str, Any]:
    snapshots = _snapshot_rows(payload)
    calls: list[float] = []
    puts: list[float] = []
    for contract, snapshot in snapshots.items():
        match = OPTION_SYMBOL.search(str(contract))
        if not match or not isinstance(snapshot, dict):
            continue
        strike = int(match.group(3)) / 1000
        if underlying_price and not underlying_price * .90 <= strike <= underlying_price * 1.10:
            continue
        iv = _number(snapshot.get("impliedVolatility"))
        if iv is None or iv <= 0:
            continue
        (calls if match.group(2) == "C" else puts).append(iv)
    if not calls and not puts:
        return {}
    call_iv = mean(calls) if calls else None
    put_iv = mean(puts) if puts else None
    all_iv = calls + puts
    avg_iv = mean(all_iv)
    skew = put_iv - call_iv if put_iv is not None and call_iv is not None else None
    safety = 65 - max(avg_iv - .35, 0) * 45
    if skew is not None:
        safety -= max(skew, 0) * 80
        safety += min(max(-skew, 0) * 30, 5)
    safety = max(0, min(100, safety))
    return {
        "us_option_data_available": True,
        "us_option_source": "OPRA",
        "us_option_contract_count": len(all_iv),
        "us_option_iv_pct": round(avg_iv * 100, 2),
        "us_option_put_call_iv_skew_pct": None if skew is None else round(skew * 100, 2),
        "us_option_safety_score": round(safety, 1),
    }


def fetch_us_opra_signals(
    candidates: list[dict[str, Any]],
    timeout: int = 20,
    session: requests.Session | None = None,
) -> dict[str, dict[str, Any]]:
    credentials = _credentials()
    feed = os.getenv("ALPACA_OPTION_FEED", "").strip().lower()
    if not credentials or not feed:
        LOG.info("OPRA feed not configured; options remain an unavailable risk dimension")
        return {}
    client = session or requests.Session()
    today = date.today()
    output: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in candidates:
        symbol = str(row.get("symbol") or "").upper()
        price = _number(row.get("us_live_price")) or _number(row.get("price"))
        if not symbol or symbol in seen or not price:
            continue
        seen.add(symbol)
        params = {
            "feed": feed,
            "limit": 100,
            "expiration_date_gte": (today + timedelta(days=7)).isoformat(),
            "expiration_date_lte": (today + timedelta(days=45)).isoformat(),
            "strike_price_gte": round(price * .90, 2),
            "strike_price_lte": round(price * 1.10, 2),
        }
        try:
            response = client.get(
                OPTION_CHAIN_URL.format(symbol=symbol),
                params=params,
                headers=_headers(credentials),
                timeout=timeout,
            )
            response.raise_for_status()
            normalized = normalize_option_chain(response.json(), price)
            if normalized:
                normalized["us_option_feed"] = feed
                normalized["us_option_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                output[symbol] = normalized
        except requests.RequestException as exc:
            LOG.warning("US option snapshot failed for %s: %s", symbol, exc)
    return output
