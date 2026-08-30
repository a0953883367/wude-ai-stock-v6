"""StockQ close-only fallback for a limited set of popular US symbols.

StockQ does not provide a complete OHLCV universe.  This module therefore
collects only the completed-session close published on its popular-US-stocks
page.  The result may value an already-open shadow holding when the primary
provider misses that close, but it must never create an entry, settle an
open-to-close experiment by itself, or enter formal ranking features.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, time as clock_time, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests

from stockq_market_context import USER_AGENT, decode_stockq_value


SOURCE_URL = "https://www.stockq.org/market/us_stocks.php"
SCHEMA_VERSION = 1
CACHE_TTL_SECONDS = 4 * 60 * 60
MAX_STALE_SECONDS = 7 * 24 * 60 * 60
NEW_YORK = ZoneInfo("America/New_York")


def _signed_number(value: Any) -> float | None:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "").replace("%", "")
    if cleaned in {"", "-", "--", "N/A"}:
        return None
    try:
        number = float(cleaned)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number(value: Any) -> float | None:
    number = _signed_number(value)
    return number if number is not None and number > 0 else None


class _PopularUSParser(HTMLParser):
    """Capture StockQ's market table while preserving per-row US symbols."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, str]]] = []
        self._capture = False
        self._table_depth = 0
        self._row: list[dict[str, str]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            classes = set(str(attributes.get("class") or "").split())
            if not self._capture and "marketdatatable" in classes:
                self._capture = True
                self._table_depth = 1
                return
            if self._capture:
                self._table_depth += 1
        if not self._capture:
            return
        if tag == "tr" and self._table_depth == 1:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {"parts": [], "href": ""}
        elif tag == "a" and self._cell is not None:
            self._cell["href"] = str(attributes.get("href") or "")
        elif tag == "span" and self._cell is not None and attributes.get("data-sq"):
            decoded = decode_stockq_value(str(attributes["data-sq"]))
            if decoded is not None:
                self._cell["parts"].append(decoded)

    def handle_data(self, data: str) -> None:
        if self._capture and self._cell is not None:
            self._cell["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append({
                "text": re.sub(r"\s+", " ", "".join(self._cell["parts"])).strip(),
                "href": self._cell["href"],
            })
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table_depth == 1:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self._capture = False


def _session_date(value: Any, updated_at: str) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{2}/\d{2}", text):
        return None
    try:
        updated = datetime.fromisoformat(updated_at)
        month, day = (int(part) for part in text.split("/"))
        candidate = datetime(updated.year, month, day)
        if candidate.date() > updated.date():
            candidate = candidate.replace(year=updated.year - 1)
        return candidate.date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_stockq_us_close_html(html: str, *, updated_at: str) -> dict[str, dict[str, Any]]:
    parser = _PopularUSParser()
    parser.feed(html)
    output: dict[str, dict[str, Any]] = {}
    for row in parser.rows:
        symbol = ""
        label = ""
        for cell in row:
            match = re.search(r"(?:^|/)US_([A-Za-z0-9.\-]+)\.php(?:$|[?#])", cell["href"])
            if match:
                symbol = match.group(1).upper()
                label = cell["text"]
                break
        if not symbol or len(row) < 2:
            continue
        close_price = _number(row[1]["text"])
        session_date = _session_date(row[-1]["text"], updated_at)
        if close_price is None or not session_date:
            continue
        output[symbol] = {
            "symbol": symbol,
            "name": label or symbol,
            "official_session_date": session_date,
            "official_close_price": close_price,
            "price": close_price,
            "change": _signed_number(row[2]["text"]) if len(row) >= 3 else None,
            "change_pct": _signed_number(row[3]["text"]) if len(row) >= 4 else None,
            "official_price_source": "StockQ_after_close_close_only",
            "source": "StockQ",
            "source_url": SOURCE_URL,
            "stockq_close_only": True,
        }
    return output


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_age(payload: dict[str, Any], now_epoch: float) -> float | None:
    try:
        return max(0.0, now_epoch - float(payload["fetched_epoch"]))
    except (KeyError, TypeError, ValueError):
        return None


def _is_us_regular_session(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK) if now.tzinfo else now.replace(tzinfo=NEW_YORK)
    return (
        local.weekday() < 5
        and clock_time(9, 30) <= local.time().replace(tzinfo=None) < clock_time(16, 10)
    )


def _with_coverage(payload: dict[str, Any], requested_symbols: Iterable[str]) -> dict[str, Any]:
    requested = {str(symbol).upper() for symbol in requested_symbols if symbol}
    rows = payload.get("rows") if isinstance(payload.get("rows"), dict) else {}
    covered = sorted(requested.intersection(rows))
    return {
        **payload,
        "requested_count": len(requested),
        "covered_requested_count": len(covered),
        "covered_requested_symbols": covered,
        "missing_requested_symbols": sorted(requested.difference(rows)),
    }


def _empty_payload(updated_at: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "status": status,
        "cache_status": "missing",
        "source": "StockQ",
        "source_url": SOURCE_URL,
        "role": "美股熱門個股收盤價備援層",
        "after_close_only": True,
        "close_only": True,
        "affects_formal_ranking": False,
        "can_create_entries": False,
        "can_settle_open_to_close": False,
        "symbol_count": 0,
        "rows": {},
        "reason": reason,
    }


def update_stockq_us_close_fallback(
    reports_dir: Path,
    *,
    updated_at: str,
    requested_symbols: Iterable[str],
    timeout: int = 20,
    now_epoch: float | None = None,
    now: datetime | None = None,
    fetcher: Callable[[], str] | None = None,
    allow_network: bool = True,
) -> dict[str, Any]:
    """Refresh close-only StockQ rows without ever requesting during US trading."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "stockq_us_close_fallback.json"
    cached = _read_cache(path)
    now_epoch = time.time() if now_epoch is None else now_epoch
    age = _cache_age(cached, now_epoch) if cached else None
    now = datetime.now(timezone.utc) if now is None else now

    if not allow_network or _is_us_regular_session(now):
        if cached:
            return _with_coverage({
                **cached,
                "cache_status": "us_market_open_hold" if allow_network else "network_disabled_hold",
                "cache_age_seconds": round(age) if age is not None else None,
                "network_fetch_skipped": True,
            }, requested_symbols)
        status = "waiting_for_us_close" if allow_network else "network_disabled"
        reason = "美股盤中禁止抓價；等待正式收盤後更新 StockQ 備援"
        return _with_coverage({
            **_empty_payload(updated_at, status, reason),
            "network_fetch_skipped": True,
        }, requested_symbols)

    if cached and age is not None and age < CACHE_TTL_SECONDS:
        return _with_coverage({
            **cached,
            "cache_status": "fresh",
            "cache_age_seconds": round(age),
        }, requested_symbols)

    error: str | None = None
    try:
        if fetcher is None:
            response = requests.get(
                SOURCE_URL,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"},
            )
            response.raise_for_status()
            html = response.text
        else:
            html = fetcher()
        rows = parse_stockq_us_close_html(html, updated_at=updated_at)
        if len(rows) < 10:
            raise ValueError(f"StockQ可用美股收盤價不足：{len(rows)}")
    except Exception as exc:  # noqa: BLE001 - provider boundary
        error = f"{type(exc).__name__}: {str(exc)[:180]}"
    else:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": updated_at,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fetched_epoch": now_epoch,
            "status": "ok",
            "cache_status": "refreshed",
            "cache_age_seconds": 0,
            "source": "StockQ",
            "source_url": SOURCE_URL,
            "role": "美股熱門個股收盤價備援層",
            "after_close_only": True,
            "close_only": True,
            "affects_formal_ranking": False,
            "can_create_entries": False,
            "can_settle_open_to_close": False,
            "symbol_count": len(rows),
            "rows": rows,
            "warning": "只補已完成交易日收盤價；不補開盤、最高、最低、成交量，不建立新持倉或重算正式排名。",
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return _with_coverage(payload, requested_symbols)

    if cached and age is not None and age <= MAX_STALE_SECONDS:
        return _with_coverage({
            **cached,
            "status": "stale_fallback",
            "cache_status": "stale",
            "cache_age_seconds": round(age),
            "last_error": error,
        }, requested_symbols)
    return _with_coverage({
        **_empty_payload(updated_at, "unavailable", "StockQ目前無法連線且沒有可用快取"),
        "last_error": error,
    }, requested_symbols)


def load_us_shadow_symbols(reports_dir: Path) -> set[str]:
    """Return only currently pending or held US symbols from shadow states."""
    symbols: set[str] = set()

    holding = _read_cache(reports_dir / "holding_simulation.json") or {}
    medium_us = ((holding.get("medium") or {}).get("US") or {})
    for row in [*(medium_us.get("positions") or []), *(medium_us.get("benchmark_positions") or [])]:
        if row.get("symbol"):
            symbols.add(str(row["symbol"]).upper())
    long_state = holding.get("long") or {}
    for row in [*(long_state.get("positions") or []), *(long_state.get("benchmark_positions") or [])]:
        if str(row.get("market") or "").upper() == "US" and row.get("symbol"):
            symbols.add(str(row["symbol"]).upper())
    for pending in [medium_us.get("pending"), (long_state.get("pending") or {}).get("US")]:
        for row in (pending or {}).get("picks") or []:
            if row.get("symbol"):
                symbols.add(str(row["symbol"]).upper())

    million = _read_cache(reports_dir / "million_simulation.json") or {}
    pending = (((million.get("markets") or {}).get("US") or {}).get("pending") or {})
    for picks in (pending.get("strategies") or {}).values():
        for row in picks or []:
            if row.get("symbol"):
                symbols.add(str(row["symbol"]).upper())
    return symbols


def apply_stockq_us_close_to_simulation_rows(
    rows: list[dict[str, Any]],
    stockq: dict[str, Any],
    universe: Iterable[dict[str, Any]],
    *,
    required_symbols: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fill only missing US closes for shadow valuation, never formal analysis."""
    output = [dict(row) for row in rows]
    indexes = {
        str(row.get("symbol") or "").upper(): index
        for index, row in enumerate(output)
        if str(row.get("market") or "").upper() == "US"
    }
    catalog = {
        str(item.get("symbol") or "").upper(): item
        for item in universe
        if str(item.get("market") or "").upper() == "US"
    }
    requested = set(indexes).union(str(symbol).upper() for symbol in required_symbols)
    fallback_rows = stockq.get("rows") if isinstance(stockq.get("rows"), dict) else {}
    applied: list[str] = []
    for symbol in sorted(requested.intersection(fallback_rows)):
        fallback = fallback_rows[symbol]
        session_date = str(fallback.get("official_session_date") or "")
        close_price = _number(fallback.get("official_close_price"))
        if not session_date or close_price is None:
            continue
        if symbol in indexes:
            current = output[indexes[symbol]]
            current_date = str(current.get("official_session_date") or "")
            current_close = _number(current.get("official_close_price"))
            if current_close is not None or (current_date and current_date != session_date):
                continue
            current.update({
                "official_session_date": session_date,
                "official_close_price": close_price,
                "price": current.get("price") or close_price,
                "official_price_source": "StockQ_after_close_close_only",
                "stockq_close_only": True,
            })
        else:
            item = catalog.get(symbol)
            if not item:
                continue
            output.append({
                "symbol": symbol,
                "name": item.get("name") or fallback.get("name") or symbol,
                "market": "US",
                "type": item.get("type") or "個股",
                "official_session_date": session_date,
                "official_close_price": close_price,
                "official_open_price": None,
                "price": close_price,
                "official_price_source": "StockQ_after_close_close_only",
                "stockq_close_only": True,
                "trade_guard_blocked": True,
                "market_contract_valid": False,
            })
        applied.append(symbol)
    return output, applied
