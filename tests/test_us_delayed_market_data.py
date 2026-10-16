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
    monkeypatch.setattr(us_delayed_market_data, 'fetch_free_us_quotes',
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


def test_free_wrapper_uses_cache_only_for_missing_symbols(monkeypatch):
    import us_delayed_market_data as delayed
    import us_cached_daily_fallback as cached
    calls = []
    sip = {'us_delayed_price': 11, 'us_delayed_source': 'Alpaca SIP'}
    yahoo = {'us_delayed_price': 9, 'us_delayed_cached': True}
    monkeypatch.setattr(delayed, 'fetch_delayed_us_bars', lambda *a, **kw: {'AAPL': sip})
    monkeypatch.setattr(cached, 'fetch_cached_us_daily',
        lambda symbols, **kw: calls.append((symbols, kw)) or {'HNHPF': yahoo})
    result = delayed.fetch_free_us_quotes(['AAPL', 'HNHPF'], observed_at=NOW)
    assert result == {'AAPL': sip, 'HNHPF': yahoo}
    assert calls == [(['HNHPF'], {'observed_at': NOW})]


def test_free_wrapper_does_not_read_cache_when_sip_complete(monkeypatch):
    import us_delayed_market_data as delayed
    import us_cached_daily_fallback as cached
    monkeypatch.setattr(delayed, 'fetch_delayed_us_bars', lambda *a, **kw: {'AAPL': {}})
    monkeypatch.setattr(cached, 'fetch_cached_us_daily', lambda *a, **kw: pytest.fail('No cache read'))
    assert delayed.fetch_free_us_quotes(['AAPL']) == {'AAPL': {}}


def test_free_daily_status_exports_counts_only_after_oidc(monkeypatch):
    import live_api
    import us_free_daily_fallback as daily
    import us_cached_daily_fallback as cached
    calls, responses = [], []
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: calls.append('auth'))
    monkeypatch.setattr(daily, 'fetch_free_completed_daily', lambda *a: {
        'missing_symbols': ['HNHPF'], 'requested_count': 2, 'available_count': 1,
        'status': 'partial', 'request_count': 1, 'session_date': '2026-10-15',
        'rows': {'AAPL': {'close': 12345.6789}},
    })
    monkeypatch.setattr(cached, 'fetch_cached_us_daily',
                        lambda symbols: calls.append(symbols) or {'HNHPF': {'us_delayed_price': 98765.4321}})
    handler = SimpleNamespace(path='/api/internal/market-data',
        headers={'Authorization': 'Bearer existing'},
        rate_limiter=SimpleNamespace(allow=lambda: True),
        large_buy_service=SimpleNamespace(weight_shadow=SimpleNamespace(calendar=object())),
        _read_json=lambda: {'kind': 'free_daily_status', 'symbols': ['AAPL', 'HNHPF']},
        _send=lambda status, result: responses.append((status, result)))
    live_api.LiveRequestHandler.do_POST(handler)
    status, result = responses[-1]
    assert status == 200
    assert calls == ['auth', ['HNHPF']]
    assert result['data']['sip_daily_count'] == result['data']['cached_yahoo_count'] == 1
    assert result['data']['status'] == 'complete'
    assert result['data']['market_values_exported'] is False
    assert result['data']['basic_entitlement_verified'] is False
    assert '12345.6789' not in json.dumps(result) and '98765.4321' not in json.dumps(result)
    assert 'rows' not in result['data']
    def deny(token):
        raise PermissionError('denied')
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', deny)
    handler._read_json = lambda: pytest.fail('Auth before payload')
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 403
    assert calls == ['auth', ['HNHPF']]


def test_free_report_validator_requires_complete_non_live_non_raw_coverage():
    from tools.validate_free_report import summarize as summarize_all
    from test_free_required_coverage import SYMBOLS, REQUIRED, row as fixture_row
    target = REQUIRED[0]
    def summarize(report, expected):
        # Test each modified row within the actual unchanged 188-symbol universe.
        candidates = [{**r, 'symbol': target} for r in report['data']]
        untouched = [fixture_row(s) for s in SYMBOLS if s != target]
        if len(expected) > 1:
            untouched = [r for r in untouched if r['symbol'] != 'HNHPF']
        return summarize_all({'data': untouched + candidates}, SYMBOLS)
    row = {'market': 'US', 'symbol': 'AAPL', 'price': 12345.6789,
           'us_delayed_data_available': True, 'us_live_data_available': False,
           'us_delayed_price': 11, 'us_delayed_minimum_seconds': 960,
           'us_delayed_source': 'Yahoo Finance completed daily report cache', 'us_delayed_cached': True,
           'us_delayed_coverage': 'provider_defined', 'us_delayed_interval': '1Day',
           'us_delayed_asof': '2026-10-16T15:13:00Z',
           'us_delayed_fetched_at': '2026-10-16T15:30:00Z'}
    result = summarize({'data': [row]}, ['AAPL'])
    assert result['passed'] is True
    assert '12345.6789' not in json.dumps(result)
    for field, value in [('us_live_data_available', True), ('us_option_data_available', True),
                         ('us_delayed_bars', []), ('price', float('nan')),
                         ('us_delayed_data_available', False)]:
        assert not summarize({'data': [{**row, field: value}]}, ['AAPL'])['passed']
    assert not summarize({'data': [row, row]}, ['AAPL'])['passed']
    assert not summarize({'data': [row]}, ['AAPL', 'HNHPF'])['passed']
    for field in ('us_delayed_source', 'us_delayed_coverage', 'us_delayed_interval',
                  'us_delayed_asof', 'us_delayed_fetched_at', 'us_delayed_minimum_seconds',
                  'us_live_data_available'):
        missing = {key: value for key, value in row.items() if key != field}
        assert not summarize({'data': [missing]}, ['AAPL'])['passed']
    for field, value in [('us_delayed_source', 'Real-time SIP'),
                         ('us_delayed_coverage', 'consolidated'),
                         ('us_delayed_asof', '2026-10-16T15:31:00Z'),
                         ('us_delayed_asof', '2026-10-16T15:15:00Z'),
                         ('us_delayed_asof', '2026-10-16T15:13:00'),
                         ('us_delayed_minimum_seconds', True),
                         ('us_delayed_minimum_seconds', 900),
                         ('us_delayed_price', float('inf'))]:
        assert not summarize({'data': [{**row, field: value}]}, ['AAPL'])['passed']
    yahoo = {**row, 'us_delayed_source': 'Yahoo Finance completed daily report cache',
             'us_delayed_coverage': 'provider_defined', 'us_delayed_interval': '1Day',
             'us_delayed_cached': True, 'us_delayed_asof': '2026-10-15T04:00:00Z'}
    assert summarize({'data': [yahoo]}, ['AAPL'])['passed']
    assert not summarize({'data': [{**yahoo, 'us_delayed_cached': False}]}, ['AAPL'])['passed']
    assert not summarize({'data': [{**yahoo, 'us_delayed_interval': '1Min'}]}, ['AAPL'])['passed']


def test_free_validation_workflow_has_no_publishing_permissions_or_steps():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / '.github/workflows/stock-briefing.yml').read_text()
    job = text.split('  free_report_validation:\n', 1)[1].split('  source_validation:\n', 1)[0]
    assert 'contents: read' in job and 'persist-credentials: false' in job
    assert 'contents: write' not in job and 'actions: write' not in job
    assert 'ALPACA_DATA_MODE: free_delayed' in job
    assert '--no-telegram' in job and 'tools/validate_free_report.py' in job
    assert not any(command in job for command in ('git push', 'git commit', 'publish_owner', 'publish_friend', 'upload-artifact'))
    gate = text.split('  gate:\n', 1)[1].split('  briefing:\n', 1)[0]
    assert 'inputs.us_free_report_validation != true' in gate


def test_free_mode_without_credentials_or_relay_can_use_attested_cache(monkeypatch):
    import us_market_data
    import us_cached_daily_fallback
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.delenv('ALPACA_API_KEY_ID')
    monkeypatch.setattr(us_market_data, '_relay_request', lambda *a, **kw: {})
    calls = []
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily',
        lambda symbols, **kw: calls.append((symbols, kw)) or {'HNHPF': {'us_delayed_cached': True}})
    assert us_market_data.fetch_us_sip_snapshots(['HNHPF'], observed_at=NOW) == {'HNHPF': {'us_delayed_cached': True}}
    assert calls == [(['HNHPF'], {'observed_at': NOW})]


@pytest.mark.parametrize('count,daily_status,expected_code', [(188, 'complete', 0), (187, 'complete', 1), (188, 'partial', 1)])
def test_probe_requires_complete_combined_and_separate_daily_coverage(tmp_path, monkeypatch, capsys, count, daily_status, expected_code):
    from tools import probe_free_us_data as probe
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    from test_free_required_coverage import SYMBOLS, row as fixture_row
    symbols = SYMBOLS
    folder = tmp_path / 'tests/fixtures'
    folder.mkdir(parents=True)
    (folder / 'us_free_probe_universe.json').write_text(json.dumps({
        'symbols': symbols, 'source': 'test', 'source_blob_sha': 'test'}))
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setattr(probe, 'fetch_us_sip_snapshots', lambda *a, **kw: {
        symbol: {**fixture_row(symbol), 'us_delayed_price': 12345.6789}
        for symbol in symbols[:count]})
    monkeypatch.setattr(probe, '_relay_request', lambda *a, **kw: {
        'requested_count': 188, 'status': daily_status, 'missing_symbols': [], 'market_values_exported': False, 'sip_daily_count': 188, 'cached_yahoo_count': 0})
    assert probe.main() == expected_code
    output = capsys.readouterr().out
    assert '12345.6789' not in output
    assert json.loads(output)['historical_available_count'] == count


def test_briefing_delayed_label_distinguishes_yahoo_from_sip():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'briefing.py').read_text())
    labels = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and 'Yahoo已完成日線快取' in ast.unparse(node)]
    assert len(labels) == 1
    statement = compile(ast.Module(body=labels, type_ignores=[]), '<label>', 'exec')
    yahoo = {'us_delayed_cached': True, 'us_delayed_source': 'Yahoo Finance completed daily report cache'}
    exec(statement, {}, {'row': yahoo})
    assert yahoo['next_session_data_mode'] == 'Yahoo已完成日線快取（非即時，僅供參考）'
    assert 'SIP' not in yahoo['next_session_data_mode']
    sip = {'us_delayed_cached': False}
    exec(statement, {}, {'row': sip})
    assert sip['next_session_data_mode'] == 'SIP延遲歷史行情（至少16分鐘；非即時，僅供參考）'
