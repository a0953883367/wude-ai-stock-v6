import json
from datetime import datetime, timedelta, timezone

import pytest

from us_free_daily_fallback import eligible_sessions, fetch_free_completed_daily


def at(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class Calendar:
    def relay_us_year(self, year):
        sessions = [f'2026-10-{day:02d}' for day in (9, 12, 13, 14, 15, 16)] if year == 2026 else []
        return {'year': year, 'status': 'verified_alpaca',
                'sources': ['Alpaca Market Calendar'], 'sessions': sessions,
                'session_details': {day: {'open': '09:30', 'close': '16:00'} for day in sessions}}


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def iter_content(self, chunk_size):
        yield json.dumps(self.payload).encode()

    def close(self):
        pass


class Client:
    def __init__(self, symbols, missing=(), status=200):
        self.symbols, self.missing, self.status = symbols, missing, status
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        payload = {'bars': {s: [
            {'t': f'2026-10-{day}T04:00:00Z', 'o': 10, 'h': 12, 'l': 9,
             'c': 11, 'v': 1000} for day in (14, 15)
        ] for s in self.symbols if s not in self.missing}, 'next_page_token': None}
        response = Response(payload)
        response.status_code = self.status
        return response


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'test-only')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'test-only')


def symbols188():
    return [f'TEST{i}' for i in range(188)]


def test_all_188_batched_and_no_live_fields():
    symbols = symbols188()
    client = Client(symbols)
    now = at('2026-10-16T15:30:00Z')
    result = fetch_free_completed_daily(symbols, Calendar(), now=now, session=client)
    assert result['status'] == 'complete'
    assert result['available_count'] == 188
    assert len(client.calls) == 1
    url, kwargs = client.calls[0]
    assert url.endswith('/v2/stocks/bars')
    assert kwargs['params']['feed'] == 'sip'
    assert kwargs['params']['timeframe'] == '1Day'
    assert at(kwargs['params']['end']) <= now - timedelta(minutes=16)
    assert result['basic_entitlement_verified'] is False
    assert result['affects_formal'] is False
    assert result['realtime'] is False
    assert 'test-only' not in json.dumps(result)
    assert all(not key.startswith('us_live_') for row in result['rows'].values() for key in row)


def test_missing_rows_reported_without_filling():
    symbols = symbols188()
    result = fetch_free_completed_daily(symbols, Calendar(), now=at('2026-10-16T15:30:00Z'),
                                       session=Client(symbols, missing=['TEST0']))
    assert result['status'] == 'partial'
    assert result['available_count'] == 187
    assert result['missing_symbols'] == ['TEST0']


@pytest.mark.parametrize('status,reason', [(401, 'provider_authentication_denied'),
    (403, 'provider_entitlement_denied'), (429, 'provider_rate_limited')])
def test_failure_is_closed_and_sanitized(status, reason):
    result = fetch_free_completed_daily(['AAPL'], Calendar(), now=at('2026-10-16T15:30:00Z'),
                                       session=Client(['AAPL'], status=status))
    assert result['status'] == 'blocked'
    assert result['reason'] == reason
    assert result['rows'] == {}


def test_no_credentials_never_makes_request(monkeypatch):
    monkeypatch.delenv('ALPACA_API_KEY_ID')
    client = Client(['AAPL'])
    result = fetch_free_completed_daily(['AAPL'], Calendar(), session=client)
    assert result['reason'] == 'existing_credentials_unavailable'
    assert not client.calls


def test_midnight_and_dst_guard():
    # Yesterday's 23:59:59 is too recent at 00:05 New York.
    assert eligible_sessions(Calendar(), at('2026-10-16T04:05:00Z'))[-1] == '2026-10-14'
    assert eligible_sessions(Calendar(), at('2026-10-16T04:17:00Z'))[-1] == '2026-10-15'


def test_weekend_uses_last_completed_session():
    assert eligible_sessions(Calendar(), at('2026-10-18T15:30:00Z'))[-1] == '2026-10-16'


def test_timezone_required():
    with pytest.raises(ValueError):
        fetch_free_completed_daily(['AAPL'], Calendar(), now=datetime(2026, 10, 16))


def test_duplicate_symbols_rejected():
    with pytest.raises(ValueError):
        fetch_free_completed_daily(['AAPL', 'AAPL'], Calendar())
