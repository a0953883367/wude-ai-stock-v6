"""Bounded current-session fallback from TWSE's licensed daily CSV.

Official resource: https://data.gov.tw/dataset/11549 (Open Government Data
License 1.0). This is one current snapshot, never historical-bar fabrication.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import io
import logging
import math
from pathlib import Path
import re
from typing import Callable

import requests

from config import SETTINGS, TAIPEI
from market_calendar import OfficialMarketCalendar

SOURCE = "TWSE Open Data"
URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data"
_FIELDS = {"日期", "證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數"}
_COLUMNS = dict(zip(("open", "high", "low", "close", "volume"),
                    ("開盤價", "最高價", "最低價", "收盤價", "成交股數")))
LOG = logging.getLogger(__name__)


def completed_tw_session(calendar: OfficialMarketCalendar, now: datetime) -> str | None:
    """Use a verified official calendar, including closures; no weekday guesses."""
    if now.tzinfo is None:
        return None
    today = now.astimezone(TAIPEI).date()
    result = calendar.lookup("TW", (today - timedelta(days=14)).isoformat(), today.isoformat())
    if not result.get("available") or result.get("status") != "verified":
        return None
    return next((day for day in reversed(result["sessions"])
                 if calendar.session_complete("TW", day, at_epoch=now.timestamp())), None)


def _complete_bar(row: dict) -> bool:
    try:
        values = [row[key] for key in _COLUMNS]
        if any(isinstance(v, bool) for v in values):
            return False
        o, h, l, c, v = map(float, values)
        return (all(math.isfinite(x) and x > 0 for x in (o, h, l, c))
                and math.isfinite(v) and v >= 0 and v.is_integer()
                and h >= max(o, l, c) and l <= min(o, h, c))
    except (KeyError, TypeError, ValueError):
        return False


def parse_current_csv(text: str, stock_ids: set[str], session: str) -> dict[str, dict]:
    """Exact identifiers, ROC/ISO date, complete OHLCV; duplicate codes reject."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not _FIELDS.issubset(reader.fieldnames or []):
        raise ValueError("unexpected TWSE open-data schema")
    result, seen, duplicates = {}, set(), set()
    for raw in reader:
        sid = str(raw.get("證券代號") or "").strip()
        if sid not in stock_ids:
            continue
        if sid in seen:
            duplicates.add(sid)
        seen.add(sid)
        stamp = str(raw.get("日期") or "").strip()
        if not re.fullmatch(r"\d{7}|\d{8}", stamp):
            continue
        try:
            year = int(stamp[:-4]) + (1911 if len(stamp) == 7 else 0)
            day = date(year, int(stamp[-4:-2]), int(stamp[-2:])).isoformat()
            volume_text = str(raw["成交股數"]).strip().replace(",", "")
            # Parse shares before float conversion: fractional/negative tiny
            # numbers must not round into apparently valid integer volumes.
            if not re.fullmatch(r"[0-9]+", volume_text):
                continue
            volume = int(volume_text)
            if volume > 2 ** 53:
                continue  # pandas OHLCV bars cannot preserve this exactly
            values = {key: float(str(raw[column]).replace(",", ""))
                      for key, column in _COLUMNS.items() if key != "volume"}
            values["volume"] = volume
        except (ValueError, TypeError, KeyError):
            continue
        if day != session or not _complete_bar(values):
            continue
        result[sid] = {**values, "date": day,
                       "tw_official_price_available": True,
                       "tw_price_source": SOURCE, "tw_price_unit": "TWD/shares",
                       "tw_price_source_url": URL}
    for sid in duplicates:
        result.pop(sid, None)
    return result


def _fetch_csv() -> str:
    # One batch request, no per-symbol retry storm or alternate historical API.
    with requests.get(URL, timeout=min(15, SETTINGS.request_timeout), stream=True,
                      headers={"User-Agent": "WudeAIStock/1.0 official-market-data"}) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError("oversized TWSE open-data response")
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8-sig")


def supplement_twse_prices(prices: dict, stock_ids: set[str], calendar_path: Path,
                           *, now: datetime | None = None,
                           fetch_text: Callable[[], str] | None = None,
                           calendar=None) -> tuple[dict, dict]:
    """Only fill stale/missing TWSE bars; failures preserve the primary result."""
    output = dict(prices)
    status = {"source": SOURCE, "requested": False, "replaced_count": 0,
              "required_count": 0, "session_date": None, "status": "calendar_unavailable"}
    try:
        calendar = calendar or OfficialMarketCalendar(calendar_path, auto_refresh=False, allow_network=False)
        session = completed_tw_session(calendar, now or datetime.now(TAIPEI))
        status["session_date"] = session
        if not session:
            return output, status
        needed = {sid for sid in stock_ids
                  if not prices.get(sid) or not _complete_bar(prices[sid])
                  or str(prices[sid].get("date") or "") < session}
        # Never replace a genuinely newer primary date, even if incomplete.
        needed = {sid for sid in needed if str(prices.get(sid, {}).get("date") or "") <= session}
        status["required_count"] = len(needed)
        if not needed:
            status["status"] = "primary_current"
            return output, status
        status["requested"] = True
        candidates = parse_current_csv((fetch_text or _fetch_csv)(), needed, session)
        output.update(candidates)
        status.update(replaced_count=len(candidates),
                      status="complete" if len(candidates) == len(needed) else "incomplete")
    except Exception:
        status["status"] = "unavailable"
        LOG.warning("TWSE open-data fallback unavailable; retaining primary prices")
    LOG.info("TWSE open-data fallback: %s", status)
    return output, status
