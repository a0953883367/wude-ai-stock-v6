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
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 27
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


def _publication_time(payload, observed):
    """Repository naive publication timestamps are Taiwan local wall time."""
    value = payload.get('updated_at')
    if not isinstance(value, str):
        return None
    try:
        # Require a timestamp, not date-only ISO input accepted by fromisoformat.
        if not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', value):
            return None
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo('Asia/Taipei'))
        stamp = stamp.astimezone(timezone.utc)
        return stamp if stamp <= observed else None
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_cached_us_daily(symbols, observed_at=None, report_path=None):
    """Return only requested, unique, fully attested US rows; fail closed on IO.

    A daily timestamp identifies the session start, not a quote/update time.
    Cache age is bounded to seven NY calendar dates. Existing current and
    recent archived reports can preserve a prior completed session when the
    latest report has already advanced to an incomplete day. Incomplete, duplicate,
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
        report_path = Path(report_path)
    except (ValueError, TypeError, ImportError, AttributeError):
        return {}
    # Fixed candidate names only: no directory scan, network or decompression.
    # Reports are named by Taiwan publication date, potentially one day ahead
    # of the NY session date. Session validity is still checked solely in NY.
    root = report_path.parent
    paths = [(report_path, 'latest_analysis', 'data'),
             (root / 'evening.json', 'current_evening', 'watchlist'),
             (root / 'morning.json', 'current_morning', 'watchlist')]
    publication_day = observed.astimezone(ZoneInfo('Asia/Taipei')).date()
    for age in range(8):
        day = publication_day - timedelta(days=age)
        for period in ('evening', 'noon', 'morning'):
            paths.append((root / 'archive' / f'{day.isoformat()}-{period}.json',
                          'archived_' + period, 'watchlist'))
    remaining = MAX_TOTAL_BYTES
    candidates = {}
    conflicts = set()
    seen_paths = set()
    for path, kind, key in paths[:MAX_FILES]:
        if remaining <= 0:
            break
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            # Refuse symlink sources so archive lookup stays in local reports.
            if path.is_symlink() or path.parent.is_symlink():
                continue
            limit = min(MAX_BYTES + 1, remaining)
            with path.open('rb') as handle:
                raw = handle.read(limit)
            remaining -= len(raw)
            if len(raw) >= limit:
                continue
            payload = json.loads(raw)
        except (OSError, ValueError, TypeError, RecursionError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            continue
        published = _publication_time(payload, observed)
        if published is None:
            continue
        rows = [r for r in payload[key] if isinstance(r, dict) and _symbol(r.get('symbol'))]
        counts = Counter(r['symbol'] for r in rows)
        for row in rows:
            symbol = row['symbol']
            if symbol not in requested or counts[symbol] != 1:
                continue
            bar = _bar(row, observed)
            if bar is None:
                continue
            # A partial intraday snapshot must never age into a completed bar.
            # Conservatively require publication after regular NY close +16min;
            # early-close sessions remain safe under this later cutoff.
            session = date.fromisoformat(row['us_daily_session_date'])
            close = datetime.combine(session, time(16), NY).astimezone(timezone.utc)
            if published < close + timedelta(seconds=DELAY_SECONDS):
                continue
            identity = (symbol, bar['t'])
            if identity in candidates and candidates[identity][0] != bar:
                conflicts.add(identity)
            else:
                candidates.setdefault(identity, (bar, kind))
    selected = {}
    for (symbol, stamp), (bar, kind) in candidates.items():
        if (symbol, stamp) in conflicts:
            continue
        if symbol not in selected or stamp > selected[symbol][0]['t']:
            selected[symbol] = (bar, kind)
    result = {}
    for symbol, (bar, kind) in selected.items():
        result[symbol] = {
            'us_delayed_data_available': True,
            'us_delayed_source': 'Yahoo Finance completed daily report cache',
            'us_delayed_price': bar['c'], 'us_delayed_asof': bar['t'],
            'us_delayed_fetched_at': observed.astimezone(timezone.utc).isoformat(),
            'us_delayed_minimum_seconds': DELAY_SECONDS,
            'us_delayed_interval': '1Day', 'us_delayed_coverage': 'provider_defined',
            'us_delayed_bars': [bar], 'us_live_data_available': False,
            'us_delayed_cached': True,
            'us_delayed_cache_source_kind': kind,
        }
    return result
