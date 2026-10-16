"""Bounded daily transport overrides and secret-free operational diagnostics."""
import json
from types import SimpleNamespace

import pytest
import requests

import us_daily_shadow_history as history
from us_free_daily_fallback import fetch_free_completed_daily
from test_us_free_daily_fallback import Calendar, Client, at


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'test')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'test')


def test_existing_reader_timeout_defaults_unchanged(monkeypatch):
    monkeypatch.setattr(history.clock, 'monotonic', lambda: 0)
    client = Client(['AAPL'])
    history._fetch(['AAPL'], ['2026-10-14', '2026-10-15'], client,
                   ('test', 'test'), 75, {'request_count': 0})
    assert client.calls[0][1]['timeout'] == (3, 7)


def test_free_daily_overrides_only_its_transport_timeouts(monkeypatch):
    monkeypatch.setattr(history.clock, 'monotonic', lambda: 0)
    client = Client(['AAPL'])
    result = fetch_free_completed_daily(['AAPL'], Calendar(),
        now=at('2026-10-16T15:30:00Z'), session=client)
    assert result['status'] == 'complete'
    assert client.calls[0][1]['timeout'] == (5, 20)


def test_override_still_bounded_by_remaining_budget(monkeypatch):
    monkeypatch.setattr(history.clock, 'monotonic', lambda: 0)
    client = Client(['AAPL'])
    history._fetch(['AAPL'], ['2026-10-14', '2026-10-15'], client,
                   ('test', 'test'), 4, {'request_count': 0}, connect_timeout=5, read_timeout=20)
    assert client.calls[0][1]['timeout'] == (2, 2)


@pytest.mark.parametrize('exception,category', [
    (requests.exceptions.ConnectTimeout, 'connect_timeout'),
    (requests.exceptions.ReadTimeout, 'read_timeout'),
    (requests.exceptions.SSLError, 'tls_error'),
    (requests.exceptions.ProxyError, 'proxy_error'),
    (requests.exceptions.ConnectionError, 'connection_error'),
    (requests.exceptions.ChunkedEncodingError, 'chunked_encoding_error'),
    (requests.exceptions.ContentDecodingError, 'content_decoding_error'),
    (requests.exceptions.Timeout, 'timeout'),
    (requests.exceptions.InvalidHeader, 'request_error'),
])
@pytest.mark.parametrize('phase', ['connect_or_headers', 'body'])
def test_network_diagnostics_are_fixed_labels_not_exception_text(exception, category, phase):
    closed = []
    def fail(*args, **kwargs):
        raise exception('SECRET_KEY https://private.example?token=secret Authorization: secret')
    response = SimpleNamespace(status_code=200, headers={}, iter_content=fail,
                               close=lambda: closed.append(True))
    client = SimpleNamespace(get=fail if phase == 'connect_or_headers' else lambda *a, **kw: response)
    result = fetch_free_completed_daily(['AAPL'], Calendar(),
        now=at('2026-10-16T15:30:00Z'), session=client)
    assert result['status'] == 'blocked' and result['rows'] == {}
    assert result['reason'] == 'provider_network_failure'
    assert result['request_count'] == 1
    assert result['network_error_category'] == category
    assert result['network_error_phase'] == phase
    assert all(secret not in json.dumps(result) for secret in ('SECRET_KEY', 'private.example', 'Authorization'))
    assert bool(closed) == (phase == 'body')


def test_authenticated_status_exposes_only_safe_transport_fields(monkeypatch):
    import live_api
    import us_free_daily_fallback
    import us_cached_daily_fallback
    responses = []
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: None)
    monkeypatch.setattr(us_free_daily_fallback, 'fetch_free_completed_daily', lambda *a: {
        'requested_count': 1, 'available_count': 0, 'missing_symbols': ['AAPL'],
        'status': 'blocked', 'reason': 'provider_network_failure', 'request_count': 1,
        'network_error_category': 'read_timeout', 'network_error_phase': 'body',
        'exception_text': 'SECRET_KEY', 'rows': {},
    })
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', lambda *a: {})
    handler = SimpleNamespace(path='/api/internal/market-data', headers={'Authorization': 'Bearer existing'},
        rate_limiter=SimpleNamespace(allow=lambda: True),
        large_buy_service=SimpleNamespace(weight_shadow=SimpleNamespace(calendar=object())),
        _read_json=lambda: {'kind': 'free_daily_status', 'symbols': ['AAPL']},
        _send=lambda status, result: responses.append((status, result)))
    live_api.LiveRequestHandler.do_POST(handler)
    status, result = responses[-1]
    assert status == 200
    assert result['data']['network_error_category'] == 'read_timeout'
    assert result['data']['network_error_phase'] == 'body'
    assert 'SECRET_KEY' not in json.dumps(result) and 'exception_text' not in result['data']
