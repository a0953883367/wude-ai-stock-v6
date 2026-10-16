"""Offline reproduction of report vs source-probe relay timeout budgets.

All identity/relay/provider interactions are mocked. No sleeps or network calls.
This reproduces a possible failure path, not an assertion about production logs.
"""
import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import us_market_data
from treasury_stress import TREASURY_SIP_SYMBOLS
from test_free_required_coverage import SYMBOLS, REQUIRED, row

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def report_environment(monkeypatch):
    monkeypatch.delenv('ALPACA_API_KEY_ID', raising=False)
    monkeypatch.delenv('ALPACA_API_SECRET_KEY', raising=False)
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setenv('REQUEST_TIMEOUT', '25')
    monkeypatch.setenv('WUDE_LIVE_API_BASE', 'https://relay.test.invalid')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://oidc.test.invalid')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'test-only-runner-token')
    assert us_market_data._credentials() is None


class JsonResponse:
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self): return self.value


def actual_report_stage_call(collector, *, fetcher=None, diagnostics=None):
    """Execute the precise production lambda without running the full report."""
    tree = ast.parse((ROOT / 'briefing.py').read_text())
    lambdas = [node for node in ast.walk(tree) if isinstance(node, ast.Lambda)
        and isinstance(node.body, ast.Call)
        and isinstance(node.body.func, ast.Name)
        and node.body.func.id == 'fetch_us_sip_snapshots']
    assert len(lambdas) == 1
    namespace = dict(os=os, fetch_us_sip_snapshots=fetcher or us_market_data.fetch_us_sip_snapshots,
        us_symbols=set(SYMBOLS), TREASURY_SIP_SYMBOLS=TREASURY_SIP_SYMBOLS,
        SETTINGS=SimpleNamespace(request_timeout=25), us_daily_shadow_samples=collector, us_free_diagnostics=diagnostics)
    return eval(compile(ast.Expression(lambdas[0]), '<actual-report-lambda>', 'eval'), namespace)()


def fake_delayed_service(monkeypatch, seconds=35):
    calls, identity_calls = [], []
    def identity(*args, **kwargs):
        identity_calls.append(kwargs)
        return JsonResponse({'value': 'test-only-oidc'})
    def post(url, **kwargs):
        calls.append(kwargs)
        # Model a service that produces its first response after `seconds`.
        # Raise immediately if its caller would time out first; never sleep.
        if kwargs['timeout'] < seconds:
            raise requests.ReadTimeout('simulated response exceeds caller budget')
        rows = {s: row(s) for s in kwargs['json']['symbols']}
        if kwargs['json'].get('purpose') == 'completed_daily_report':
            for value in rows.values():
                value.update(us_delayed_source='Yahoo Finance completed daily report cache',
                    us_delayed_interval='1Day', us_delayed_cached=True, us_delayed_coverage='provider_defined',
                    us_delayed_asof='2026-10-15T20:00:00Z')
        return JsonResponse({'data': rows})
    monkeypatch.setattr(us_market_data.requests, 'get', identity)
    monkeypatch.setattr(us_market_data.requests, 'post', post)
    return calls, identity_calls


def test_exact_report_payload_30s_times_out_to_77_cached_rows_while_90s_probe_succeeds(report_environment, monkeypatch):
    import us_cached_daily_fallback
    cache_symbols = REQUIRED[:76] + ['HNHPF']
    cached = {s: {**row(s), 'us_delayed_source': 'Yahoo Finance completed daily report cache',
        'us_delayed_cached': True, 'us_delayed_interval': '1Day',
        'us_delayed_coverage': 'provider_defined'} for s in cache_symbols}
    cache_calls = []
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily',
        lambda symbols, **kw: cache_calls.append(symbols) or cached)
    calls, identity_calls = fake_delayed_service(monkeypatch)
    collector = {}
    import importlib.util
    spec = importlib.util.spec_from_file_location('pre_budget_reference',
        Path(__file__).parent / 'fixtures/pre_budget_us_market_data.py')
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    report_rows = actual_report_stage_call(collector, fetcher=lambda *a, **kw: baseline.fetch_us_sip_snapshots(*a, **{k:v for k,v in kw.items() if k != "free_purpose"}))
    assert report_rows == cached and len(report_rows) == 77
    assert len(set(report_rows) & set(REQUIRED)) == 76
    assert collector == {}
    payload = calls[0]['json']
    assert len(payload['symbols']) == 191
    assert set(payload['symbols']) == set(SYMBOLS) | {'TLT', 'IEF', 'SHY'}
    assert payload['kind'] == 'sip' and payload['data_mode'] == 'free_delayed'
    assert payload['include_daily_shadow'] is True
    assert calls[0]['timeout'] == 30
    assert cache_calls == [payload['symbols']]
    source_rows = us_market_data.fetch_us_sip_snapshots(SYMBOLS, timeout=90)
    assert len(source_rows) == 188 and calls[1]['timeout'] == 90
    assert len(calls) == len(identity_calls) == 2
    assert all(call['timeout'] == 20 for call in identity_calls)
    assert len(cache_calls) == 1


def test_report_path_returns_full191_when_service_is_within30s(report_environment, monkeypatch):
    import us_cached_daily_fallback
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', lambda *a, **kw: pytest.fail('No cache fallback'))
    calls, _ = fake_delayed_service(monkeypatch, seconds=29)
    assert len(actual_report_stage_call({})) == 191
    assert calls[0]['timeout'] == 90
    assert calls[0]['json']['purpose'] == 'completed_daily_report'


def test_server_free_mode_priority_ignores_shadow_request_and_accepts191_after_oidc(monkeypatch):
    import live_api
    import us_delayed_market_data
    from us_daily_shadow_history import _symbols
    symbols = sorted(set(SYMBOLS) | set(TREASURY_SIP_SYMBOLS.values()))
    assert len(_symbols(symbols)) == 191  # Below existing 200-symbol cap.
    order, replies = [], []
    def validate(token): order.append('oidc')
    def free_fetch(requested, **kwargs):
        order.append('free')
        assert requested == symbols
        return {s: row(s) for s in requested}
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', validate)
    monkeypatch.setattr(us_delayed_market_data, 'fetch_free_us_quotes', free_fetch)
    monkeypatch.setattr(live_api, 'fetch_us_sip_snapshots', lambda *a, **kw: pytest.fail('Wrong paid/shadow branch'))
    handler = SimpleNamespace(path='/api/internal/market-data', headers={'Authorization': 'Bearer test'},
        rate_limiter=SimpleNamespace(allow=lambda: True),
        _read_json=lambda: {'kind': 'sip', 'data_mode': 'free_delayed',
            'include_daily_shadow': True, 'symbols': symbols},
        _send=lambda status, result: replies.append((status, result)))
    live_api.LiveRequestHandler.do_POST(handler)
    assert order == ['oidc', 'free']
    assert replies[-1][0] == 200 and len(replies[-1][1]['data']) == 191
    assert 'private_daily_shadow_samples' not in replies[-1][1]


@pytest.mark.parametrize('enabled', [False, True])
def test_fixed_90s_free_relay_budget_resolves_same_report_path(report_environment, monkeypatch, enabled):
    import us_cached_daily_fallback
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', lambda *a, **kw: pytest.fail('No fallback needed'))
    calls, _ = fake_delayed_service(monkeypatch)
    diagnostics = {} if enabled else None
    assert len(actual_report_stage_call({}, diagnostics=diagnostics)) == 191
    assert calls[0]['timeout'] == 90
    assert calls[0]['json']['include_daily_shadow'] is True
    assert (calls[0]['json'].get('include_free_diagnostics') is True) is enabled
    if enabled:
        assert diagnostics['relay']['status'] == 'complete'
        assert diagnostics['relay']['returned_count'] == 191


def test_paid_relay_timeout_remains_30_for25_input(report_environment, monkeypatch):
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid_live')
    calls, _ = fake_delayed_service(monkeypatch, seconds=29)
    assert len(actual_report_stage_call({})) == 191
    assert calls[0]['timeout'] == 30
    assert 'data_mode' not in calls[0]['json']
    assert 'include_free_diagnostics' not in calls[0]['json']


def test_free_relay_budget_is_fixed90_even_for_large_caller_timeout(report_environment, monkeypatch):
    calls, identity_calls = fake_delayed_service(monkeypatch)
    assert len(us_market_data.fetch_us_sip_snapshots(SYMBOLS, timeout=10000)) == 188
    assert calls[0]['timeout'] == 90 and identity_calls[0]['timeout'] == 20


def test_paid_report_payload_matches_pre_budget_reference(report_environment, monkeypatch):
    import importlib.util
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid_live')
    spec = importlib.util.spec_from_file_location('pre_budget_paid',
        Path(__file__).parent / 'fixtures/pre_budget_us_market_data.py')
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    calls, _ = fake_delayed_service(monkeypatch, seconds=29)
    expected = actual_report_stage_call({}, fetcher=baseline.fetch_us_sip_snapshots)
    actual = actual_report_stage_call({})
    assert actual == expected and calls[0] == calls[1]
