"""Private, opt-in Basic-compatible SIP bars; never live signals or NBBO.

Current paid subscriptions cannot prove downgraded account entitlement. The
endpoint and explicit cutoff follow Alpaca's documented free historical rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests

ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
DELAY_SECONDS = 960
MAX_PAGES = 12
MAX_BYTES = 3 * 1024 * 1024
NY = ZoneInfo('America/New_York')


class DelayedDataUnavailable(Exception):
    pass


def _mark(telemetry, **values):
    if telemetry is not None:
        telemetry.update(values)


def _count(telemetry, field, amount=1):
    if telemetry is not None:
        telemetry[field] = telemetry.get(field, 0) + amount


def _diagnostic_failure(telemetry, exc):
    # Fixed allowlists only: do not serialize exception text, class names,
    # request URLs, headers, response bodies, market values or page tokens.
    if telemetry is None:
        return
    reasons = {'invalid_payload', 'invalid_bar', 'out_of_range_bar',
               'unexpected_symbol', 'invalid_bars', 'duplicate_bar',
               'invalid_pagination', 'pagination_budget', 'request_time_budget',
               'response_byte_budget'}
    reason, category = 'invalid_value', None
    if isinstance(exc, DelayedDataUnavailable):
        value = exc.args[0] if exc.args else None
        reason = value if value in reasons else 'provider_http_failure'
    elif isinstance(exc, requests.RequestException):
        reason = 'provider_network_failure'
        category = 'request_error'
        for kind, label in ((requests.exceptions.ConnectTimeout, 'connect_timeout'),
                            (requests.exceptions.ReadTimeout, 'read_timeout'),
                            (requests.exceptions.SSLError, 'tls_error'),
                            (requests.exceptions.ProxyError, 'proxy_error'),
                            (requests.exceptions.ChunkedEncodingError, 'chunked_encoding_error'),
                            (requests.exceptions.ContentDecodingError, 'content_decoding_error'),
                            (requests.exceptions.Timeout, 'timeout'),
                            (requests.exceptions.ConnectionError, 'connection_error')):
            if isinstance(exc, kind):
                category = label
                break
    elif telemetry.get('phase') == 'json':
        reason = 'invalid_json'
    _mark(telemetry, status='blocked', reason=reason)
    if category is not None:
        _mark(telemetry, network_category=category)


def _read(response: Any, deadline: float, telemetry=None) -> dict:
    body = bytearray()
    try:
        _mark(telemetry, phase='http', http_status=response.status_code)
        if response.status_code != 200:
            raise DelayedDataUnavailable(f'provider_http_{response.status_code}')
        _mark(telemetry, phase='body')
        for chunk in response.iter_content(chunk_size=1):
            if time.monotonic() > deadline:
                raise DelayedDataUnavailable('request_time_budget')
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise DelayedDataUnavailable('response_byte_budget')
        _mark(telemetry, phase='json')
        payload = json.loads(body)
        _mark(telemetry, phase='payload')
        if not isinstance(payload, dict) or not isinstance(payload.get('bars'), dict):
            raise DelayedDataUnavailable('invalid_payload')
        return payload
    finally:
        _count(telemetry, 'response_bytes', len(body))
        response.close()


def _bar(raw: Any, cutoff: datetime, interval: str, telemetry=None) -> dict:
    _mark(telemetry, phase='bar_shape')
    if not isinstance(raw, dict):
        raise DelayedDataUnavailable('invalid_bar')
    try:
        _mark(telemetry, phase='bar_timestamp')
        stamp = datetime.fromisoformat(str(raw['t']).replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp > cutoff:
            raise ValueError()
        if interval == '1Min' and stamp + timedelta(minutes=1) > cutoff:
            raise ValueError()
        if interval == '1Day' and stamp.astimezone(NY).time().isoformat() != '00:00:00':
            raise ValueError()
        _mark(telemetry, phase='bar_ohlcv')
        values = {}
        for key in ('o', 'h', 'l', 'c', 'v'):
            value = raw[key]
            if isinstance(value, bool):
                raise ValueError()
            value = float(value)
            if not math.isfinite(value) or (value < 0 if key == 'v' else value <= 0):
                raise ValueError()
            values[key] = value
        if values['l'] > min(values['o'], values['c']) or values['h'] < max(values['o'], values['c']):
            raise ValueError()
        return {'t': stamp.astimezone(timezone.utc).isoformat(), **values}
    except (KeyError, TypeError, ValueError, OverflowError):
        raise DelayedDataUnavailable('invalid_bar') from None


def _fetch(symbols, interval, start, end, client, headers, timeout, deadline, telemetry=None):
    _mark(telemetry, interval=interval, requested_count=len(symbols),
          phase='before_request', http_status=None, page_limit=MAX_PAGES, response_byte_limit=MAX_BYTES,
          validated_symbol_count=0)
    params = {'symbols': ','.join(symbols), 'timeframe': interval, 'feed': 'sip',
              'adjustment': 'split', 'currency': 'USD', 'sort': 'desc', 'limit': 10000,
              'start': start.isoformat(), 'end': end.isoformat()}
    result = {symbol: [] for symbol in symbols}
    tokens = set()
    stamps = {symbol: set() for symbol in symbols}
    for _ in range(MAX_PAGES):
        _mark(telemetry, phase='before_request', http_status=None)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DelayedDataUnavailable('request_time_budget')
        _count(telemetry, 'request_count')
        _mark(telemetry, phase='connect_or_headers', http_status=None)
        response = client.get(ENDPOINT, params=dict(params), headers=headers,
                              timeout=min(timeout, remaining), allow_redirects=False, stream=True)
        payload = _read(response, deadline, telemetry)
        if time.monotonic() > deadline:
            raise DelayedDataUnavailable('request_time_budget')
        _mark(telemetry, phase='symbols')
        if set(payload['bars']) - set(symbols):
            raise DelayedDataUnavailable('unexpected_symbol')
        for symbol, bars in payload['bars'].items():
            _mark(telemetry, phase='bar_list')
            if not isinstance(bars, list) or len(bars) > 10000:
                raise DelayedDataUnavailable('invalid_bars')
            for raw in bars:
                bar = _bar(raw, end + timedelta(minutes=1) if interval == '1Min' else end, interval, telemetry)
                stamp = datetime.fromisoformat(bar['t'])
                _mark(telemetry, phase='bar_window')
                if stamp < start or stamp > end:
                    raise DelayedDataUnavailable('out_of_range_bar')
                if interval == '1Day' and stamp.astimezone(NY).date() >= end.astimezone(NY).date():
                    continue  # Never pass a partial current-day bar as completed.
                _mark(telemetry, phase='duplicate_bar')
                if bar['t'] in stamps[symbol]:
                    raise DelayedDataUnavailable('duplicate_bar')
                stamps[symbol].add(bar['t'])
                result[symbol].append(bar)
                _count(telemetry, 'validated_bar_count')
                if len(result[symbol]) == 1:
                    _count(telemetry, 'validated_symbol_count')
        _count(telemetry, 'completed_page_count')
        _mark(telemetry, phase='pagination')
        token = payload.get('next_page_token')
        if token is None:
            return result
        if not isinstance(token, str) or not token or len(token) > 4096 or token in tokens:
            raise DelayedDataUnavailable('invalid_pagination')
        tokens.add(token)
        params['page_token'] = token
    raise DelayedDataUnavailable('pagination_budget')


def fetch_delayed_us_bars(symbols, timeout=20, session=None, observed_at=None, *, telemetry=None):
    from us_market_data import _credentials, _headers
    from us_daily_shadow_history import _symbols
    normalized = sorted(set(str(s).upper() for s in symbols if s))
    if not normalized:
        return {}
    requested = _symbols(normalized)
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError('timezone-aware observation required')
    credentials = _credentials()
    if not credentials:
        return {}
    cutoff = (observed.astimezone(timezone.utc) - timedelta(seconds=DELAY_SECONDS)).replace(second=0, microsecond=0)
    # End at a completed minute; avoid including the bar beginning at cutoff.
    end = cutoff - timedelta(minutes=1)
    client = session or requests.Session()
    headers = _headers(credentials)
    started = time.monotonic()
    deadline = started + 75
    _mark(telemetry, schema_version=1, status='running', stage='minute',
          minimum_delay_seconds=DELAY_SECONDS, total_budget_seconds=75,
          request_timeout_seconds=timeout, minute_window_minutes=90)
    output = {}
    failures = []

    def record_failure(exc):
        # Preserve each stage's safe receipt before the next request overwrites
        # current phase/HTTP fields. No exception strings or market data escape.
        state = dict(telemetry or {})
        _diagnostic_failure(state, exc)
        receipt = {key: state[key] for key in (
            'stage', 'phase', 'reason', 'network_category', 'http_status',
            'request_count', 'completed_page_count', 'validated_bar_count',
            'validated_symbol_count') if key in state}
        receipt['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        failures.append(receipt)

    try:
        try:
            minute = _fetch(requested, '1Min', end - timedelta(minutes=90), end,
                            client, headers, timeout, deadline, telemetry)
        except (DelayedDataUnavailable, requests.RequestException, ValueError, TypeError) as exc:
            record_failure(exc)
            minute = {}  # Failed pagination never supplies partially validated rows.
        _mark(telemetry, minute_available_count=sum(bool(rows) for rows in minute.values()))
        missing = [symbol for symbol in requested if not minute.get(symbol)]
        _mark(telemetry, stage='missing_daily' if missing else 'complete', missing_for_daily_count=len(missing))
        daily = {}
        if missing:
            try:
                # _fetch checks the same deadline before making any new request.
                daily = _fetch(missing, '1Day', end - timedelta(days=10), end,
                               client, headers, timeout, deadline, telemetry)
            except (DelayedDataUnavailable, requests.RequestException, ValueError, TypeError) as exc:
                record_failure(exc)
                # Retain successfully validated minute rows when only fallback fails.
                daily = {}
        _mark(telemetry, daily_available_count=sum(bool(rows) for rows in daily.values()), phase='output')
        for symbol in requested:
            bars = minute.get(symbol) or daily.get(symbol) or []
            if not bars:
                continue
            bars = sorted(bars, key=lambda row: row['t'])
            interval = '1Min' if minute.get(symbol) else '1Day'
            output[symbol] = {
                'us_delayed_data_available': True,
                'us_delayed_source': 'Alpaca SIP historical (>=16min delayed)',
                'us_delayed_price': bars[-1]['c'], 'us_delayed_asof': bars[-1]['t'],
                'us_delayed_fetched_at': observed.astimezone(timezone.utc).isoformat(),
                'us_delayed_minimum_seconds': DELAY_SECONDS,
                'us_delayed_interval': interval, 'us_delayed_coverage': 'consolidated',
                'us_delayed_bars': bars,
                'us_live_data_available': False,
            }
    except (DelayedDataUnavailable, requests.RequestException, ValueError, TypeError) as exc:
        record_failure(exc)
        output = {}
    coverage = 'complete' if len(output) == len(requested) else 'partial' if output else 'unavailable'
    status = ('recovered' if output else 'blocked') if failures else 'complete'
    _mark(telemetry, status=status, stage='complete', phase='output', failures=failures,
          coverage_status=coverage, elapsed_ms=round((time.monotonic() - started) * 1000),
          returned_count=len(output))
    return output


def fetch_free_us_quotes(symbols, timeout=20, session=None, observed_at=None, *, telemetry=None):
    """Private delayed SIP first, then explicitly labelled existing daily cache."""
    requested = sorted(set(str(symbol).upper() for symbol in symbols if symbol))
    output = fetch_delayed_us_bars(requested, timeout=timeout, session=session,
                                   observed_at=observed_at, **({'telemetry': telemetry} if telemetry is not None else {}))
    missing = sorted(set(requested) - set(output))
    if missing:
        from us_cached_daily_fallback import fetch_cached_us_daily
        output.update(fetch_cached_us_daily(missing, observed_at=observed_at))
    return output
