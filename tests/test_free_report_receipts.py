import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import us_market_data
from free_report_diagnostics import sanitize_diagnostics, write_receipt, print_receipt

SECRET = 'SECRET_ACCESS https://private.invalid/?token=SECRET_ACCESS'


class Response:
    def __init__(self, payload=None, status=200, error=None):
        self.payload, self.status_code, self.error = payload, status, error
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(SECRET)
    def json(self):
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.delenv('ALPACA_API_KEY_ID', raising=False)
    monkeypatch.delenv('ALPACA_API_SECRET_KEY', raising=False)
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setenv('WUDE_LIVE_API_BASE', 'https://relay.test.invalid')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://identity.test.invalid')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'test-only-token')


@pytest.mark.parametrize('where,phase,reason', [
    ('config', 'oidc_configuration', 'configuration_unavailable'),
    ('identity_network', 'oidc_acquisition', 'provider_network_failure'),
    ('identity_http', 'oidc_http', 'provider_http_failure'),
    ('identity_json', 'oidc_response', 'invalid_json'),
    ('identity_payload', 'oidc_response', 'invalid_payload'),
    ('identity_empty', 'oidc_response', 'empty_oidc_token'),
    ('post_network', 'relay_post', 'provider_network_failure'),
    ('post_http', 'relay_http', 'provider_http_failure'),
    ('post_json', 'relay_response', 'invalid_json'),
    ('post_payload', 'relay_response', 'invalid_payload'),
    ('empty', 'complete', 'empty_data'),
])
def test_all_relay_failure_phases_are_safe_and_never_complete(monkeypatch, caplog, where, phase, reason):
    import us_cached_daily_fallback
    cached = {'NVDA': {'us_delayed_cached': True}}
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', lambda *a, **kw: cached)
    if where == 'config': monkeypatch.delenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    def identity(*a, **kw):
        if where == 'identity_network': raise requests.ReadTimeout(SECRET)
        if where == 'identity_http': return Response(status=403)
        if where == 'identity_json': return Response(error=ValueError(SECRET))
        if where == 'identity_payload': return Response([])
        if where == 'identity_empty': return Response({})
        return Response({'value': 'test-only-oidc'})
    def post(*a, **kw):
        if where == 'post_network': raise requests.ConnectTimeout(SECRET)
        if where == 'post_http': return Response(status=503)
        if where == 'post_json': return Response(error=ValueError(SECRET))
        if where == 'post_payload': return Response({'data': []})
        return Response({'data': {}})
    monkeypatch.setattr(us_market_data.requests, 'get', identity)
    monkeypatch.setattr(us_market_data.requests, 'post', post)
    receipt = {}
    result = us_market_data.fetch_us_sip_snapshots(['NVDA'], timeout=25,
        daily_shadow_samples={}, free_diagnostics=receipt)
    assert result == cached
    assert receipt['relay']['status'] in {'blocked', 'unavailable'}
    assert receipt['relay']['phase'] == phase and receipt['relay']['reason'] == reason
    assert receipt['client_cache_count'] == receipt['client_returned_count'] == 1
    assert 'SECRET_ACCESS' not in json.dumps(receipt) and 'SECRET_ACCESS' not in caplog.text
    assert 'private.invalid' not in json.dumps(receipt) and 'private.invalid' not in caplog.text


@pytest.mark.parametrize('exception,category', [(requests.ConnectTimeout, 'connect_timeout'),
    (requests.ReadTimeout, 'read_timeout'), (requests.exceptions.SSLError, 'tls_error'),
    (requests.exceptions.ProxyError, 'proxy_error'), (requests.ConnectionError, 'connection_error'),
    (requests.exceptions.ChunkedEncodingError, 'chunked_encoding_error'),
    (requests.exceptions.ContentDecodingError, 'content_decoding_error'),
    (requests.Timeout, 'timeout'), (requests.exceptions.InvalidHeader, 'request_error')])
def test_network_categories_are_allowlisted(monkeypatch, caplog, exception, category):
    def fail(*a, **kw): raise exception(SECRET)
    monkeypatch.setattr(us_market_data.requests, 'get', fail)
    receipt = {}
    assert us_market_data._relay_request('sip', {'symbols': ['NVDA'], 'data_mode': 'free_delayed'}, 25, free_diagnostics=receipt) == {}
    assert receipt['relay']['network_category'] == category
    assert 'SECRET_ACCESS' not in json.dumps(receipt) + caplog.text


def test_receipt_writer_and_printer_recursively_allowlist_types(tmp_path, capsys):
    path = tmp_path / 'receipt.json'
    payload = {'status': 'recovered', 'returned_count': 187, 'price': 123.45,
        'relay': {'phase': 'relay_post', 'reason': 'provider_network_failure',
                  'network_category': 'read_timeout', 'exception': SECRET, 'http_status': None},
        'failures': [{'stage': 'minute', 'reason': 'invalid_bar', 'phase': 'bar_ohlcv',
                      'price': 987.65, 'headers': SECRET}], 'request_count': True,
        'elapsed_ms': float('inf'), 'source': SECRET, 'urls': [SECRET]}
    assert write_receipt(path, payload, market_stage_completed=True)
    raw = path.read_text()
    assert 'SECRET_ACCESS' not in raw and '123.45' not in raw and '987.65' not in raw
    print_receipt(path)
    output = json.loads(capsys.readouterr().out)
    assert output['market_stage_completed'] is True
    assert output['diagnostics']['relay']['phase'] == 'relay_post'
    assert output['diagnostics']['failures'][0]['phase'] == 'bar_ohlcv'
    assert 'request_count' not in output['diagnostics'] and 'elapsed_ms' not in output['diagnostics']


def test_receipt_missing_or_malicious_file_prints_only_fixed_missing(tmp_path, capsys):
    path = tmp_path / 'missing.json'
    print_receipt(path)
    assert json.loads(capsys.readouterr().out) == {'receipt_status': 'unavailable', 'reason': 'receipt_missing'}
    path.write_text(SECRET)
    print_receipt(path)
    assert json.loads(capsys.readouterr().out) == {'receipt_status': 'unavailable', 'reason': 'receipt_missing'}


def test_briefing_opt_in_finally_receipt_and_workflow_does_not_mask_failure(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'briefing.py').read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    start = next(i for i,node in enumerate(main.body) if isinstance(node, ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id == 'free_diagnostic_path' for t in node.targets))
    nodes = main.body[start:start + 4]
    assert isinstance(nodes[-1], ast.Try) and nodes[-1].finalbody
    code = compile(ast.Module(body=nodes, type_ignores=[]), '<briefing-opt-in>', 'exec')
    for mode, explicit, fail in [('paid_live', True, False), ('free_delayed', False, False),
                                  ('free_delayed', True, False), ('free_delayed', True, True)]:
        path = tmp_path / f'{mode}-{explicit}-{fail}.json'
        env = {'ALPACA_DATA_MODE': mode, 'WUDE_FREE_REPORT_DIAGNOSTICS_PATH': str(path) if explicit else ''}
        calls = []
        def fetch(*a, **kw):
            calls.append(kw)
            if 'free_diagnostics' in kw:
                kw['free_diagnostics']['relay'] = {'status': 'blocked', 'phase': 'relay_post', 'reason': 'provider_network_failure'}
            if fail: raise RuntimeError('simulated stage failure')
            return {}
        scope = dict(os=SimpleNamespace(getenv=lambda key, default='': env.get(key,default)),
            _stage=lambda label, action: action(), fetch_us_sip_snapshots=fetch,
            us_symbols={'NVDA'}, TREASURY_SIP_SYMBOLS={}, SETTINGS=SimpleNamespace(request_timeout=25),
            us_daily_shadow_samples={})
        if fail:
            with pytest.raises(RuntimeError): exec(code, scope)
        else:
            exec(code, scope)
        enabled = mode == 'free_delayed' and explicit
        assert path.exists() is enabled
        assert ('free_diagnostics' in calls[0]) is enabled
        if enabled: assert json.loads(path.read_text())['market_stage_completed'] is (not fail)
    workflow = (root / '.github/workflows/stock-briefing.yml').read_text()
    job = workflow.split('  free_report_validation:\n',1)[1].split('  source_validation:\n',1)[0]
    assert 'if: ${{ always() }}' in job
    assert 'python free_report_diagnostics.py "$WUDE_FREE_REPORT_DIAGNOSTICS_PATH"' in job
    assert 'continue-on-error' not in job
    assert 'free-report.log' in job and 'cat ' not in job
