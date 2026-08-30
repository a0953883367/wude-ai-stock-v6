"""Low-frequency StockQ market-context collector.

StockQ is a useful human-facing aggregation page, not a broker quote API.  This
module therefore treats it as an attributable after-close fallback for the
market indicators it actually publishes.  It never overwrites complete primary
data, individual-symbol prices, fundamentals, rankings, weights, or order fields.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import requests


SOURCE_URL = "https://www.stockq.org/"
SCHEMA_VERSION = 1
CACHE_TTL_SECONDS = 15 * 60
MAX_STALE_SECONDS = 3 * 24 * 60 * 60
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
)

TARGETS = {
    "taiwan_weighted": ("台灣加權", "equity_index"),
    "dow_jones": ("道瓊工業", "equity_index"),
    "sp500": ("S&P 500", "equity_index"),
    "nasdaq": ("NASDAQ", "equity_index"),
    "nasdaq100": ("NASDAQ 100", "equity_index"),
    "sox": ("費城半導體", "sector_index"),
    "russell2000": ("羅素2000", "equity_index"),
    "vix": ("VIX波動率", "risk"),
    "dollar_index": ("美元指數", "currency"),
    "us10y_yield": ("10年期公債利率", "rate"),
    "gold": ("黃金", "commodity"),
    "wti_oil": ("紐約輕原油", "commodity"),
    "taiwan_futures": ("台指期", "index_future"),
    "sp500_futures": ("S&P 500", "index_future"),
    "nasdaq100_futures": ("NASDAQ 100", "index_future"),
    "vix_futures": ("S&P 500 VIX", "index_future"),
}

CORE_MARKET_FALLBACKS = {
    "加權指數": "taiwan_weighted",
    "S&P 500": "sp500",
    "Nasdaq": "nasdaq",
    "費城半導體": "sox",
    "美元指數": "dollar_index",
    "VIX": "vix",
    "美國10年期公債殖利率": "us10y_yield",
}


def _number(value: Any) -> float | None:
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


def decode_stockq_value(payload: str) -> str | None:
    """Decode the public value rendered by StockQ's sq-obfuscate.js."""
    try:
        fields = base64.b64decode(payload).decode("utf-8").split("|")
        if len(fields) != 6:
            return None
        seed = int(fields.pop(0))
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None

    def random_value() -> float:
        nonlocal seed
        seed = seed * 48271 % 2147483647
        return seed / 2147483647

    random_value()
    random_value()
    order = [0, 1, 2]
    for index in range(2, 0, -1):
        swap = int(random_value() * (index + 1))
        order[index], order[swap] = order[swap], order[index]
    random_value()
    fake_position_1 = int(random_value() * 4)
    random_value()
    fake_position_2 = int(random_value() * 5)
    fields.pop(fake_position_2)
    fields.pop(fake_position_1)
    result = ["", "", ""]
    for index in range(3):
        result[order[index]] = fields[index]
    return "".join(result)


class _StockQTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._capture = False
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            classes = set(str(attributes.get("class") or "").split())
            if not self._capture and classes.intersection(
                {"marketdatatable", "returntable", "bonddatatable"}
            ):
                self._capture = True
                self._table_depth = 1
                self._rows = []
                return
            if self._capture:
                self._table_depth += 1
        if not self._capture:
            return
        if tag == "tr" and self._table_depth == 1:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "span" and self._cell is not None and attributes.get("data-sq"):
            decoded = decode_stockq_value(str(attributes["data-sq"]))
            if decoded is not None:
                self._cell.append(decoded)

    def handle_data(self, data: str) -> None:
        if self._capture and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table_depth == 1:
            if self._row:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append(self._rows)
                self._capture = False
                self._rows = []


def parse_stockq_html(html: str) -> dict[str, dict[str, Any]]:
    parser = _StockQTableParser()
    parser.feed(html)
    candidates: list[dict[str, Any]] = []
    for rows in parser.tables:
        title = rows[0][0] if rows and rows[0] else ""
        for row in rows[1:]:
            if len(row) < 3:
                continue
            label = row[0].strip()
            if label in {"股市", "名稱", "匯率", "債券", "商品"}:
                continue
            candidates.append({
                "label": label,
                "price": _number(row[1]),
                "change": _number(row[2]),
                "change_pct": _number(row[3]) if len(row) >= 4 else None,
                "observed_at": row[4] if len(row) >= 5 else row[-1],
                "table": re.sub(r"\s+", " ", title).strip(),
            })

    result: dict[str, dict[str, Any]] = {}
    for key, (label, category) in TARGETS.items():
        matched = [row for row in candidates if row["label"] == label]
        if category == "index_future":
            matched = [row for row in matched if "期貨" in row["table"]]
        else:
            matched = [row for row in matched if "期貨" not in row["table"]]
        usable = next((row for row in matched if row["price"] is not None), None)
        if usable:
            result[key] = {
                **usable,
                "category": category,
                "source": "StockQ",
                "source_url": SOURCE_URL,
            }
    return result


def _market_signal(indicators: dict[str, dict[str, Any]]) -> dict[str, Any]:
    score = 50.0
    reasons: list[str] = []

    def change(key: str) -> float | None:
        return _number((indicators.get(key) or {}).get("change_pct"))

    vix_price = _number((indicators.get("vix") or {}).get("price"))
    if vix_price is not None:
        if vix_price >= 30:
            score -= 18
            reasons.append("VIX高於30")
        elif vix_price >= 25:
            score -= 12
            reasons.append("VIX高於25")
        elif vix_price <= 15:
            score += 6
            reasons.append("VIX低於15")
    for key, label, weight in (
        ("sp500_futures", "S&P 500期貨", 3.0),
        ("nasdaq100_futures", "NASDAQ 100期貨", 3.0),
        ("sox", "費城半導體", 2.0),
    ):
        value = change(key)
        if value is not None:
            contribution = max(-7.0, min(7.0, value * weight))
            score += contribution
            if abs(value) >= 0.5:
                reasons.append(f"{label}{'走強' if value > 0 else '走弱'} {value:.2f}%")
    dollar_change = change("dollar_index")
    if dollar_change is not None:
        score -= max(-4.0, min(4.0, dollar_change * 4.0))
    score = round(max(0.0, min(100.0, score)), 1)
    return {
        "score": score,
        "regime": "偏多" if score >= 60 else "偏空" if score < 40 else "中性",
        "reasons": reasons or ["StockQ市場指標沒有明顯一致方向"],
        "affects_formal_ranking": False,
    }


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_age(payload: dict[str, Any], now_epoch: float) -> float | None:
    fetched_epoch = _number(payload.get("fetched_epoch"))
    return max(0.0, now_epoch - fetched_epoch) if fetched_epoch is not None else None


def _observed_session_date(value: Any, updated_at: str) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{2}/\d{2}", text):
        return None
    try:
        year = datetime.fromisoformat(updated_at).year
        month, day = (int(part) for part in text.split("/"))
        candidate = datetime(year, month, day)
        updated = datetime.fromisoformat(updated_at)
        if candidate > updated.replace(tzinfo=None):
            candidate = candidate.replace(year=year - 1)
        return candidate.date().isoformat()
    except (TypeError, ValueError):
        return None


def apply_stockq_market_fallback(
    primary: dict[str, dict[str, Any]],
    stockq: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Fill missing completed market indicators without overwriting primary data."""
    output = {label: dict(row or {}) for label, row in primary.items()}
    indicators = stockq.get("indicators") or {}
    updated_at = str(stockq.get("updated_at") or "")
    for label, indicator_key in CORE_MARKET_FALLBACKS.items():
        fallback = indicators.get(indicator_key) or {}
        price = _number(fallback.get("price"))
        if price is None:
            continue
        current = output.setdefault(label, {})
        used = False
        if _number(current.get("price")) is None:
            current["price"] = price
            used = True
        if _number(current.get("change_pct")) is None:
            current["change_pct"] = _number(fallback.get("change_pct"))
            used = True
        if not current.get("session_date"):
            current["session_date"] = _observed_session_date(
                fallback.get("observed_at"), updated_at
            )
            used = True
        if used:
            current["source"] = "StockQ_after_close_fallback"
            current["source_url"] = SOURCE_URL
    return output


def update_stockq_market_context(
    reports_dir: Path,
    *,
    updated_at: str,
    timeout: int = 20,
    now_epoch: float | None = None,
    fetcher: Callable[[], str] | None = None,
    allow_network: bool = True,
) -> dict[str, Any]:
    """Fetch after close only and keep a dated fallback for market indicators."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "stockq_market_context.json"
    cached = _read_cache(path)
    now_epoch = time.time() if now_epoch is None else now_epoch
    age = _cache_age(cached, now_epoch) if cached else None
    if not allow_network:
        if cached:
            return {
                **cached,
                "cache_status": "after_close_hold",
                "cache_age_seconds": round(age) if age is not None else None,
                "network_fetch_skipped": True,
                "after_close_only": True,
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": updated_at,
            "status": "waiting_for_close",
            "cache_status": "missing",
            "source": "StockQ",
            "source_url": SOURCE_URL,
            "role": "收盤後全球市場與總體環境備援層",
            "affects_formal_ranking": False,
            "overwrites_symbol_data": False,
            "indicator_count": 0,
            "indicators": {},
            "market_signal": {
                "score": None,
                "regime": "等待收盤",
                "reasons": ["盤中禁止抓價；等待正式收盤後再更新 StockQ 備援"],
                "affects_formal_ranking": False,
            },
            "network_fetch_skipped": True,
            "after_close_only": True,
        }
    if cached and age is not None and age < CACHE_TTL_SECONDS:
        return {
            **cached,
            "cache_status": "fresh",
            "cache_age_seconds": round(age),
            "after_close_only": True,
        }

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
        indicators = parse_stockq_html(html)
        if len(indicators) < 8:
            raise ValueError(f"StockQ可用指標不足：{len(indicators)}")
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
            "role": "收盤後全球市場與總體環境備援層",
            "affects_formal_ranking": False,
            "overwrites_symbol_data": False,
            "after_close_only": True,
            "indicator_count": len(indicators),
            "indicators": indicators,
            "market_signal": _market_signal(indicators),
            "warning": "StockQ只在收盤後補主要來源缺少的市場指標；個股價格、財報及逐筆成交仍以授權或官方來源為準。",
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return payload

    if cached and age is not None and age <= MAX_STALE_SECONDS:
        return {
            **cached,
            "status": "stale_fallback",
            "cache_status": "stale",
            "cache_age_seconds": round(age),
            "last_error": error,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "status": "unavailable",
        "cache_status": "missing",
        "source": "StockQ",
        "source_url": SOURCE_URL,
        "role": "收盤後全球市場與總體環境備援層",
        "affects_formal_ranking": False,
        "overwrites_symbol_data": False,
        "after_close_only": True,
        "indicator_count": 0,
        "indicators": {},
        "market_signal": {
            "score": None,
            "regime": "資料不足",
            "reasons": ["StockQ暫時無法連線且沒有可用快取"],
            "affects_formal_ranking": False,
        },
        "last_error": error,
    }
