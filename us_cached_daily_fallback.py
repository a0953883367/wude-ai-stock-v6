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


SIP_DAILY_SOURCE = 'Alpaca SIP completed daily bars'
SIP_CACHE_SOURCE = 'Alpaca SIP completed daily report cache'


def _aware_stamp(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _sip_close_reference(row, published, observed, publication_text):
    """Validate an attested close reference without constructing any bars."""
    if row.get('market') != 'US' or row.get('us_live_data_available') is not False:
        return None
    if row.get('us_delayed_data_available') is not True or row.get('us_delayed_interval') != '1Day':
        return None
    if row.get('us_delayed_calendar_verified') is not True or row.get('us_delayed_session_completed') is not True:
        return None
    if row.get('us_delayed_calendar_source') != 'Alpaca Market Calendar':
        return None
    if row.get('us_delayed_coverage') != 'consolidated':
        return None
    minimum = row.get('us_delayed_minimum_seconds')
    if type(minimum) not in (int, float) or minimum != DELAY_SECONDS:
        return None
    source = row.get('us_delayed_source')
    if source == SIP_DAILY_SOURCE:
        if row.get('us_delayed_cached') is True:
            return None
        original_publication = published
        original_publication_text = publication_text
    elif source == SIP_CACHE_SOURCE:
        if row.get('us_delayed_cached') is not True or row.get('us_delayed_origin_source') != SIP_DAILY_SOURCE:
            return None
        original_publication_text = row.get('us_delayed_origin_published_at')
        original_publication = _aware_stamp(original_publication_text)
        if original_publication is None:
            return None
    else:
        return None
    day = row.get('us_delayed_session_date')
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        return None
    try:
        session = date.fromisoformat(day)
        age = (observed.astimezone(NY).date() - session).days
        if not 1 <= age <= 7:
            return None
        end = datetime.combine(session + timedelta(days=1), time(), NY)
        eligible_at = end.astimezone(timezone.utc) + timedelta(seconds=DELAY_SECONDS - 1)
        if observed < eligible_at:
            return None
        price = row.get('us_delayed_price')
        if isinstance(price, bool) or not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
            return None
        asof = _aware_stamp(row.get('us_delayed_asof'))
        close = _aware_stamp(row.get('us_delayed_session_close'))
        fetched = _aware_stamp(row.get('us_delayed_fetched_at'))
        if asof is None or close is None or fetched is None or asof != close:
            return None
        local_close = close.astimezone(NY)
        if local_close.date() != session or not time(9, 30) < local_close.time() <= time(16):
            return None
        if fetched < eligible_at:
            return None
        if not close + timedelta(seconds=DELAY_SECONDS) <= fetched <= original_publication <= published <= observed:
            return None
        # Preserve the original timestamps exactly, never replacing them with
        # cache retrieval or later republication time.
        return {
            'us_delayed_data_available': True,
            'us_delayed_source': SIP_CACHE_SOURCE,
            'us_delayed_origin_source': SIP_DAILY_SOURCE,
            'us_delayed_price': price,
            'us_delayed_asof': row['us_delayed_asof'],
            'us_delayed_fetched_at': row['us_delayed_fetched_at'],
            'us_delayed_session_date': day,
            'us_delayed_session_close': row['us_delayed_session_close'],
            'us_delayed_calendar_verified': True,
            'us_delayed_calendar_source': 'Alpaca Market Calendar',
            'us_delayed_session_completed': True,
            'us_delayed_interval': '1Day',
            'us_delayed_coverage': 'consolidated',
            'us_delayed_minimum_seconds': DELAY_SECONDS,
            'us_live_data_available': False,
            'us_delayed_cached': True,
            'us_delayed_origin_published_at': original_publication_text,
            'us_delayed_cache_read_at': observed.astimezone(timezone.utc).isoformat(),
        }
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_cached_us_daily_references(symbols, observed_at=None, report_path=None):
    """Read bounded completed SIP close references for report display only.

    Does not return bars, mutate formal prices, or promote Yahoo provenance.
    Calendar attestation must originate in the completed-session adapter; old
    reports lacking that attestation remain unavailable. No network is used.
    """
    observed = observed_at if observed_at is not None else datetime.now(timezone.utc)
    if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
        return {}
    if not isinstance(symbols, (list, tuple, set, frozenset)):
        return {}
    requested = {symbol for symbol in symbols if _symbol(symbol)}
    if not requested:
        return {}
    try:
        if report_path is None:
            from config import SETTINGS
            report_path = Path(SETTINGS.reports_dir) / 'all_analysis.json'
        report_path = Path(report_path)
    except (ValueError, TypeError, ImportError, AttributeError):
        return {}
    root = report_path.parent
    paths = [(report_path, 'latest_analysis', 'data'),
             (root / 'evening.json', 'current_evening', 'watchlist'),
             (root / 'morning.json', 'current_morning', 'watchlist')]
    today = observed.astimezone(ZoneInfo('Asia/Taipei')).date()
    for age in range(8):
        day = (today - timedelta(days=age)).isoformat()
        for period in ('evening', 'noon', 'morning'):
            paths.append((root / 'archive' / f'{day}-{period}.json', 'archived_' + period, 'watchlist'))
    remaining = MAX_TOTAL_BYTES
    candidates, conflicts, seen = {}, set(), set()
    for path, kind, key in paths[:MAX_FILES]:
        if remaining <= 0:
            break
        if path in seen:
            continue
        seen.add(path)
        try:
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
        published = _aware_stamp(payload.get('us_daily_reference_published_at'))
        if published is not None and published > observed:
            published = None
        if published is None:
            continue
        rows = [r for r in payload[key] if isinstance(r, dict) and _symbol(r.get('symbol'))]
        counts = Counter(r['symbol'] for r in rows)
        for row in rows:
            symbol = row['symbol']
            if symbol not in requested or counts[symbol] != 1:
                continue
            reference = _sip_close_reference(row, published, observed, payload['us_daily_reference_published_at'])
            if reference is None:
                continue
            identity = (symbol, reference['us_delayed_session_date'])
            reference['us_delayed_cache_source_kind'] = kind
            previous = candidates.get(identity)
            if previous is not None and (
                previous['us_delayed_price'] != reference['us_delayed_price'] or
                _aware_stamp(previous['us_delayed_session_close']) != _aware_stamp(reference['us_delayed_session_close'])
            ):
                conflicts.add(identity)
            else:
                candidates.setdefault(identity, reference)
    result = {}
    for (symbol, day), reference in candidates.items():
        if (symbol, day) in conflicts:
            continue
        if symbol not in result or day > result[symbol]['us_delayed_session_date']:
            result[symbol] = reference
    return result
