"""Official exchange-session calendars with persistent, fail-closed caching.

Taiwan sessions are derived from the official TWSE and TPEx holiday schedules.
US sessions are read from Alpaca's market-calendar API, including early closes.
The scoring layer never falls back to an assumed Monday-Friday calendar.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


VERSION = 1
MARKETS = ("TW", "US")
MARKET_ZONES = {"TW": ZoneInfo("Asia/Taipei"), "US": ZoneInfo("America/New_York")}
REFRESH_SECONDS = 12 * 60 * 60
HTTP_TIMEOUT_SECONDS = 20
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _years_between(start: date, end: date) -> list[int]:
    return list(range(start.year, end.year + 1))


def _weekday_dates(year: int) -> list[str]:
    cursor = date(year, 1, 1)
    end = date(year, 12, 31)
    result = []
    while cursor <= end:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def parse_twse_holidays(payload: Any, year: int) -> set[str]:
    """Validate and extract official TWSE closed dates."""
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise ValueError("invalid TWSE holiday response")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("TWSE holiday rows missing")
    closed: set[str] = set()
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        value = str(row[0])
        parsed = _parse_date(value)
        if parsed.year == year:
            closed.add(value)
    if not closed:
        raise ValueError("TWSE holiday schedule empty")
    return closed


def parse_tpex_holidays(payload: Any, year: int) -> set[str]:
    """Extract TPEx closed dates from its official holiday-table response."""
    html = ((payload or {}).get("data") or {}).get("html") if isinstance(payload, dict) else None
    if not isinstance(html, str) or not html:
        raise ValueError("invalid TPEx holiday response")
    parser = _TableParser()
    parser.feed(html)
    current_month: int | None = None
    closed: set[str] = set()
    for cells in parser.rows:
        if not cells:
            continue
        first = cells[0].strip().lower()
        if first in MONTHS:
            current_month = MONTHS[first]
            if len(cells) < 3:
                continue
            date_text, description = cells[1], cells[2]
        elif current_month is not None and cells:
            date_text = cells[0]
            # A one-cell row inherits the previous row-spanned holiday
            # description (for example the second clearing-only day).
            description = cells[-1] if len(cells) >= 2 else "inherited closed holiday"
        else:
            continue
        # "Last Trading Day" rows are informational and remain open sessions.
        if "last trading day" in description.lower():
            continue
        for day_text in re.findall(r"\b([0-3]?\d)\s*\(", date_text):
            day = int(day_text)
            try:
                closed.add(date(year, current_month, day).isoformat())
            except ValueError:
                continue
    if not closed:
        raise ValueError("TPEx holiday schedule empty")
    return closed


def parse_alpaca_calendar(payload: Any, year: int) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(payload, list):
        raise ValueError("invalid Alpaca calendar response")
    sessions: list[str] = []
    details: dict[str, dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        value = str(row["date"])
        parsed = _parse_date(value)
        if parsed.year != year:
            continue
        market_open = str(row.get("open") or "")
        market_close = str(row.get("close") or "")
        sessions.append(value)
        details[value] = {
            "open": market_open,
            "close": market_close,
            "early_close": bool(market_close and market_close not in {"16:00", "16:00:00"}),
        }
    sessions = sorted(set(sessions))
    if not sessions:
        raise ValueError("Alpaca calendar has no sessions")
    return sessions, details


class OfficialMarketCalendar:
    """Thread-safe official calendar cache used only to validate settlement dates."""

    def __init__(
        self,
        state_path: Path,
        *,
        clock: Callable[[], float] = time.time,
        fetch_json: Callable[[str, dict[str, str]], Any] | None = None,
        auto_refresh: bool = True,
        allow_network: bool | None = None,
    ) -> None:
        self.state_path = state_path
        self.clock = clock
        self.fetch_json = fetch_json or self._fetch_json
        self.allow_network = ("pytest" not in sys.modules) if allow_network is None else bool(allow_network)
        self._lock = threading.RLock()
        self._refreshing = False
        self._last_error: dict[str, str | None] = {market: None for market in MARKETS}
        self._state = self._load()
        if auto_refresh and self.allow_network:
            self.refresh_async()

    def _empty(self) -> dict[str, Any]:
        return {"version": VERSION, "markets": {market: {"years": {}} for market in MARKETS}}

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return self._empty()
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            return self._empty()
        markets = payload.get("markets")
        if not isinstance(markets, dict):
            return self._empty()
        for market in MARKETS:
            market_row = markets.setdefault(market, {})
            if not isinstance(market_row.get("years"), dict):
                market_row["years"] = {}
        return payload

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.state_path)

    @staticmethod
    def _fetch_json(url: str, headers: dict[str, str]) -> Any:
        request = Request(url, headers={"User-Agent": "wude-market-calendar/1", **headers})
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))

    def _target_years(self) -> list[int]:
        current = datetime.fromtimestamp(self.clock(), timezone.utc).year
        # The next year's Taiwan schedule is often not published yet. Fetch it
        # on demand only when a lookup actually crosses the year boundary.
        return [current - 1, current]

    def _fresh(self, row: dict[str, Any]) -> bool:
        fetched = row.get("fetched_at_epoch")
        try:
            return self.clock() - float(fetched) < REFRESH_SECONDS
        except (TypeError, ValueError):
            return False

    def refresh_async(self, years: list[int] | None = None) -> None:
        if not self.allow_network:
            return
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        thread = threading.Thread(target=self._refresh_worker, args=(years or self._target_years(),), daemon=True)
        thread.start()

    def refresh(self, years: list[int] | None = None) -> None:
        self._refresh_worker(years or self._target_years())

    def _refresh_worker(self, years: list[int]) -> None:
        try:
            for year in sorted(set(years)):
                for market in MARKETS:
                    with self._lock:
                        saved = ((self._state["markets"][market].get("years") or {}).get(str(year)) or {})
                        if saved and self._fresh(saved):
                            continue
                    try:
                        row = self._fetch_tw(year) if market == "TW" else self._fetch_us(year)
                    except Exception as exc:  # keep the last verified cache on any provider failure
                        with self._lock:
                            self._last_error[market] = f"{type(exc).__name__}: {exc}"
                        continue
                    with self._lock:
                        self._state["markets"][market]["years"][str(year)] = row
                        self._last_error[market] = None
                        self._save()
        finally:
            with self._lock:
                self._refreshing = False

    def _fetch_tw(self, year: int) -> dict[str, Any]:
        twse_error: Exception | None = None
        tpex_error: Exception | None = None
        twse_closed: set[str] | None = None
        tpex_closed: set[str] | None = None
        try:
            query = urlencode({"date": str(year), "response": "json"})
            payload = self.fetch_json(
                f"https://www.twse.com.tw/rwd/en/holidaySchedule/holidaySchedule?{query}", {}
            )
            twse_closed = parse_twse_holidays(payload, year)
        except Exception as exc:
            twse_error = exc
        try:
            query = urlencode({"date": str(year)})
            payload = self.fetch_json(
                f"https://www.tpex.org.tw/www/en-us/bulletin/tradingDate?{query}", {}
            )
            tpex_closed = parse_tpex_holidays(payload, year)
        except Exception as exc:
            tpex_error = exc
        if twse_closed is None and tpex_closed is None:
            raise RuntimeError(f"TWSE={twse_error}; TPEx={tpex_error}")
        weekday = set(_weekday_dates(year))
        twse_weekday = (twse_closed or set()) & weekday
        tpex_weekday = (tpex_closed or set()) & weekday
        closed = (twse_weekday | tpex_weekday)
        sources = []
        if twse_closed is not None:
            sources.append("TWSE")
        if tpex_closed is not None:
            sources.append("TPEx")
        conflicts = sorted(twse_weekday ^ tpex_weekday) if twse_closed is not None and tpex_closed is not None else []
        status = "verified_twse_tpex" if len(sources) == 2 and not conflicts else (
            "verified_conservative_union" if len(sources) == 2 else f"verified_{sources[0].lower()}_only"
        )
        return {
            "year": year,
            "status": status,
            "sources": sources,
            "sessions": sorted(weekday - closed),
            "session_details": {},
            "closed_dates": sorted(closed),
            "conflict_dates": conflicts,
            "fetched_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds"),
            "fetched_at_epoch": self.clock(),
        }

    def _fetch_us(self, year: int) -> dict[str, Any]:
        key = os.getenv("ALPACA_API_KEY_ID", "").strip()
        secret = os.getenv("ALPACA_API_SECRET_KEY", "").strip()
        if not key or not secret:
            raise RuntimeError("Alpaca calendar credentials unavailable")
        query = urlencode({"start": f"{year}-01-01", "end": f"{year}-12-31"})
        payload = self.fetch_json(
            f"https://paper-api.alpaca.markets/v2/calendar?{query}",
            {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        sessions, details = parse_alpaca_calendar(payload, year)
        return {
            "year": year,
            "status": "verified_alpaca",
            "sources": ["Alpaca Market Calendar"],
            "sessions": sessions,
            "session_details": details,
            "closed_dates": [],
            "conflict_dates": [],
            "fetched_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds"),
            "fetched_at_epoch": self.clock(),
        }

    def lookup(self, market: str, start_exclusive: str, end_inclusive: str) -> dict[str, Any]:
        """Return exact official sessions in (start, end], never guessed weekdays."""
        market = str(market or "").upper()
        try:
            start = _parse_date(start_exclusive)
            end = _parse_date(end_inclusive)
        except (TypeError, ValueError):
            return {"available": False, "status": "invalid_date", "sessions": []}
        if market not in MARKETS or end < start:
            return {"available": False, "status": "invalid_request", "sessions": []}
        years = _years_between(start, end)
        with self._lock:
            rows = [((self._state["markets"][market].get("years") or {}).get(str(year))) for year in years]
            missing = [year for year, row in zip(years, rows) if not isinstance(row, dict) or not row.get("sessions")]
            if missing:
                if self.allow_network:
                    self.refresh_async(missing)
                return {
                    "available": False,
                    "status": "loading_official_calendar" if self._refreshing else "official_calendar_unavailable",
                    "sessions": [],
                    "missing_years": missing,
                }
            sessions = sorted({value for row in rows for value in row.get("sessions", []) if start_exclusive < value <= end_inclusive})
            statuses = sorted({str(row.get("status") or "") for row in rows})
            details = {key: value for row in rows for key, value in (row.get("session_details") or {}).items() if key in sessions}
        return {
            "available": True,
            "status": "verified" if all(value.startswith("verified") for value in statuses) else "cached",
            "source_statuses": statuses,
            "sessions": sessions,
            "session_details": details,
        }

    def status(self, market: str) -> dict[str, Any]:
        market = str(market or "").upper()
        with self._lock:
            years = (self._state.get("markets", {}).get(market, {}).get("years") or {})
            rows = [row for row in years.values() if isinstance(row, dict) and row.get("sessions")]
            current_year = str(datetime.fromtimestamp(self.clock(), MARKET_ZONES.get(market, timezone.utc)).year)
            current = years.get(current_year) if isinstance(years.get(current_year), dict) else None
            early_closes = sum(
                bool(detail.get("early_close"))
                for row in rows for detail in (row.get("session_details") or {}).values()
                if isinstance(detail, dict)
            )
            return {
                "market": market,
                "available": current is not None,
                "status": (current or {}).get("status") or ("loading_official_calendar" if self._refreshing else "official_calendar_unavailable"),
                "sources": (current or {}).get("sources") or [],
                "covered_years": sorted(int(year) for year, row in years.items() if isinstance(row, dict) and row.get("sessions")),
                "fetched_at": (current or {}).get("fetched_at"),
                "conflict_dates": (current or {}).get("conflict_dates") or [],
                "early_close_sessions_cached": early_closes,
                "refreshing": self._refreshing,
                "last_error": self._last_error.get(market) if current is None else None,
                "fallback_policy": "verified_cache_or_quarantine_never_guess",
            }

    def session_complete(self, market: str, session_date: str, *, at_epoch: float | None = None) -> bool:
        """Confirm the official close time has passed, including US early closes."""
        market = str(market or "").upper()
        if market not in MARKETS:
            return False
        try:
            parsed = _parse_date(session_date)
        except (TypeError, ValueError):
            return False
        with self._lock:
            row = ((self._state["markets"][market].get("years") or {}).get(str(parsed.year)) or {})
            if session_date not in (row.get("sessions") or []):
                return False
            if market == "US":
                close_text = str(((row.get("session_details") or {}).get(session_date) or {}).get("close") or "")
                match = re.match(r"^(\d{1,2}):(\d{2})", close_text)
                if not match:
                    return False
                hour, minute = int(match.group(1)), int(match.group(2))
            else:
                hour, minute = 13, 30
        closed_at = datetime(parsed.year, parsed.month, parsed.day, hour, minute, tzinfo=MARKET_ZONES[market])
        now = self.clock() if at_epoch is None else float(at_epoch)
        return now >= closed_at.timestamp()
