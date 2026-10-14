"""Allowlisted, date-only evidence for an aborted report; never model inputs."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import pandas as pd

COHORTS = ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF")
FILENAME = "report_session_failure.json"


def _date(value) -> str | None:
    """Do not echo arbitrary provider strings, URLs, tokens or exception text."""
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if value == parsed.isoformat() else None


def _cohort(row: dict) -> str | None:
    market = row.get("market")
    if market not in ("TW", "US"):
        return None
    return f"{market}_{'ETF' if 'ETF' in str(row.get('type', '')).upper() else 'STOCK'}"


def _summary(values: list[str | None]) -> dict:
    counts = Counter(value for value in values if value)
    dominant, count = counts.most_common(1)[0] if counts else (None, 0)
    return {
        "expected_count": len(values),
        "dated_count": sum(counts.values()),
        "missing_or_invalid_count": len(values) - sum(counts.values()),
        "session_counts": dict(sorted(counts.items())),
        "dominant_session": dominant,
        "dominant_coverage_pct": round(count / len(values) * 100, 2) if values else 0.0,
    }


def _history_date(frame, market: str) -> str | None:
    if frame is None or frame.empty or "close" not in frame:
        return None
    # Match build_features: its final non-null close supplies the session.
    valid = frame.dropna(subset=["close"])
    if valid.empty:
        return None
    stamp = pd.Timestamp(valid.index[-1])
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(ZoneInfo("Asia/Taipei" if market == "TW" else "America/New_York"))
    return _date(stamp.date().isoformat())


def build_session_diagnostic(*, universe, history, official_prices,
                             before_carry, after_carry, previous_payload,
                             period: str, checked_at: datetime) -> dict:
    """Project only aggregate counts and validated dates, never raw rows."""
    before = {row.get("symbol"): row for row in before_carry if isinstance(row, dict)}
    after = {row.get("symbol"): row for row in after_carry if isinstance(row, dict)}
    previous = {row.get("symbol"): row for row in previous_payload.get("data", [])
                if isinstance(row, dict)} if isinstance(previous_payload, dict) else {}
    cohorts = {}
    official = {"TWSE": [], "TPEx": []}
    for cohort in COHORTS:
        items = [item for item in universe if _cohort(item) == cohort]
        if not items:
            continue
        symbols = [item.get("symbol") for item in items]
        stages = {"downloaded_daily": _summary([
            _history_date(history.get(symbol), cohort[:2]) for symbol in symbols
        ])}
        # Comparing raw index dates with actual priced sessions identifies
        # Yahoo's non-price batch tails without exporting any row or value.
        raw_tail_dates = []
        unpriced_tail_count = 0
        for symbol in symbols:
            frame = history.get(symbol)
            raw_date = None
            if frame is not None and not frame.empty:
                stamp = pd.Timestamp(frame.index[-1])
                if not pd.isna(stamp):
                    if stamp.tzinfo is not None:
                        stamp = stamp.tz_convert(ZoneInfo(
                            "Asia/Taipei" if cohort.startswith("TW_") else "America/New_York"
                        ))
                    raw_date = _date(stamp.date().isoformat())
                unpriced_tail_count += int("close" not in frame or pd.isna(frame["close"].iloc[-1]))
            raw_tail_dates.append(raw_date)
        stages["downloaded_raw_tail_index"] = _summary(raw_tail_dates)
        stages["downloaded_unpriced_tail_count"] = unpriced_tail_count
        for name, rows in (("features_before_carry", before),
                           ("features_after_carry", after), ("previous_formal", previous)):
            stages[name] = _summary([_date(rows.get(symbol, {}).get("official_session_date"))
                                    for symbol in symbols])
        stages["regressed_count_after_carry"] = sum(
            bool(current and prior and current < prior)
            for symbol in symbols
            for current, prior in [(_date(after.get(symbol, {}).get("official_session_date")),
                                    _date(previous.get(symbol, {}).get("official_session_date")))]
        )
        cohorts[cohort] = stages
        if cohort.startswith("TW_"):
            for symbol in symbols:
                if not isinstance(symbol, str):
                    continue
                exchange = "TWSE" if symbol.endswith(".TW") else "TPEx" if symbol.endswith(".TWO") else None
                if exchange:
                    snapshot = official_prices.get(symbol.split(".")[0], {})
                    official[exchange].append(_date(snapshot.get("date")) if isinstance(snapshot, dict) else None)
    return {
        "schema_version": 1,
        "status": "blocked_by_session_guard",
        "checked_at": checked_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "period": period if period in ("morning", "noon", "evening") else "unknown",
        "guard_relaxed": False,
        "formal_state_updated": False,
        "raw_prices_included": False,
        "cohorts": cohorts,
        "official_daily": {name: _summary(values) for name, values in official.items()},
        "official_daily_by_source": {
            source: _summary([_date(row.get("date")) for row in official_prices.values()
                              if isinstance(row, dict) and row.get("tw_price_source") == source])
            for source in ("TWSE OpenAPI", "TPEx OpenAPI", "TWSE Open Data")
        },
    }


def save_session_diagnostic_safely(directory: Path, **kwargs) -> bool:
    """Atomic run-local file; failure must not mask or bypass the original guard."""
    temporary = None
    try:
        payload = build_session_diagnostic(**kwargs)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                prefix=".session-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(directory / FILENAME)
        return True
    except Exception:  # noqa: BLE001 - diagnostics cannot change guard behavior
        logging.warning("Session diagnostic unavailable; original report guard remains blocking")
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
