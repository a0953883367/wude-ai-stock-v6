"""Read-only, private display fallback from attested completed Yahoo report bars.

No provider requests, credentials, formal-model writes, or real-time coverage
claims. Report load time is distinct from the historical bar's session time.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
MAX_BYTES = 8 * 1024 * 1024
DELAY_SECONDS = 960
_SYMBOL = re.compile(r'[A-Z][A-Z0-9.-]{0,19}\Z')


def _symbol(value):
    return isinstance(value, str) and _SYMBOL.fullmatch(value) is not None


def _bar(row, observed):
    if row.get('market') != 'US' or row.get('us_daily_source') != 'Yahoo Finance daily bars':
        return None
    if row.get('us_daily_price_available') is not True or row.get('source_daily_ohlcv_complete') is not True:
        return None
    day = row.get('us_daily_session_date')
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        return None
    if row.get('source_daily_ohlcv_session_date') != day or row.get('official_session_date') != day:
        return None
    try:
        session = date.fromisoformat(day)
        today = observed.astimezone(NY).date()
        if not 1 <= (today - session).days <= 7:
            return None
        # Wait until the entire NY calendar day has ended, plus the safety delay.
        end = datetime.combine(session + timedelta(days=1), time(), NY)
        if end.astimezone(timezone.utc) + timedelta(seconds=DELAY_SECONDS) > observed:
            return None
        values = {}
        for key, field in (('o', 'official_open_price'), ('h', 'official_high_price'),
                           ('l', 'official_low_price'), ('c', 'official_close_price'),
                           ('v', 'official_volume')):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            value = float(value)
            if not math.isfinite(value) or (value < 0 if key == 'v' else value <= 0):
                return None
            values[key] = value
        if values['l'] > min(values['o'], values['c']) or values['h'] < max(values['o'], values['c']):
            return None
        stamp = datetime.combine(session, time(), NY).astimezone(timezone.utc).isoformat()
        return {'t': stamp, **values}
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_cached_us_daily(symbols, observed_at=None, report_path=None):
    """Return only requested, unique, fully attested US rows; fail closed on IO.

    A daily timestamp identifies the session start, not a quote/update time.
    Cache age is bounded to seven NY calendar dates. Incomplete, duplicate,
    future, and unrequested rows never substitute for missing observations.
    """
    observed = observed_at if observed_at is not None else datetime.now(timezone.utc)
    if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
        return {}
    if not isinstance(symbols, (list, tuple, set, frozenset)):
        return {}
    requested = {s for s in symbols if _symbol(s)}
    if not requested:
        return {}
    try:
        if report_path is None:
            from config import SETTINGS
            report_path = Path(SETTINGS.reports_dir) / 'all_analysis.json'
        with Path(report_path).open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {}
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError, ImportError, AttributeError, RecursionError):
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        return {}
    rows = [r for r in payload['data'] if isinstance(r, dict) and _symbol(r.get('symbol'))]
    counts = Counter(r['symbol'] for r in rows)
    result = {}
    for row in rows:
        symbol = row['symbol']
        if symbol not in requested or counts[symbol] != 1:
            continue
        bar = _bar(row, observed)
        if bar is None:
            continue
        result[symbol] = {
            'us_delayed_data_available': True,
            'us_delayed_source': 'Yahoo Finance completed daily report cache',
            'us_delayed_price': bar['c'], 'us_delayed_asof': bar['t'],
            'us_delayed_fetched_at': observed.astimezone(timezone.utc).isoformat(),
            'us_delayed_minimum_seconds': DELAY_SECONDS,
            'us_delayed_interval': '1Day', 'us_delayed_coverage': 'provider_defined',
            'us_delayed_bars': [bar], 'us_live_data_available': False,
            'us_delayed_cached': True,
        }
    return result
