"""No-network end-to-end report reference reader, including the 191-symbol batch."""
from datetime import datetime
from types import SimpleNamespace
import pytest
import requests
from test_us_delayed_market_data import Client, payload, bar
from test_free_required_coverage import SYMBOLS
from us_free_report_data import fetch_free_report_daily, SOURCE
from tools.validate_free_report import valid_delayed_reference

NOW = datetime.fromisoformat('2026-10-16T18:00:00+00:00')
DAYS = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15']
CALENDAR = SimpleNamespace(relay_us_year=lambda year: {
    'year': year, 'status': 'verified_alpaca', 'sources': ['Alpaca Market Calendar'],
    'sessions': DAYS if year == 2026 else [], 'session_details': {day: {'open': '09:30', 'close': '16:00'} for day in DAYS}})


def test_exact191_uses_only_two_completed_sessions(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'fake')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'fake')
    symbols = sorted(set(SYMBOLS) | {'TLT', 'IEF', 'SHY'})
    client = Client(payload({s: [bar(day + 'T04:00:00Z') for day in DAYS[-2:]] for s in symbols}))
    receipt = {}
    rows = fetch_free_report_daily(symbols, CALENDAR, session=client, observed_at=NOW, telemetry=receipt)
    assert len(rows) == 191 and len(client.calls) == 1
    call = client.calls[0][1]
    assert call['params']['start'] == '2026-10-14'
    assert call['params']['end'] == '2026-10-15T23:59:59-04:00'
    assert call['params']['timeframe'] == '1Day' and call['params']['limit'] == 10000
    assert call['timeout'] == (5, 20)
    assert call['allow_redirects'] is False and call['stream'] is True
    assert all(valid_delayed_reference(row) for row in rows.values())
    assert all(row['us_delayed_source'] == SOURCE and 'us_delayed_bars' not in row for row in rows.values())
    assert receipt['source_daily']['status'] == 'complete' and receipt['daily_available_count'] == 191


def test_daily_network_failure_receipt_survives_cache(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'fake')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'fake')
    import us_cached_daily_fallback
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', lambda *a, **kw: {'AAPL': {'us_delayed_cached': True}})
    receipt = {}
    fetch_free_report_daily(['AAPL', 'MSFT'], CALENDAR,
        session=Client(requests.ReadTimeout('SECRET url token')), observed_at=NOW, telemetry=receipt)
    assert receipt['source_daily'] == {'status': 'blocked', 'requested_count': 2, 'returned_count': 0,
        'request_count': 1, 'reason': 'provider_network_failure', 'network_category': 'read_timeout',
        'phase': 'connect_or_headers'}
    assert receipt['status'] == 'recovered' and receipt['coverage_status'] == 'partial'
    assert 'SECRET' not in str(receipt)


def test_completed_daily_label_rejects_minute_interval():
    row = {'us_delayed_data_available': True, 'us_live_data_available': False,
        'us_delayed_source': SOURCE, 'us_delayed_interval': '1Min', 'us_delayed_coverage': 'consolidated',
        'us_delayed_price': 10, 'us_delayed_minimum_seconds': 960,
        'us_delayed_asof': '2026-10-15T16:00:00-04:00',
        'us_delayed_session_close': '2026-10-15T16:00:00-04:00',
        'us_delayed_session_date': '2026-10-15', 'us_delayed_calendar_verified': True,
        'us_delayed_calendar_source': 'Alpaca Market Calendar', 'us_delayed_session_completed': True, 'us_delayed_fetched_at': NOW.isoformat()}
    assert not valid_delayed_reference(row)
    row['us_delayed_interval'] = '1Day'
    assert valid_delayed_reference(row)


def test_briefing_completed_daily_label_is_distinct():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'briefing.py').read_text())
    expression = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.IfExp)
        and any(isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                and target.slice.value == 'next_session_data_mode' for target in node.targets))
    label = eval(compile(ast.Expression(expression), '<label>', 'eval'),
                 {'row': {'us_delayed_source': SOURCE, 'us_delayed_session_date': '2026-10-15'}})
    assert label == 'SIP已完成日線（2026-10-15；非即時，僅供參考）'


def test_publication_stamp_preserves_generation_time(monkeypatch):
    import ast, os
    from pathlib import Path
    from datetime import timezone
    tree = ast.parse((Path(__file__).parents[1] / 'briefing.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_stamp_us_daily_reference_publication')
    namespace = {'os': os, 'datetime': datetime, 'timezone': timezone}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<stamp>', 'exec'), namespace)
    stamp = namespace[node.name]
    report = {'updated_at': 'original-generation-start', 'data': [{'us_delayed_interval': '1Day', 'us_delayed_data_available': True}]}
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid_live')
    stamp(report)
    assert 'us_daily_reference_published_at' not in report
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    stamp(report)
    assert report['updated_at'] == 'original-generation-start'
    assert datetime.fromisoformat(report['us_daily_reference_published_at']).tzinfo is not None


@pytest.mark.parametrize('field,value', [('us_delayed_calendar_verified', False),
    ('us_delayed_session_completed', False), ('us_delayed_calendar_source', 'unverified'),
    ('us_delayed_session_date', '2026-10-14'), ('us_delayed_session_close', '2026-10-15T23:59:59-04:00')])
def test_daily_validator_requires_session_attestation(monkeypatch, field, value):
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'fake')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'fake')
    client = Client(payload({'AAPL': [bar(day + 'T04:00:00Z') for day in DAYS[-2:]]}))
    row = fetch_free_report_daily(['AAPL'], CALENDAR, session=client, observed_at=NOW)['AAPL']
    row[field] = value
    assert not valid_delayed_reference(row)


@pytest.mark.parametrize('mutation', ['year', 'reversed', 'invalid_close', 'price', 'future'])
def test_adapter_local_validation_never_claims_source_complete(monkeypatch, mutation):
    import us_free_report_data as adapter
    import us_cached_daily_fallback
    daily = {'status': 'complete', 'request_count': 1, 'observed_at': NOW.isoformat(),
        'rows': {'AAPL': {'session_date': '2026-10-15', 'close': 10}}}
    evidence = CALENDAR.relay_us_year(2026)
    if mutation == 'year': evidence['year'] = 2025
    if mutation == 'reversed': evidence['session_details']['2026-10-15']['open'] = '17:00'
    if mutation == 'invalid_close': evidence['session_details']['2026-10-15']['close'] = 'bogus'
    if mutation == 'price': daily['rows']['AAPL']['close'] = -1
    if mutation == 'future': daily['rows']['AAPL']['session_date'] = '2026-10-17'
    monkeypatch.setattr(adapter, 'fetch_free_completed_daily', lambda *a, **kw: daily)
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', lambda *a, **kw: {})
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily_references', lambda *a, **kw: {})
    receipt = {}
    assert adapter.fetch_free_report_daily(['AAPL'], SimpleNamespace(relay_us_year=lambda year: evidence), observed_at=NOW, telemetry=receipt) == {}
    assert receipt['source_daily']['status'] == 'blocked'
    assert receipt['source_daily']['returned_count'] == 0
    assert receipt['source_daily']['reason'] in {'official_calendar_invalid', 'invalid_bar'}
    assert receipt['status'] == 'blocked'


def test_partial_report_relay_fills_local_cache_without_erasing_source_failure(monkeypatch):
    import us_market_data as market
    import us_cached_daily_fallback as cache
    monkeypatch.setattr(market, '_credentials', lambda: None)
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    def relay(*args, **kwargs):
        kwargs['free_diagnostics'].update(source_daily={'status': 'blocked', 'reason': 'provider_network_failure'})
        from test_free_required_coverage import row
        return {'AAPL': {**row('AAPL'), 'us_delayed_price': 10,
            'us_delayed_source': 'Yahoo Finance completed daily report cache',
            'us_delayed_interval': '1Day', 'us_delayed_cached': True, 'us_delayed_coverage': 'provider_defined'}}
    monkeypatch.setattr(market, '_relay_request', relay)
    monkeypatch.setattr(cache, 'fetch_cached_us_daily_references', lambda symbols, **kw: {'MSFT': {'us_delayed_cached': True}} if symbols == ['MSFT'] else pytest.fail('wrong missing'))
    monkeypatch.setattr(cache, 'fetch_cached_us_daily', lambda *a, **kw: pytest.fail('No remaining missing'))
    receipt = {}
    rows = market.fetch_us_sip_snapshots(['AAPL', 'MSFT'], free_purpose='completed_daily_report', free_diagnostics=receipt)
    assert set(rows) == {'AAPL', 'MSFT'} and rows['AAPL']['us_delayed_price'] == 10
    assert receipt['source_daily']['status'] == 'blocked'
    assert receipt['client_cache_count'] == 1 and receipt['client_returned_count'] == 2


@pytest.mark.parametrize('publication,passed', [(None, False), ('2026-10-16T17:59:00Z', False),
    ('2026-10-16T18:01:00Z', True), ('2026-10-16T19:01:00Z', False), ('2026-10-16T18:01:00', False)])
def test_report_publication_order_is_verified(monkeypatch, publication, passed):
    from tools.validate_free_report import summarize
    from test_free_required_coverage import report
    monkeypatch.setenv('ALPACA_API_KEY_ID', 'fake')
    monkeypatch.setenv('ALPACA_API_SECRET_KEY', 'fake')
    client = Client(payload({'AAPL': [bar(day + 'T04:00:00Z') for day in DAYS[-2:]]}))
    reference = fetch_free_report_daily(['AAPL'], CALENDAR, session=client, observed_at=NOW)['AAPL']
    value = report()
    value['data'][0].update(reference)
    value['data'][0].pop('us_delayed_cached', None)
    value.pop('us_daily_reference_published_at', None)
    if publication is not None:
        value['us_daily_reference_published_at'] = publication
    result = summarize(value, SYMBOLS, observed_at=datetime.fromisoformat('2026-10-16T19:00:00+00:00'))
    assert result['passed'] is passed and result['daily_reference_publication_valid'] is passed


def test_invalid_relay_identity_does_not_suppress_report_cache(monkeypatch):
    import us_market_data as market
    import us_cached_daily_fallback as cache
    monkeypatch.setattr(market, '_credentials', lambda: None)
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(market, '_relay_request', lambda *a, **kw: {'AAPL': {'us_delayed_price': -1}})
    cached = {'us_delayed_cached': True}
    monkeypatch.setattr(cache, 'fetch_cached_us_daily_references', lambda symbols, **kw: {'AAPL': cached})
    receipt = {}
    rows = market.fetch_us_sip_snapshots(['AAPL'], free_purpose='completed_daily_report', free_diagnostics=receipt)
    assert rows == {'AAPL': cached} and receipt['client_rejected_count'] == 1
    assert receipt['relay']['status'] == 'blocked' and receipt['relay']['reason'] == 'invalid_payload'


def test_report_daily_health_capability_preserves_existing_markers():
    from pathlib import Path
    text = (Path(__file__).parents[1] / 'live_api.py').read_text()
    assert '"report_daily_version": 1' in text
    assert '"free_diagnostics_version": 1' in text and 'US-FREE-DELAYED-V4' in text


def test_validator_cli_imports_without_pythonpath():
    import subprocess, sys
    from pathlib import Path
    script = Path(__file__).parents[1] / 'tools/validate_free_report.py'
    result = subprocess.run([sys.executable, '-I', '-c',
        'import runpy,sys; runpy.run_path(sys.argv[1], run_name="cli_import_check")', str(script)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('field', ['us_delayed_price', 'us_delayed_minimum_seconds'])
def test_large_numeric_reference_fails_closed(field):
    from test_free_required_coverage import row
    value = row('AAPL')
    value[field] = 10**400
    assert not valid_delayed_reference(value)


def test_report_rejects_minute_route_while_general_quote_validator_accepts():
    from test_free_required_coverage import report
    from tools.validate_free_report import summarize
    value = report()
    value['data'][0].update(us_delayed_source='Alpaca SIP historical (>=16min delayed)',
        us_delayed_coverage='consolidated', us_delayed_interval='1Min')
    assert valid_delayed_reference(value['data'][0])
    assert not summarize(value, SYMBOLS)['passed']


@pytest.mark.parametrize('bad', ['minute', 'future_fetch', 'future_read'])
def test_report_relay_rejects_wrong_route_or_future_reference(monkeypatch, bad):
    import us_market_data as market
    import us_cached_daily_fallback as cache
    from test_free_required_coverage import row
    value = row('AAPL')
    if bad == 'minute':
        value.update(us_delayed_source='Alpaca SIP historical (>=16min delayed)', us_delayed_coverage='consolidated', us_delayed_interval='1Min')
    else:
        value['us_delayed_fetched_at' if bad == 'future_fetch' else 'us_delayed_cache_read_at'] = '2026-10-17T18:00:00Z'
    monkeypatch.setattr(market, '_credentials', lambda: None)
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(market, '_relay_request', lambda *a, **kw: {'AAPL': value})
    monkeypatch.setattr(cache, 'fetch_cached_us_daily_references', lambda symbols, **kw: {'AAPL': {'cached': True}})
    receipt = {}
    assert market.fetch_us_sip_snapshots(['AAPL'], observed_at=NOW, free_purpose='completed_daily_report', free_diagnostics=receipt) == {'AAPL': {'cached': True}}
    assert receipt['client_rejected_count'] == 1


@pytest.mark.parametrize('field', ['us_delayed_source', 'us_delayed_interval', 'us_delayed_coverage'])
@pytest.mark.parametrize('invalid', [[], {}, None])
def test_malformed_reference_labels_fail_closed(field, invalid):
    from test_free_required_coverage import row, report
    from us_free_reference_validation import valid_report_reference
    from tools.validate_free_report import summarize
    value = row('AAPL')
    value[field] = invalid
    assert not valid_delayed_reference(value) and not valid_report_reference(value)
    data = report()
    data['data'][0][field] = invalid
    assert not summarize(data, SYMBOLS)['passed']
