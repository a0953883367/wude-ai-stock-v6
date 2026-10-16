import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import us_delayed_market_data as module

NOW = datetime.fromisoformat('2026-10-16T15:30:00+00:00')
SECRET = 'SECRET_AUTH https://private.invalid/?token=SECRET_AUTH'


def bar(stamp='2026-10-16T15:13:00Z', **updates):
    return {'t': stamp, 'o': 10, 'h': 12, 'l': 9, 'c': 11, 'v': 1000, **updates}


class Response:
    def __init__(self, bars=None, token=None, status=200, raw=None, error=None):
        self.status_code = status
        self.headers = {}
        self.raw = raw if raw is not None else json.dumps({'bars': bars or {}, 'next_page_token': token}).encode()
        self.error = error
        self.closed = False
    def iter_content(self, chunk_size):
        if self.error:
            raise self.error
        yield self.raw
    def close(self):
        self.closed = True


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0) if self.responses else Response()
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'secret-key')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'secret-secret')


def run(responses, symbols=('AAPL',), target=module, telemetry=True):
    counts = {}
    client = Client(responses)
    output = target.fetch_delayed_us_bars(symbols, session=client, observed_at=NOW,
        **({'telemetry': counts} if telemetry else {}))
    assert all(s not in json.dumps(counts) for s in (SECRET, 'SECRET_AUTH', 'private.invalid', 'secret-key', 'secret-secret'))
    return output, counts, client


def cases():
    return [
        ([requests.ConnectTimeout(SECRET)], 'provider_network_failure', 'connect_or_headers'),
        ([Response(error=requests.ConnectionError(SECRET))], 'provider_network_failure', 'body'),
        ([Response(status=403)], 'provider_http_failure', 'http'),
        ([Response(status=429)], 'provider_http_failure', 'http'),
        ([Response(raw=b'not json '+SECRET.encode())], 'invalid_json', 'json'),
        ([Response(raw=b'[]')], 'invalid_payload', 'payload'),
        ([Response({'OTHER': [bar()]})], 'unexpected_symbol', 'symbols'),
        ([Response({'AAPL': {}})], 'invalid_bars', 'bar_list'),
        ([Response({'AAPL': [None]})], 'invalid_bar', 'bar_shape'),
        ([Response({'AAPL': [bar('2026-10-16T15:13:00')]})], 'invalid_bar', 'bar_timestamp'),
        ([Response({'AAPL': [bar('2026-10-16T15:14:00Z')]})], 'invalid_bar', 'bar_timestamp'),
        ([Response({'AAPL': [bar(c=-1)]})], 'invalid_bar', 'bar_ohlcv'),
        ([Response({'AAPL': [bar(c=float('nan'))]})], 'invalid_bar', 'bar_ohlcv'),
        ([Response({'AAPL': [bar('2026-10-16T12:00:00Z')]})], 'out_of_range_bar', 'bar_window'),
        ([Response({'AAPL': [bar(), bar()]})], 'duplicate_bar', 'duplicate_bar'),
        ([Response({'AAPL': [bar()]}, token=12)], 'invalid_pagination', 'pagination'),
        ([Response({}, token='same'), Response({}, token='same')], 'invalid_pagination', 'pagination'),
        ([Response({}, token=str(i)) for i in range(12)], 'pagination_budget', 'pagination'),
    ]


@pytest.mark.parametrize('responses,reason,phase', cases())
def test_every_failed_branch_reports_fixed_reason_and_phase(responses, reason, phase):
    output, counts, client = run(responses)
    assert output == {}
    assert counts['status'] == 'blocked' and counts['failures'][0]['reason'] == reason
    assert counts['failures'][0]['phase'] == phase and counts['failures'][0]['stage'] == 'minute'
    assert counts['returned_count'] == 0
    assert counts['request_count'] == len(client.calls)
    assert all(response.closed for response in responses if isinstance(response, Response))


def test_minute_success_survives_daily_failure_with_receipt():
    output, counts, client = run([Response({'AAPL': [bar()]}), requests.ReadTimeout(SECRET)], ['AAPL', 'MISSING'])
    assert set(output) == {'AAPL'}
    assert counts['status'] == 'recovered' and counts['coverage_status'] == 'partial'
    assert counts['failures'][0]['stage'] == 'missing_daily'
    assert counts['minute_available_count'] == 1
    assert counts['missing_for_daily_count'] == 1
    assert counts['failures'][0]['network_category'] == 'read_timeout'
    assert counts['request_count'] == 2
    assert client.calls[1][1]['params']['symbols'] == 'MISSING'


def test_daily_timestamp_validation_is_distinct():
    output, counts, _ = run([Response(), Response({'AAPL': [bar('2026-10-15T15:00:00Z')]})])
    assert not output
    assert counts['failures'][0]['stage'] == 'missing_daily'
    assert counts['failures'][0]['reason'] == 'invalid_bar' and counts['failures'][0]['phase'] == 'bar_timestamp'


def test_current_daily_session_excluded_without_claiming_error():
    output, counts, _ = run([Response(), Response({'AAPL': [bar('2026-10-16T04:00:00Z')]})])
    assert not output and counts['status'] == 'complete'
    assert counts['daily_available_count'] == counts['returned_count'] == 0


def test_byte_budget_distinct(monkeypatch):
    monkeypatch.setattr(module, 'MAX_BYTES', 2)
    output, counts, _ = run([Response({'AAPL': [bar()]})])
    assert not output and counts['failures'][0]['reason'] == 'response_byte_budget'
    assert counts['failures'][0]['phase'] == 'body' and counts['response_bytes'] > 2


def test_deadline_distinct(monkeypatch):
    ticks = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: ticks[0])
    class Late(Response):
        def iter_content(self, chunk_size):
            ticks[0] = 76
            yield self.raw
    output, counts, _ = run([Late({'AAPL': [bar()]})])
    assert not output and counts['failures'][0]['reason'] == 'request_time_budget'
    assert counts['failures'][0]['phase'] == 'body' and counts['elapsed_ms'] == 76000


def test_static_parameters_and_success_counts_unchanged():
    output, counts, client = run([Response({'AAPL': [bar()]})])
    url, options = client.calls[0]
    params = options['params']
    assert url == 'https://data.alpaca.markets/v2/stocks/bars'
    assert {k: params[k] for k in ('feed', 'adjustment', 'currency', 'sort', 'limit', 'timeframe')} == {
        'feed': 'sip', 'adjustment': 'split', 'currency': 'USD', 'sort': 'desc', 'limit': 10000, 'timeframe': '1Min'}
    start, end = (datetime.fromisoformat(params[k]) for k in ('start', 'end'))
    assert end == NOW - timedelta(minutes=17)
    assert end - start == timedelta(minutes=90)
    assert options['timeout'] <= 20 and options['allow_redirects'] is False
    assert counts['total_budget_seconds'] == 75 and counts['minimum_delay_seconds'] == 960
    assert counts['validated_bar_count'] == counts['validated_symbol_count'] == counts['minute_available_count'] == 1
    assert counts['completed_page_count'] == counts['returned_count'] == 1
    assert 'us_delayed_bars' not in json.dumps(counts)
    assert output['AAPL']['us_delayed_interval'] == '1Min'


def test_invalid_observation_and_symbol_contract_remains_raise():
    with pytest.raises(ValueError):
        module.fetch_delayed_us_bars(['AAPL'], observed_at=datetime(2026, 10, 16), telemetry={})
    with pytest.raises(ValueError):
        module.fetch_delayed_us_bars(['INVALID!'], observed_at=NOW, telemetry={})


def test_successful_results_match_deployed_v3_outputs():
    spec = importlib.util.spec_from_file_location('v3_reference', Path(__file__).parent / 'fixtures/v3_minute_baseline.py')
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    for factory, symbols in [
        (lambda: [Response({'AAPL': [bar()]})], ['AAPL']),
        (lambda: [Response(), Response({'AAPL': [bar('2026-10-15T04:00:00Z')]})], ['AAPL']),
    ]:
        expected, _, old_client = run(factory(), symbols, target=baseline, telemetry=False)
        for enabled in (False, True):
            actual, _, new_client = run(factory(), symbols, telemetry=enabled)
            assert actual == expected
            assert new_client.calls == old_client.calls


def test_diagnostics_only_available_after_existing_oidc_gate(monkeypatch):
    import live_api
    sent, calls = [], []
    def fetch(symbols, **kwargs):
        calls.append(kwargs)
        if 'telemetry' in kwargs:
            kwargs['telemetry'].update(status='blocked', reason='invalid_bar', phase='bar_ohlcv')
        return {}
    monkeypatch.setattr(module, 'fetch_free_us_quotes', fetch)
    payload = {'kind': 'sip', 'data_mode': 'free_delayed', 'symbols': ['AAPL'], 'include_free_diagnostics': True}
    handler = SimpleNamespace(path='/api/internal/market-data', headers={'Authorization': 'Bearer test'},
        rate_limiter=SimpleNamespace(allow=lambda: True), _read_json=lambda: payload,
        _send=lambda status, result: sent.append((status, result)))
    def deny(token):
        raise PermissionError('denied')
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', deny)
    live_api.LiveRequestHandler.do_POST(handler)
    assert sent[-1][0] == 403 and not calls
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: None)
    live_api.LiveRequestHandler.do_POST(handler)
    assert sent[-1][0] == 200
    assert sent[-1][1]['private_free_diagnostics']['reason'] == 'invalid_bar'
    payload.pop('include_free_diagnostics')
    live_api.LiveRequestHandler.do_POST(handler)
    assert 'private_free_diagnostics' not in sent[-1][1] and calls[-1] == {}


def test_187_successful_minute_symbols_survive_daily_error():
    symbols = [f'TEST{i}' for i in range(188)]
    output, counts, _ = run([Response({s: [bar()] for s in symbols[:187]}),
                            requests.ReadTimeout(SECRET)], symbols)
    assert len(output) == 187
    assert counts['status'] == 'recovered' and counts['coverage_status'] == 'partial'
    assert counts['minute_available_count'] == 187
    assert counts['missing_for_daily_count'] == 1
    assert counts['failures'][0]['stage'] == 'missing_daily' and counts['request_count'] == 2
    assert counts['returned_count'] == 187


def test_deadline_before_first_request_makes_no_provider_call(monkeypatch):
    calls = [0]
    def clock():
        calls[0] += 1
        return 0 if calls[0] == 1 else 76
    monkeypatch.setattr(module.time, 'monotonic', clock)
    output, counts, client = run([])
    assert not output and not client.calls
    assert counts['failures'][0]['reason'] == 'request_time_budget' and counts['failures'][0]['phase'] == 'before_request'


def test_swallowed_generic_typeerror_has_no_exception_text():
    class WrongChunk(Response):
        def iter_content(self, chunk_size):
            yield SECRET
    output, counts, _ = run([WrongChunk()])
    assert not output and counts['failures'][0]['reason'] == 'invalid_value' and counts['failures'][0]['phase'] == 'body'


def test_authenticated_relay_sidecar_does_not_change_market_data(monkeypatch):
    import us_market_data
    monkeypatch.setenv('WUDE_LIVE_API_BASE', 'https://relay.invalid')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://identity.invalid')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'test')
    class JsonResponse:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    monkeypatch.setattr(us_market_data.requests, 'get', lambda *a, **kw: JsonResponse({'value': 'existing-oidc'}))
    monkeypatch.setattr(us_market_data.requests, 'post', lambda *a, **kw: JsonResponse({
        'data': {'AAPL': {'us_delayed_price': 11}},
        'private_free_diagnostics': {'reason': 'invalid_bar', 'phase': 'bar_ohlcv'}}))
    counts = {}
    output = us_market_data._relay_request('sip', {'include_free_diagnostics': True}, 90, free_diagnostics=counts)
    assert output == {'AAPL': {'us_delayed_price': 11}}
    assert counts == {'reason': 'invalid_bar', 'phase': 'bar_ohlcv'}


def test_probe_opt_in_uses_existing_calls_only_and_prints_no_prices(tmp_path, monkeypatch, capsys):
    from tools import probe_free_us_data as probe
    folder = tmp_path / 'tests/fixtures'
    folder.mkdir(parents=True)
    from test_free_required_coverage import SYMBOLS, row as fixture_row
    symbols = SYMBOLS
    (folder / 'us_free_probe_universe.json').write_text(json.dumps({
        'symbols': symbols, 'source': 'fixture', 'source_blob_sha': 'fixture'}))
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    calls = []
    def relay(kind, payload, timeout, **kwargs):
        calls.append(kind)
        if kind == 'sip':
            assert payload['include_free_diagnostics'] is True
            kwargs['free_diagnostics'].update(stage='missing_daily', minute_available_count=187,
                reason='provider_network_failure', phase='connect_or_headers')
            return {s: {**fixture_row(s), 'us_delayed_price': 98765.4321} for s in symbols}
        return {'requested_count': 188, 'status': 'complete', 'missing_symbols': [], 'market_values_exported': False, 'sip_daily_count': 188, 'cached_yahoo_count': 0}
    monkeypatch.setattr(probe, '_relay_request', relay)
    assert probe.main(diagnostics=True) == 0
    captured = capsys.readouterr().out
    assert '98765.4321' not in captured
    assert calls == ['sip', 'free_daily_status']
    assert json.loads(captured)['private_free_diagnostics']['minute_available_count'] == 187


def test_network_failure_after_successful_page_has_no_stale_http_status():
    output, counts, _ = run([Response({'AAPL': [bar()]}, token='next'), requests.ReadTimeout(SECRET)])
    assert not output
    assert counts['failures'][0]['http_status'] is None
    assert counts['failures'][0]['request_count'] == 2 and counts['failures'][0]['completed_page_count'] == 1


def test_stream_byte_accounting_updates_only_once_per_response(monkeypatch):
    calls = []
    original = module._count
    def count(telemetry, field, amount=1):
        if field == 'response_bytes':
            calls.append(amount)
        return original(telemetry, field, amount)
    monkeypatch.setattr(module, '_count', count)
    class ByteResponse(Response):
        def iter_content(self, chunk_size):
            for byte in self.raw:
                yield bytes([byte])
    response = ByteResponse({'AAPL': [bar()]})
    _, counts, _ = run([response])
    assert calls == [len(response.raw)]
    assert counts['response_bytes'] == len(response.raw)


def test_health_marker_and_workflow_diagnostic_flag_are_explicit():
    root = Path(__file__).resolve().parents[1]
    assert '"free_diagnostics_version": 1' in (root / 'live_api.py').read_text()
    workflow = (root / '.github/workflows/stock-briefing.yml').read_text()
    assert 'run: python tools/probe_free_us_data.py --diagnostics' in workflow
    assert 'inputs.us_free_data == true' in workflow


def test_minute_transport_error_recovers_187_daily_rows_with_failure_receipt():
    symbols = [f'TEST{i}' for i in range(188)]
    output, counts, client = run([requests.ReadTimeout(SECRET),
        Response({s: [bar('2026-10-15T04:00:00Z')] for s in symbols[:187]})], symbols)
    assert len(output) == 187
    assert all(row['us_delayed_interval'] == '1Day' for row in output.values())
    assert counts['status'] == 'recovered' and counts['coverage_status'] == 'partial'
    assert counts['minute_available_count'] == 0 and counts['daily_available_count'] == 187
    assert len(counts['failures']) == 1
    assert counts['failures'][0]['stage'] == 'minute'
    assert counts['failures'][0]['reason'] == 'provider_network_failure'
    assert counts['failures'][0]['network_category'] == 'read_timeout'
    assert counts['failures'][0]['http_status'] is None
    assert len(client.calls) == 2


def test_validation_failure_can_recover_but_is_not_relabelled_transport():
    output, counts, _ = run([Response({'AAPL': [bar(c=-1)]}),
                             Response({'AAPL': [bar('2026-10-15T04:00:00Z')]})])
    assert output['AAPL']['us_delayed_interval'] == '1Day'
    assert counts['status'] == 'recovered' and counts['coverage_status'] == 'complete'
    assert counts['failures'][0]['reason'] == 'invalid_bar'
    assert counts['failures'][0]['phase'] == 'bar_ohlcv'
    assert 'network_category' not in counts['failures'][0]


def test_both_stages_fail_and_both_safe_receipts_survive():
    output, counts, _ = run([requests.ConnectTimeout(SECRET), Response(status=403)])
    assert output == {} and counts['status'] == 'blocked'
    assert counts['coverage_status'] == 'unavailable'
    assert [item['stage'] for item in counts['failures']] == ['minute', 'missing_daily']
    assert [item['reason'] for item in counts['failures']] == ['provider_network_failure', 'provider_http_failure']
    assert counts['failures'][1]['http_status'] == 403
    assert 'network_category' not in counts['failures'][1]


def test_recovery_keeps_original_total_deadline(monkeypatch):
    ticks = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: ticks[0])
    class LateMinute(Client):
        def get(self, url, **kwargs):
            if not self.calls:
                self.calls.append((url, kwargs))
                ticks[0] = 70
                raise requests.ReadTimeout(SECRET)
            return super().get(url, **kwargs)
    client = LateMinute([Response({'AAPL': [bar('2026-10-15T04:00:00Z')]})])
    counts = {}
    result = module.fetch_delayed_us_bars(['AAPL'], session=client, observed_at=NOW, telemetry=counts)
    assert result['AAPL']['us_delayed_interval'] == '1Day'
    assert client.calls[1][1]['timeout'] == 5
    assert counts['total_budget_seconds'] == 75 and counts['status'] == 'recovered'


def test_failed_minute_pages_never_reappear_as_minute_rows():
    output, counts, _ = run([Response({'AAPL': [bar()]}, token='next'),
                             requests.ReadTimeout(SECRET),
                             Response({'AAPL': [bar('2026-10-15T04:00:00Z')]})])
    assert output['AAPL']['us_delayed_interval'] == '1Day'
    assert counts['minute_available_count'] == 0
    assert counts['failures'][0]['validated_symbol_count'] == 1
    assert counts['status'] == 'recovered'
