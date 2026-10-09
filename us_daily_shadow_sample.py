"""Private, non-decision daily samples from an already-fetched Alpaca snapshot.

No I/O, history substitution, provider requests, or public-report fields belong
here. A closed exchange session does not establish immutable daily-bar finality:
Alpaca daily volume can still change with extended-hours trades/corrections.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone
import math
import re
from typing import Any
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")
SNAPSHOT_ENDPOINT = "https://data.alpaca.markets/v2/stocks/snapshots"
BAR_FIELDS = ("t", "o", "h", "l", "c", "v")


def _bar_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _cached_close(calendar: Any, session: str) -> tuple[datetime | None, str]:
    """The cache-only export cannot trigger the calendar's async refresh."""
    if calendar is None:
        return None, "official_calendar_unavailable"
    try:
        day = date.fromisoformat(session)
        cached = calendar.relay_us_year(day.year)
        if (cached.get("year") != day.year or cached.get("status") != "verified_alpaca"
                or cached.get("sources") != ["Alpaca Market Calendar"]):
            return None, "official_calendar_unverified"
        if session not in (cached.get("sessions") or []):
            return None, "not_official_session"
        detail = (cached.get("session_details") or {}).get(session) or {}
        opening, closing = str(detail.get("open") or ""), str(detail.get("close") or "")
        if not all(re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value) for value in (opening, closing)):
            return None, "official_close_missing"
        open_time, close_time = time.fromisoformat(opening), time.fromisoformat(closing)
        if close_time <= open_time:
            return None, "official_close_invalid"
        return datetime.combine(day, close_time, NEW_YORK), "verified_alpaca"
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
        return None, "official_calendar_unavailable"


def build_us_daily_shadow_sample(
    symbol: str, snapshot: dict[str, Any], *, feed: str, calendar: Any = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Preserve source fields separately; never attest any derived indicators."""
    observed_at = observed_at or datetime.now(timezone.utc)
    observation_valid = observed_at.tzinfo is not None and observed_at.utcoffset() is not None
    observed = observed_at.astimezone(timezone.utc) if observation_valid else None
    source = snapshot if isinstance(snapshot, dict) else {}
    candidates: list[dict[str, Any]] = []
    for field in ("dailyBar", "prevDailyBar", "previousDailyBar"):
        bar = source.get(field)
        if not isinstance(bar, dict):
            continue
        # Whitelist only required provider data, never copy arbitrary payloads.
        candidate: dict[str, Any] = {
            "provider_field": field,
            "raw_bar": {key: deepcopy(bar[key]) for key in BAR_FIELDS if key in bar},
            "session_date": None,
            "ohlcv_complete": False,
            "calendar_verified": False,
            "session_closed": False,
            "official_close_at": None,
            "status": "invalid_bar_timestamp",
        }
        candidates.append(candidate)
        try:
            stamp = datetime.fromisoformat(str(bar.get("t") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                continue
            local = stamp.astimezone(NEW_YORK)
            # Provider daily timestamps are the start of the New York day.
            if local.time() != time(0):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        candidate["session_date"] = local.date().isoformat()
        values = {key: _bar_number(bar.get(key)) for key in ("o", "h", "l", "c", "v")}
        if any(value is None for value in values.values()):
            candidate["status"] = "missing_or_invalid_ohlcv"
            continue
        if not (values["l"] <= min(values["o"], values["c"]) <= max(values["o"], values["c"]) <= values["h"]):
            candidate["status"] = "inconsistent_ohlcv"
            continue
        candidate["ohlcv_complete"] = True
        if feed != "sip":
            candidate["status"] = "unapproved_feed"
            continue
        if observed is None:
            candidate["status"] = "invalid_observation_time"
            continue
        closing, status = _cached_close(calendar, candidate["session_date"])
        candidate["status"] = status
        if closing is None:
            continue
        candidate["calendar_verified"] = True
        candidate["official_close_at"] = closing.isoformat()
        candidate["session_closed"] = observed >= closing
        candidate["status"] = "closed_session_sample" if candidate["session_closed"] else "source_session_not_closed"

    closed = [item for item in candidates if item["session_closed"]]
    selected = max(closed, key=lambda item: item["session_date"], default=None)
    # Conflicting provider aliases must not silently choose a preferred price.
    conflict = bool(selected and any(
        item["session_date"] == selected["session_date"] and item["raw_bar"] != selected["raw_bar"]
        for item in closed
    ))
    return {
        "schema_version": 1,
        "symbol": str(symbol).upper(),
        "source": "Alpaca SIP" if feed == "sip" else f"Alpaca {str(feed).upper()}",
        "feed": feed,
        "endpoint": SNAPSHOT_ENDPOINT,
        "observed_at": observed.isoformat() if observed is not None else None,
        "status": "conflicting_daily_bars" if conflict else "closed_session_sample" if selected else "no_closed_session_sample",
        "candidates": candidates,
        "selected": deepcopy(selected) if selected and not conflict else None,
        "price_unit": "USD/shares",
        "bar_finality_verified": False,
        "history_ready": False,
        "decision_eligible": False,
        "affects_formal": False,
        "private_only": True,
        "redistribution_authorized": False,
    }


def build_daily_shadow_samples(payloads: dict[str, Any], calendar: Any, now: datetime) -> dict[str, Any]:
    """Revalidate a private collector using capture-time, never later close-time.

    Safe to persist only in an ignored private cache. Do not write this report
    into all_analysis, prediction histories, feature rows, or public artifacts.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("shadow capture time must have a timezone")
    samples: dict[str, Any] = {}
    for symbol, payload in (payloads or {}).items():
        if not isinstance(payload, dict) or not re.fullmatch(r"[A-Z0-9.\-]{1,16}", str(symbol)):
            continue
        if (payload.get("symbol") != symbol or payload.get("endpoint") != SNAPSHOT_ENDPOINT
                or payload.get("source") != "Alpaca SIP" or payload.get("feed") != "sip"):
            continue
        try:
            observed = datetime.fromisoformat(str(payload.get("observed_at") or ""))
            if observed.tzinfo is None or observed.utcoffset() is None or observed > now:
                continue
        except (TypeError, ValueError):
            continue
        raw = {}
        for item in payload.get("candidates") or []:
            if isinstance(item, dict) and item.get("provider_field") in {"dailyBar", "prevDailyBar", "previousDailyBar"}:
                raw[item["provider_field"]] = item.get("raw_bar")
        samples[symbol] = build_us_daily_shadow_sample(
            symbol, raw, feed="sip", calendar=calendar, observed_at=observed,
        )
    return {
        "schema_version": 1,
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "closed_sample_count": sum(item["selected"] is not None for item in samples.values()),
        "samples": samples,
        "history_ready": False,
        "decision_eligible": False,
        "affects_formal": False,
        "private_only": True,
        "redistribution_authorized": False,
    }
