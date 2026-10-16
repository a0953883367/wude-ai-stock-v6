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


def _read(response: Any, deadline: float) -> dict:
    try:
        if response.status_code != 200:
            raise DelayedDataUnavailable(f'provider_http_{response.status_code}')
        body = bytearray()
        for chunk in response.iter_content(chunk_size=1):
            if time.monotonic() > deadline:
                raise DelayedDataUnavailable('request_time_budget')
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise DelayedDataUnavailable('response_byte_budget')
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get('bars'), dict):
            raise DelayedDataUnavailable('invalid_payload')
        return payload
    finally:
        response.close()


def _bar(raw: Any, cutoff: datetime, interval: str) -> dict:
    if not isinstance(raw, dict):
        raise DelayedDataUnavailable('invalid_bar')
    try:
        stamp = datetime.fromisoformat(str(raw['t']).replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp > cutoff:
            raise ValueError()
        if interval == '1Min' and stamp + timedelta(minutes=1) > cutoff:
            raise ValueError()
        if interval == '1Day' and stamp.astimezone(NY).time().isoformat() != '00:00:00':
            raise ValueError()
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


def _fetch(symbols, interval, start, end, client, headers, timeout, deadline):
    params = {'symbols': ','.join(symbols), 'timeframe': interval, 'feed': 'sip',
              'adjustment': 'split', 'currency': 'USD', 'sort': 'desc', 'limit': 10000,
              'start': start.isoformat(), 'end': end.isoformat()}
    result = {symbol: [] for symbol in symbols}
    tokens = set()
    stamps = {symbol: set() for symbol in symbols}
    for _ in range(MAX_PAGES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DelayedDataUnavailable('request_time_budget')
        response = client.get(ENDPOINT, params=dict(params), headers=headers,
                              timeout=min(timeout, remaining), allow_redirects=False, stream=True)
        payload = _read(response, deadline)
        if time.monotonic() > deadline:
            raise DelayedDataUnavailable('request_time_budget')
        if set(payload['bars']) - set(symbols):
            raise DelayedDataUnavailable('unexpected_symbol')
        for symbol, bars in payload['bars'].items():
            if not isinstance(bars, list) or len(bars) > 10000:
                raise DelayedDataUnavailable('invalid_bars')
            for raw in bars:
                bar = _bar(raw, end + timedelta(minutes=1) if interval == '1Min' else end, interval)
                stamp = datetime.fromisoformat(bar['t'])
                if stamp < start or stamp > end:
                    raise DelayedDataUnavailable('out_of_range_bar')
                if interval == '1Day' and stamp.astimezone(NY).date() >= end.astimezone(NY).date():
                    continue  # Never pass a partial current-day bar as completed.
                if bar['t'] in stamps[symbol]:
                    raise DelayedDataUnavailable('duplicate_bar')
                stamps[symbol].add(bar['t'])
                result[symbol].append(bar)
        token = payload.get('next_page_token')
        if token is None:
            return result
        if not isinstance(token, str) or not token or len(token) > 4096 or token in tokens:
            raise DelayedDataUnavailable('invalid_pagination')
        tokens.add(token)
        params['page_token'] = token
    raise DelayedDataUnavailable('pagination_budget')


def fetch_delayed_us_bars(symbols, timeout=20, session=None, observed_at=None):
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
    deadline = time.monotonic() + 75
    output = {}
    try:
        try:
            minute = _fetch(requested, '1Min', end - timedelta(minutes=90), end,
                            client, headers, timeout, deadline)
        except (DelayedDataUnavailable, requests.RequestException, ValueError, TypeError):
            # Discard the entire failed minute retrieval, including partial pages.
            # A separately validated completed daily bar remains a valid fallback.
            minute = {}
        missing = [symbol for symbol in requested if not minute.get(symbol)]
        daily = {}
        if missing and time.monotonic() < deadline:
            try:
                daily = _fetch(missing, '1Day', end - timedelta(days=10), end,
                               client, headers, timeout, deadline)
            except (DelayedDataUnavailable, requests.RequestException, ValueError, TypeError):
                # Keep already validated minute data if only daily fallback fails.
                daily = {}
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
    except (DelayedDataUnavailable, requests.RequestException, ValueError, TypeError):
        return {}  # Fail closed; existing Yahoo/report path remains available.
    return output


def fetch_free_us_quotes(symbols, timeout=20, session=None, observed_at=None):
    """Private delayed SIP first, then explicitly labelled existing daily cache."""
    requested = sorted(set(str(symbol).upper() for symbol in symbols if symbol))
    output = fetch_delayed_us_bars(requested, timeout=timeout, session=session,
                                   observed_at=observed_at)
    missing = sorted(set(requested) - set(output))
    if missing:
        from us_cached_daily_fallback import fetch_cached_us_daily
        output.update(fetch_cached_us_daily(missing, observed_at=observed_at))
    return output
