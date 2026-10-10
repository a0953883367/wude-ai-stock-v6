"""Bounded, in-memory Alpaca SIP daily research; no formal-model or raw exports.

Existing Railway credentials only. All price/volume/indicator values die with
this request. The caller receives operational counts, never market data rows.
Historical retrieval is not point-in-time evidence of earlier availability.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import math
import json
from hashlib import sha256
import re
import time as clock
from typing import Any
from zoneinfo import ZoneInfo

import requests

from us_market_data import _credentials, _headers

ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
VERSION = 'US-SIP-DAILY-IN-MEMORY-V2'
NY = ZoneInfo('America/New_York')
MAX_SYMBOLS = 200
MAX_DAYS = 400
MAX_PAGES = 12
MAX_SECONDS = 75
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
MIN_BARS = 60


class HistoryBlocked(Exception):
    def __init__(self, reason: str, http_status: int | None = None):
        super().__init__(reason)
        self.reason, self.http_status = reason, http_status


def _symbols(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SYMBOLS:
        raise ValueError('daily shadow requires 1 to 200 symbols')
    if any(not isinstance(s, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', s) for s in value):
        raise ValueError('invalid daily shadow symbol')
    if len(set(value)) != len(value):
        raise ValueError('duplicate daily shadow symbol')
    return sorted(value)


def _sessions(calendar: Any, now: datetime, *, verified_weeks: dict | None = None) -> list[str]:
    """Require verified cached sessions; exclude the current New York date.

    This intentionally adds one local-day settlement buffer. It does not claim
    provider bars are immutable or unaffected by later corrections.
    """
    today = now.astimezone(NY).date()
    start = today - timedelta(days=MAX_DAYS)
    sessions = set()
    first_week_start = start - timedelta(days=start.weekday())
    for year in range(start.year, today.year + 1):
        try:
            row = calendar.relay_us_year(year)
        except (AttributeError, ValueError, RuntimeError):
            # Only current-year history is enough when earlier cache is absent.
            if year == today.year:
                raise HistoryBlocked('official_calendar_unavailable')
            continue
        if (row.get('year') != year or row.get('status') != 'verified_alpaca'
                or row.get('sources') != ['Alpaca Market Calendar']):
            raise HistoryBlocked('official_calendar_unverified')
        for value in row.get('sessions') or []:
            try:
                day = date.fromisoformat(value)
                detail = (row.get('session_details') or {}).get(value) or {}
                opening, closing = time.fromisoformat(detail['open']), time.fromisoformat(detail['close'])
            except (TypeError, ValueError, KeyError):
                raise HistoryBlocked('official_calendar_invalid')
            if day.year != year or opening >= closing:
                raise HistoryBlocked('official_calendar_invalid')
            if verified_weeks is not None and first_week_start <= day <= today + timedelta(days=7):
                monday = day - timedelta(days=day.weekday())
                # A cross-year week needs both authoritative year exports;
                # conservatively leave it uncertified in this bounded stage.
                if monday.year == (monday + timedelta(days=6)).year:
                    verified_weeks.setdefault(monday.isoformat(), []).append(value)
            if start <= day < today:
                sessions.add(value)
    if len(sessions) < 4:
        raise HistoryBlocked('official_calendar_insufficient')
    return sorted(sessions)


def _number(value: Any, *, volume: bool = False) -> float:
    if isinstance(value, bool):
        raise HistoryBlocked('invalid_ohlcv')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise HistoryBlocked('invalid_ohlcv') from None
    if not math.isfinite(number) or (number < 0 if volume else number <= 0):
        raise HistoryBlocked('invalid_ohlcv')
    # Split-adjusted share volumes may be fractional. Never round or fill.
    return number


def _normalize(raw: Any, expected: set[str]) -> tuple[str, tuple[float, ...]]:
    if not isinstance(raw, dict):
        raise HistoryBlocked('invalid_bar')
    try:
        stamp = datetime.fromisoformat(str(raw.get('t') or '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        local = stamp.astimezone(NY)
        day = local.date().isoformat()
        if local.time() != time(0) or day not in expected:
            raise ValueError()
    except (TypeError, ValueError):
        raise HistoryBlocked('invalid_bar_session') from None
    o, h, l, c, v = (_number(raw.get(key), volume=key == 'v') for key in ('o', 'h', 'l', 'c', 'v'))
    if l > min(o, c) or h < max(o, c) or l > h:
        raise HistoryBlocked('invalid_ohlcv')
    return day, (o, h, l, c, v)


def _fetch(symbols: list[str], sessions: list[str], client: Any, credentials: tuple[str, str],
           deadline: float, telemetry: dict[str, Any]) -> dict[str, dict[str, tuple[float, ...]]]:
    params = {'symbols': ','.join(symbols), 'timeframe': '1Day', 'feed': 'sip',
              'adjustment': 'split', 'currency': 'USD', 'sort': 'asc', 'limit': 10000,
              'asof': sessions[-1], 'start': sessions[0], 'end': sessions[-1] + 'T23:59:59-04:00'}
    # Use the actual New York offset for the final day, including DST changes.
    params['end'] = datetime.combine(date.fromisoformat(sessions[-1]), time(23, 59, 59), NY).isoformat()
    output = {symbol: {} for symbol in symbols}
    seen_tokens = set()
    for _ in range(MAX_PAGES):
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise HistoryBlocked('request_time_budget')
        telemetry['request_count'] += 1
        try:
            response = client.get(ENDPOINT, params=dict(params), headers={**_headers(credentials), "Accept-Encoding": "identity"},
                                  timeout=(min(3, remaining / 2), min(7, remaining / 2)), allow_redirects=False, stream=True)
        except requests.RequestException:
            raise HistoryBlocked('provider_network_failure') from None
        if clock.monotonic() > deadline:
            response.close()
            raise HistoryBlocked('request_time_budget')
        status = response.status_code
        if status != 200:
            response.close()
            reason = {401: 'provider_authentication_denied', 403: 'provider_entitlement_denied',
                      429: 'provider_rate_limited'}.get(status, 'provider_http_failure')
            raise HistoryBlocked(reason, status)
        # Never include provider response messages or request headers in errors.
        for name in ('X-RateLimit-Limit', 'X-RateLimit-Remaining'):
            value = str(response.headers.get(name, ''))
            if re.fullmatch(r'\d{1,9}', value):
                telemetry[name.lower().replace('-', '_')] = int(value)
        # One-byte streaming ensures even a drip-fed response reaches the
        # deadline check between arrived bytes, rather than waiting to fill a
        # large chunk. A stalled read is bounded by the <=7s socket timeout.
        body = bytearray()
        try:
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise HistoryBlocked('unexpected_content_encoding')
            for chunk in response.iter_content(chunk_size=1):
                if clock.monotonic() > deadline:
                    raise HistoryBlocked('request_time_budget')
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise HistoryBlocked('response_byte_budget')
            payload = json.loads(body)
            if clock.monotonic() > deadline:
                raise HistoryBlocked('request_time_budget')
        except requests.RequestException:
            raise HistoryBlocked('provider_network_failure') from None
        except (ValueError, TypeError):
            raise HistoryBlocked('invalid_provider_json') from None
        finally:
            response.close()
        if not isinstance(payload, dict) or not isinstance(payload.get('bars'), dict):
            raise HistoryBlocked('invalid_provider_payload')
        bars = payload['bars']
        if any(not isinstance(records, list) for records in bars.values()) or sum(len(records) for records in bars.values()) > 10000:
            raise HistoryBlocked('invalid_provider_payload')
        if set(bars) - set(symbols):
            raise HistoryBlocked('unexpected_provider_symbol')
        for symbol, records in bars.items():
            if not isinstance(records, list) or len(records) > 10000:
                raise HistoryBlocked('invalid_provider_payload')
            for raw in records:
                if clock.monotonic() > deadline:
                    raise HistoryBlocked('request_time_budget')
                day, values = _normalize(raw, set(sessions))
                if day in output[symbol]:
                    raise HistoryBlocked('duplicate_provider_session')
                output[symbol][day] = values
        token = payload.get('next_page_token')
        if token is None:
            return output
        if not isinstance(token, str) or not token or len(token) > 4096 or token in seen_tokens:
            raise HistoryBlocked('invalid_pagination')
        seen_tokens.add(token)
        params['page_token'] = token
    raise HistoryBlocked('pagination_budget_exhausted')


def collect_daily_shadow_status(symbols: Any, calendar: Any, *, now: datetime | None = None,
                                session: Any = None, collect_history: bool = False, instrument_types: Any = None) -> dict[str, Any]:
    started = clock.monotonic()
    requested = _symbols(symbols)
    categories = {symbol: 'unknown' for symbol in requested} if instrument_types is None else instrument_types
    if (not isinstance(categories, dict) or set(categories) != set(requested)
            or any(not isinstance(value, str) or value not in {'stock', 'etf', 'unknown'} for value in categories.values())):
        raise ValueError('invalid frozen universe categories')
    manifest_hash = sha256(json.dumps([[symbol, categories[symbol]] for symbol in requested],
                                     separators=(',', ':')).encode()).hexdigest()
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError('timezone-aware observation required')
    result = {'schema_version': 1, 'version': VERSION, 'status': 'blocked',
              'observed_at': observed.astimezone(timezone.utc).isoformat(),
              'source': 'Alpaca SIP historical daily bars', 'feed': 'sip', 'interval': '1Day',
              'adjustment': 'split', 'requested_count': len(requested), 'probe_status': 'not_run',
              'stock_count': sum(v == 'stock' for v in categories.values()),
              'etf_count': sum(v == 'etf' for v in categories.values()),
              'unclassified_count': sum(v == 'unknown' for v in categories.values()),
              'universe_manifest_sha256': manifest_hash,
              'history_complete_count': 0, 'indicator_complete_count': 0,
              'weekly_indicator_complete_count': 0, 'daily_momentum_complete_count': 0, 'research_plan_complete_count': 0,
              'decision_eligible': False, 'affects_formal': False, 'prospective_evaluation_started': False,
              'durable_raw_retention': False, 'market_values_exported': False,
              'bar_finality_verified': False, 'corporate_actions_independently_verified': False, 'request_count': 0}
    client = None
    try:
        credentials = _credentials()
        if not credentials:
            raise HistoryBlocked('existing_credentials_unavailable')
        verified_weeks = {}
        sessions = _sessions(calendar, observed, verified_weeks=verified_weeks)
        result['session_date'] = sessions[-1]
        result['calendar_session_count'] = len(sessions)
        deadline = clock.monotonic() + MAX_SECONDS
        client = session or requests.Session()
        probe_sessions = sessions[-4:]
        probe = _fetch(['AAPL'], probe_sessions, client, credentials, deadline, result)
        if set(probe['AAPL']) != set(probe_sessions):
            raise HistoryBlocked('probe_incomplete')
        result['observed_at'] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        result['probe_status'] = 'passed'
        result['probe_session_count'] = 4
        if not collect_history:
            result['status'] = 'probe_verified'
            result['next_stage'] = 'bounded_history_collection_not_started'
            return result
        histories = _fetch(requested, sessions, client, credentials, deadline, result)
        from us_private_shadow_projection import project_symbol
        captured_at = now or datetime.now(timezone.utc)
        result['observed_at'] = captured_at.astimezone(timezone.utc).isoformat()
        reasons = Counter()
        quality_diagnostics = []
        coverage_reasons = Counter()
        for symbol in requested:
            if clock.monotonic() > deadline:
                raise HistoryBlocked('request_time_budget')
            projection = project_symbol(symbol, histories[symbol], sessions,
                                        observed=captured_at, adjustment='split', verified_weeks=verified_weeks, instrument_category=categories[symbol])
            result['history_complete_count'] += int(projection.full_window_complete)
            if projection.features is None:
                reason = projection.reasons[0]
                reasons[reason] += 1
                label = {'adjusted_price_discontinuity_requires_review': 'source_continuity_review',
                         'insufficient_contiguous_indicator_history': 'history_window_insufficient'}.get(reason)
                if label:
                    quality_diagnostics.append({'symbol': symbol, 'reason': label,
                                                'sessions': list(projection.quality_sessions)})
                continue
            result['indicator_complete_count'] += 1
            result['daily_momentum_complete_count'] += int(
                projection.features.daily_k9 is not None and projection.features.daily_d9 is not None)
            result['weekly_indicator_complete_count'] += int(
                projection.features.weekly_k9 is not None and projection.features.weekly_d9 is not None)
            result['research_plan_complete_count'] += int(projection.plan is not None)
            for reason in projection.reasons:
                coverage_reasons[reason] += 1
            # This object contains private market values and remains local only.
            # No projection, dataclass serialization or hash is put in a report.
        if clock.monotonic() > deadline:
            raise HistoryBlocked('request_time_budget')
        result['coverage_notes'] = dict(coverage_reasons)
        result['blocked_reasons'] = dict(reasons)
        result['quality_diagnostics'] = quality_diagnostics
        result['status'] = 'computed_in_memory' if result['indicator_complete_count'] == len(requested) else 'partial_in_memory'
        result['next_stage'] = 'private_retention_rights_and_prospective_observation_required'
        result['projection_version'] = 'US-PRIVATE-SIP-PROJECTION-V1'
        result['corporate_action_basis'] = 'provider_split_adjusted_as_observed_now'
    except HistoryBlocked as exc:
        result['reason'] = exc.reason
        if exc.http_status is not None:
            result['provider_http_status'] = exc.http_status
        if result['probe_status'] == 'not_run':
            result['probe_status'] = 'blocked'
    finally:
        result['elapsed_ms'] = max(0, round((clock.monotonic() - started) * 1000))
        if session is None and client is not None:
            client.close()
    return result
