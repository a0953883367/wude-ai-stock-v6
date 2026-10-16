"""Independent guardrail tests; all provider traffic is simulated."""
import json
from datetime import datetime, timedelta

import pytest
import requests
from types import SimpleNamespace

from us_delayed_market_data import fetch_delayed_us_bars


NOW = datetime.fromisoformat('2026-10-16T15:30:00+00:00')


def bar(stamp='2026-10-16T15:13:00Z', **updates):
    return {'t': stamp, 'o': 10, 'h': 12, 'l': 9, 'c': 11, 'v': 1000, **updates}


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield json.dumps(self.payload).encode()

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('private-provider-error')


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            return Response({'bars': {}, 'next_page_token': None})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def payload(bars, token=None):
    return Response({'bars': bars, 'next_page_token': token})


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'test-key-never-export')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'test-secret-never-export')


def fetch(client, symbols=None):
    return fetch_delayed_us_bars(symbols or ['AAPL'], session=client, observed_at=NOW)


def test_all_188_symbols_use_bounded_historical_requests():
    symbols = [f'TEST{i}' for i in range(188)]
    client = Client(payload({symbol: [bar()] for symbol in symbols}))
    result = fetch(client, symbols)
    assert set(result) == set(symbols)
    assert len(client.calls) == 1
    url, kwargs = client.calls[0]
    assert url.endswith('/v2/stocks/bars')
    assert kwargs['params']['feed'] == 'sip'
    assert kwargs['params']['timeframe'] == '1Min'
    assert kwargs['allow_redirects'] is False
    assert kwargs['stream'] is True
    end = datetime.fromisoformat(kwargs['params']['end'].replace('Z', '+00:00'))
    assert end <= NOW - timedelta(minutes=16)
    row = result[symbols[0]]
    assert row['us_delayed_price'] == 11
    assert row['us_delayed_minimum_seconds'] >= 960
    assert row['us_delayed_interval'] == '1Min'
    assert row['us_delayed_coverage'] == 'consolidated'
    assert row['us_live_data_available'] is False
    assert {k for k in row if k.startswith('us_live_')} == {'us_live_data_available'}
    assert 'test-secret' not in json.dumps(result)


@pytest.mark.parametrize('status', [301, 401, 403, 429, 500])
def test_http_failure_never_exports_rows(status):
    assert fetch(Client(Response({'secret': 'private-provider-error'}, status))) == {}


def test_no_credentials_never_requests(monkeypatch):
    monkeypatch.delenv('ALPACA_API_SECRET_KEY')
    client = Client()
    assert fetch(client) == {}
    assert client.calls == []


def test_network_error_never_exports_exception_details():
    assert fetch(Client(requests.ConnectionError('private-provider-error'))) == {}


@pytest.mark.parametrize('updates', [
    {'c': float('nan')}, {'c': float('inf')}, {'v': -1}, {'v': True},
    {'l': 13}, {'h': 9}, {'c': 0}, {'o': None},
])
def test_malformed_ohlcv_never_available(updates):
    assert 'AAPL' not in fetch(Client(payload({'AAPL': [bar(**updates)]})))


@pytest.mark.parametrize('stamp', ['2026-10-16T15:29:00Z', '2026-10-17T15:13:00Z',
                                  '2026-10-16T15:13:00', 'invalid'])
def test_future_recent_and_naive_bars_never_available(stamp):
    assert 'AAPL' not in fetch(Client(payload({'AAPL': [bar(stamp)]})))


def test_unrequested_symbol_never_exported():
    result = fetch(Client(payload({'AAPL': [bar()], 'SECRET': [bar()]})))
    assert 'SECRET' not in result


def test_pagination_collects_symbols_before_returning():
    client = Client(payload({'AAPL': [bar()]}, 'page-two'),
                    payload({'MSFT': [bar()]}))
    result = fetch(client, ['AAPL', 'MSFT'])
    assert set(result) == {'AAPL', 'MSFT'}
    assert len(client.calls) == 2
    assert client.calls[1][1]['params']['page_token'] == 'page-two'


def test_failed_later_page_does_not_export_incomplete_minute_data():
    client = Client(payload({'AAPL': [bar()]}, 'page-two'), Response({}, 403))
    assert fetch(client, ['AAPL', 'MSFT']) == {}


def test_repeated_page_token_stops_within_request_bound():
    client = Client(payload({'AAPL': [bar()]}, 'same'),
                    payload({}, 'same'))
    assert fetch(client) == {}
    assert len(client.calls) <= 3


def test_daily_fallback_explicitly_labels_completed_session():
    client = Client(payload({}), payload({'AAPL': [bar('2026-10-15T04:00:00Z')]}))
    result = fetch(client)
    assert result['AAPL']['us_delayed_interval'] == '1Day'
    assert result['AAPL']['us_live_data_available'] is False
    assert client.calls[1][1]['params']['timeframe'] == '1Day'


def test_daily_fallback_rejects_current_local_session():
    client = Client(payload({}), payload({'AAPL': [bar('2026-10-16T04:00:00Z')]}))
    assert 'AAPL' not in fetch(client)


def test_daily_fallback_rejects_non_midnight_timestamp():
    client = Client(payload({}), payload({'AAPL': [bar('2026-10-15T15:00:00Z')]}))
    assert 'AAPL' not in fetch(client)


def test_duplicate_bar_across_pages_fails_closed():
    client = Client(payload({'AAPL': [bar()]}, 'next'), payload({'AAPL': [bar()]}))
    assert fetch(client) == {}


def test_no_partial_current_minute_bar_is_returned():
    assert fetch(Client(payload({'AAPL': [bar('2026-10-16T15:14:00Z')]}))) == {}


def test_successful_response_is_closed():
    response = payload({'AAPL': [bar()]})
    fetch(Client(response))
    assert response.closed


def test_http_error_response_is_closed():
    response = Response({}, 403)
    fetch(Client(response))
    assert response.closed


def test_daily_fallback_only_requests_missing_symbols():
    client = Client(payload({'AAPL': [bar()]}),
                    payload({'MSFT': [bar('2026-10-15T04:00:00Z')]}))
    result = fetch(client, ['AAPL', 'MSFT'])
    assert set(result) == {'AAPL', 'MSFT'}
    assert client.calls[1][1]['params']['symbols'] == 'MSFT'
    assert result['AAPL']['us_delayed_interval'] == '1Min'
    assert result['MSFT']['us_delayed_interval'] == '1Day'


def test_opt_in_integration_never_requests_live_snapshot(monkeypatch):
    from us_market_data import fetch_us_sip_snapshots
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    collector = {}
    client = Client(payload({'AAPL': [bar()]}))
    result = fetch_us_sip_snapshots(['AAPL'], session=client,
                                    daily_shadow_samples=collector, observed_at=NOW)
    assert result['AAPL']['us_live_data_available'] is False
    assert result['AAPL']['us_delayed_data_available'] is True
    assert collector == {}
    assert all(url.endswith('/v2/stocks/bars') for url, _ in client.calls)


def test_default_integration_still_uses_existing_snapshot(monkeypatch):
    from us_market_data import fetch_us_sip_snapshots
    monkeypatch.delenv('ALPACA_DATA_MODE', raising=False)
    client = Client(Response({'AAPL': {'latestTrade': {'p': 11}}}))
    result = fetch_us_sip_snapshots(['AAPL'], session=client, observed_at=NOW)
    assert result['AAPL']['us_live_data_available'] is True
    assert client.calls[0][0].endswith('/v2/stocks/snapshots')


def test_empty_symbol_set_retains_existing_noop_contract(monkeypatch):
    from us_market_data import fetch_us_sip_snapshots
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    client = Client()
    assert fetch_us_sip_snapshots([], session=client, observed_at=NOW) == {}
    assert not client.calls


def test_streaming_time_budget_stops_and_closes(monkeypatch):
    import us_delayed_market_data as module
    ticks = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: ticks[0])

    class SlowResponse(Response):
        def iter_content(self, chunk_size):
            yield b'{'
            ticks[0] = 76
            yield b'"bars": {}}'
            pytest.fail('Stream was read beyond its deadline')

    response = SlowResponse()
    assert fetch(Client(response)) == {}
    assert response.closed


def test_response_byte_budget_stops_and_closes(monkeypatch):
    import us_delayed_market_data as module
    monkeypatch.setattr(module, 'MAX_BYTES', 8)
    response = payload({'AAPL': [bar()]})
    assert fetch(Client(response)) == {}
    assert response.closed


def test_minute_bars_outside_requested_window_rejected():
    assert fetch(Client(payload({'AAPL': [bar('2026-10-16T12:00:00Z')]}))) == {}


def test_free_mode_disables_options_before_direct_or_relay_requests(monkeypatch):
    import us_market_data
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setenv('ALPACA_OPTION_FEED', 'opra')
    monkeypatch.setattr(us_market_data, '_relay_request', lambda *a, **kw: pytest.fail('No options relay'))
    client = Client()
    assert us_market_data.fetch_us_opra_signals([{'symbol': 'AAPL'}], session=client) == {}
    assert not client.calls


def test_free_mode_never_starts_us_websocket(monkeypatch):
    import threading
    import large_buy_streams
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    statuses = []
    service = SimpleNamespace(symbols=lambda market: ['AAPL'],
                              set_stream_status=lambda *a, **kw: statuses.append((a, kw)))
    stream = large_buy_streams.AlpacaLargeBuyStream(service, threading.Event())
    monkeypatch.setattr(large_buy_streams.threading, 'Thread', lambda *a, **kw: pytest.fail('No thread'))
    stream.start()
    assert stream._thread is None
    assert statuses[-1][0] == ('US', 'blocked')
    assert statuses[-1][1]['subscribed'] == 0


def test_delayed_live_api_preserves_asof_and_never_fetches_options():
    from live_api import LiveDataService
    row = fetch(Client(payload({'AAPL': [bar()]})))['AAPL']
    service = LiveDataService(us_fetcher=lambda symbols: {'AAPL': row},
        option_fetcher=lambda *a: pytest.fail('No options for delayed quote'))
    result = service._us_quote('AAPL', include_options=True)
    assert result['quote_status'] == 'delayed'
    assert result['realtime'] is False
    assert result['fetched_at'] == row['us_delayed_asof']
    assert result['observed_at'] == row['us_delayed_fetched_at']
    assert result['quote']['lastPrice'] == 11
    assert result['quote']['bids'] == result['quote']['asks'] == []
    assert result['quote']['orderBookComplete'] is False
    assert result['options'] == {}


def test_free_mode_is_propagated_to_existing_authenticated_relay(monkeypatch):
    import us_market_data
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.delenv('ALPACA_API_KEY_ID')
    calls = []
    monkeypatch.setattr(us_market_data, '_relay_request',
                        lambda *a, **kw: calls.append((a, kw)) or {})
    assert us_market_data.fetch_us_sip_snapshots(['AAPL']) == {}
    assert calls[0][0][0] == 'sip'
    assert calls[0][0][1]['data_mode'] == 'free_delayed'
    assert calls[0][0][1]['symbols'] == ['AAPL']


def test_delayed_internal_relay_keeps_oidc_gate(monkeypatch):
    import live_api
    import us_delayed_market_data
    calls, responses = [], []
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: calls.append(token))
    monkeypatch.setattr(us_delayed_market_data, 'fetch_delayed_us_bars',
                        lambda symbols: calls.append(symbols) or {'AAPL': {'us_delayed_price': 11}})
    monkeypatch.setattr(live_api, 'fetch_us_sip_snapshots', lambda *a, **kw: pytest.fail('No live fallback'))
    handler = SimpleNamespace(path='/api/internal/market-data',
        headers={'Authorization': 'Bearer test-oidc'},
        rate_limiter=SimpleNamespace(allow=lambda: True),
        _read_json=lambda: {'kind': 'sip', 'symbols': ['AAPL'], 'data_mode': 'free_delayed',
                            'include_daily_shadow': True},
        _send=lambda status, result: responses.append((status, result)))
    live_api.LiveRequestHandler.do_POST(handler)
    assert calls == ['test-oidc', ['AAPL']]
    assert responses[-1][0] == 200
    assert 'private_daily_shadow_samples' not in responses[-1][1]
    def deny(token):
        raise PermissionError('denied')
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', deny)
    handler._read_json = lambda: pytest.fail('Denied request must not parse payload')
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 403
    assert calls == ['test-oidc', ['AAPL']]
