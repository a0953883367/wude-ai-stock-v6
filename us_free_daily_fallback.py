"""Opt-in private completed-session fallback; no live or formal-model wiring.

Uses the existing authenticated SIP historical reader, never snapshot/latest,
and returns an isolated schema that cannot impersonate live SIP feature rows.
No credentials, history, or market rows are persisted by this module.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import time as clock
from typing import Any

from us_daily_shadow_history import (
    HistoryBlocked, MAX_SECONDS, NY, _fetch, _sessions, _symbols,
)
from us_market_data import _credentials

DELAY = timedelta(minutes=16)  # one-minute buffer beyond the Basic restriction


def eligible_sessions(calendar: Any, observed: datetime) -> list[str]:
    if observed.tzinfo is None:
        raise ValueError('timezone-aware observation required')
    cutoff = observed.astimezone(timezone.utc) - DELAY
    return [day for day in _sessions(calendar, observed)
            if datetime.combine(date.fromisoformat(day), time(23, 59, 59), NY) <= cutoff]


def fetch_free_completed_daily(
    symbols: list[str], calendar: Any, *, now: datetime | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Return completed-session research closes, not intraday delayed quotes.

    Account downgrade is neither performed nor inferred. A successful request
    with paid credentials proves endpoint coverage only, not Basic entitlement.
    Missing latest-session rows remain unavailable; no stale close substitution.
    """
    requested = _symbols(symbols)
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError('timezone-aware observation required')
    result: dict[str, Any] = {
        'schema_version': 1, 'status': 'blocked', 'mode': 'completed_session_only',
        'source': 'Alpaca SIP historical daily bars', 'feed': 'sip',
        'observed_at': observed.astimezone(timezone.utc).isoformat(),
        'minimum_delay_seconds': int(DELAY.total_seconds()),
        'requested_count': len(requested), 'available_count': 0,
        'missing_symbols': list(requested), 'rows': {}, 'request_count': 0,
        'affects_formal': False, 'realtime': False,
        'basic_entitlement_verified': False,
    }
    credentials = _credentials()
    if not credentials:
        return {**result, 'reason': 'existing_credentials_unavailable'}
    try:
        sessions = eligible_sessions(calendar, observed)
        if len(sessions) < 2:
            raise HistoryBlocked('completed_sessions_unavailable')
        # Only two completed sessions are needed. Keep 188 symbols in one batch;
        # inherited reader bounds pagination, bytes, HTTP time, and credentials.
        selected = sessions[-2:]
        if session is None:
            import requests
            session = requests.Session()
        history = _fetch(requested, selected, session, credentials,
                         clock.monotonic() + MAX_SECONDS, result,
                         connect_timeout=5, read_timeout=20)
        rows = {}
        for symbol in requested:
            bars = history.get(symbol, {})
            latest = bars.get(selected[-1])
            if latest is None:
                continue
            previous = bars.get(selected[-2])
            rows[symbol] = {
                'symbol': symbol, 'session_date': selected[-1],
                'close': latest[3], 'volume': latest[4],
                'previous_close': previous[3] if previous else None,
                'source': result['source'], 'feed': 'sip', 'interval': '1Day',
                'adjustment': 'split', 'currency': 'USD',
                'realtime': False, 'scope': 'private_research_only',
            }
        missing = sorted(set(requested) - set(rows))
        result.update(status='complete' if not missing else 'partial',
                      session_date=selected[-1], rows=rows, missing_symbols=missing,
                      available_count=len(rows))
    except HistoryBlocked as exc:
        result.update(reason=exc.reason)
        if exc.http_status is not None:
            result['http_status'] = exc.http_status
    return result
