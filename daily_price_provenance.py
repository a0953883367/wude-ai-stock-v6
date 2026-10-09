"""Attest the daily Yahoo bars actually ingested, separately from live quotes.

This adapter does not certify exchange authority, session completion, or freshness.
Those require the independent calendar gates. Intraday aggregates, SIP snapshots,
and close-only StockQ/Tiingo valuation fallbacks cannot acquire this attestation.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


YAHOO_DAILY_SOURCE = "Yahoo Finance daily bars"
_ATTRIBUTE = "us_daily_ohlcv_provenance"
_COLUMNS = ("open", "high", "low", "close", "volume")


def observed_daily_volume(frame: pd.DataFrame) -> float | None:
    """Return the supplied last-bar share volume; missing is never zero."""
    if frame.empty or "volume" not in frame:
        return None
    value = frame["volume"].iloc[-1]
    if pd.api.types.is_bool(value):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _last_bar(frame: pd.DataFrame, market: str = "US") -> tuple[str | None, list[float] | None]:
    if (frame.empty or not isinstance(frame.index, pd.DatetimeIndex)
            or not frame.index.is_monotonic_increasing or not frame.index.is_unique):
        return None, None
    stamp = frame.index[-1]
    if pd.isna(stamp):
        return None, None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Taipei" if market == "TW" else "America/New_York")
    session = stamp.date().isoformat()
    if not set(_COLUMNS).issubset(frame.columns):
        return session, None
    if any(pd.api.types.is_bool(frame[column].iloc[-1]) for column in _COLUMNS):
        return session, None
    try:
        values = [float(frame[column].iloc[-1]) for column in _COLUMNS]
    except (TypeError, ValueError):
        return session, None
    if (not all(math.isfinite(value) and value > 0 for value in values[:4])
            or observed_daily_volume(frame) is None
            or values[1] < max(values[0], values[2], values[3])
            or values[2] > min(values[0], values[1], values[3])):
        return session, None
    return session, values


def attest_yahoo_daily(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Call only immediately after extracting a Yahoo interval=1d response.

Yahoo US ticker syntax excludes indices, currencies, and foreign exchange
suffixes. The consumer must also explicitly classify the security as US.
"""
    symbol = str(symbol).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9-]*", symbol):
        return frame
    session, values = _last_bar(frame)
    frame.attrs[_ATTRIBUTE] = {
        "source": YAHOO_DAILY_SOURCE, "symbol": symbol, "interval": "1d",
        "session_date": session, "price_unit": "USD/shares", "ohlcv": values,
    }
    return frame


def us_daily_metadata(frame: pd.DataFrame, symbol: str, *, promoted: bool = False) -> dict[str, Any]:
    """Fail closed if a later transformation changed the attested last bar."""
    proof = frame.attrs.get(_ATTRIBUTE) or {}
    if not isinstance(proof, dict):
        proof = {}
    session, values = _last_bar(frame)
    matches = bool(
        not promoted and proof.get("source") == YAHOO_DAILY_SOURCE
        and proof.get("symbol") == str(symbol).upper()
        and proof.get("interval") == "1d" and proof.get("price_unit") == "USD/shares"
        and session and session == proof.get("session_date")
        and values is not None and values == proof.get("ohlcv")
    )
    return {
        "us_daily_source": proof.get("source") if matches else None,
        "us_daily_session_date": proof.get("session_date") if matches else None,
        "us_daily_price_available": matches,
        "us_daily_price_unit": proof.get("price_unit") if matches else None,
        "source_daily_ohlcv_complete": matches,
        "source_daily_ohlcv_session_date": proof.get("session_date") if matches else None,
    }


def tw_official_daily_metadata(frame: pd.DataFrame, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attest a complete exchange snapshot, never a mixed partial overlay."""
    session, values = _last_bar(frame, "TW")
    try:
        source_frame = pd.DataFrame([snapshot], index=pd.to_datetime([snapshot.get("date")]))
        source_session, source_values = _last_bar(source_frame, "TW")
    except (TypeError, ValueError):
        source_session, source_values = None, None
    matches = bool(
        snapshot.get("tw_official_price_available") is True
        and snapshot.get("tw_price_source") in {"TWSE OpenAPI", "TPEx OpenAPI"}
        and snapshot.get("tw_price_unit") == "TWD/shares"
        and session and session == source_session
        and values is not None and values == source_values
    )
    return {
        "source_daily_ohlcv_complete": matches,
        "source_daily_ohlcv_session_date": source_session if matches else None,
    }
