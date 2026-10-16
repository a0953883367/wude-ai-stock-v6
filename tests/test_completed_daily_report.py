"""Completed-session report routing; providers and authentication are simulated."""
from datetime import datetime
from types import SimpleNamespace

import pytest

NOW = datetime.fromisoformat('2026-10-16T15:30:00+00:00')
PURPOSE = 'completed_daily_report'


class Calendar:
    def relay_us_year(self, year):
        return {'year': year, 'status': 'verified_alpaca',
                'sources': ['Alpaca Market Calendar'], 'sessions': ['2026-10-15'],
                'session_details': {'2026-10-15': {'open': '09:30', 'close': '16:00'}}}



def fail(*args, **kwargs):
    pytest.fail('Unexpected provider or alternate data path')


def daily_result(symbols=('AAPL',), status='complete', reason=None):
    rows = {symbol: {'symbol': symbol, 'session_date': '2026-10-15',
                    'close': 110, 'volume': 1200, 'previous_close': 100,
                    'source': 'Alpaca SIP historical daily bars', 'feed': 'sip',
                    'interval': '1Day', 'realtime': False}
            for symbol in symbols}
    result = {'status': status, 'rows': rows, 'available_count': len(rows),
              'requested_count': 2 if status != 'complete' else len(rows),
              'missing_symbols': ['MSFT'] if status == 'partial' else [],
              'observed_at': NOW.isoformat(), 'request_count': 1, 'session_date': '2026-10-15',
              'source': 'Alpaca SIP historical daily bars', 'realtime': False}
    if reason:
        result['reason'] = reason
    return result


def stub_sources(monkeypatch, daily, cache=None):
    import us_free_report_data as adapter
    import us_free_daily_fallback as source
    import us_cached_daily_fallback as cached
    calls = []
    def get_daily(symbols, calendar, **kwargs):
        calls.append(('daily', symbols, calendar, kwargs))
        return daily
    def get_cache(symbols, **kwargs):
        calls.append(('cache', symbols, kwargs))
        return cache or {}
    monkeypatch.setattr(source, 'fetch_free_completed_daily', get_daily)
    monkeypatch.setattr(cached, 'fetch_cached_us_daily', get_cache)
    monkeypatch.setattr(cached, 'fetch_cached_us_daily_references', lambda *a, **kw: {})
    for name, replacement in [('fetch_free_completed_daily', get_daily),
                              ('fetch_cached_us_daily', get_cache)]:
        if hasattr(adapter, name):
            monkeypatch.setattr(adapter, name, replacement)
    return adapter, calls


def test_completed_daily_normalization_and_no_raw_bars(monkeypatch):
    adapter, calls = stub_sources(monkeypatch, daily_result())
    calendar, session, telemetry = Calendar(), Calendar(), {}
    result = adapter.fetch_free_report_daily(['AAPL'], calendar, session=session,
                                             observed_at=NOW, telemetry=telemetry)
    row = result['AAPL']
    assert row['us_delayed_price'] == 110
    assert row['us_delayed_source'] == 'Alpaca SIP completed daily bars'
    assert row['us_delayed_interval'] == '1Day'
    assert row['us_delayed_coverage'] == 'consolidated'
    assert row['us_live_data_available'] is False
    assert row['us_delayed_data_available'] is True
    assert datetime.fromisoformat(row['us_delayed_asof']) == datetime.fromisoformat('2026-10-15T16:00:00-04:00')
    assert row['us_delayed_session_close'] == row['us_delayed_asof']
    assert row['us_delayed_session_date'] == '2026-10-15'
    assert row['us_delayed_calendar_verified'] is True
    assert row['us_delayed_calendar_source'] == 'Alpaca Market Calendar'
    assert row['us_delayed_session_completed'] is True
    assert row['us_delayed_fetched_at'] == NOW.isoformat()
    assert 'us_delayed_bars' not in row and 'bars' not in row
    assert not any(key in row for key in ('price', 'score', 'us_live_price'))
    assert telemetry['source_daily']['status'] == 'complete'
    assert telemetry['coverage_status'] == 'complete'
    assert telemetry['status'] == 'complete'
    assert telemetry['returned_count'] == 1
    assert len(calls) == 1
    assert calls[0][2] is calendar and calls[0][3]['session'] is session
    assert calls[0][3]['now'] == NOW


def test_cache_only_receives_missing_and_preserves_sip_rows(monkeypatch):
    cached = {'us_delayed_price': 21, 'us_delayed_source': 'Yahoo Finance completed daily report cache',
              'us_live_data_available': False, 'us_delayed_interval': '1Day'}
    adapter, calls = stub_sources(monkeypatch, daily_result(status='partial'), {'MSFT': cached})
    telemetry = {}
    result = adapter.fetch_free_report_daily(['AAPL', 'MSFT'], Calendar(), observed_at=NOW, telemetry=telemetry)
    assert result['AAPL']['us_delayed_price'] == 110
    assert result['MSFT'] == cached
    assert calls[-1][0:2] == ('cache', ['MSFT'])
    assert telemetry['source_daily']['status'] == 'partial'
    assert telemetry['coverage_status'] == 'complete'
    assert telemetry['returned_count'] == 2


def test_blocked_source_partial_cache_does_not_claim_complete(monkeypatch):
    daily = daily_result((), 'blocked', 'provider_http_failure')
    daily['missing_symbols'] = ['AAPL', 'MSFT']
    adapter, calls = stub_sources(monkeypatch, daily, {'AAPL': {'us_delayed_price': 11}})
    telemetry = {}
    result = adapter.fetch_free_report_daily(['AAPL', 'MSFT'], Calendar(), observed_at=NOW, telemetry=telemetry)
    assert set(result) == {'AAPL'}
    assert telemetry['source_daily']['status'] == 'blocked'
    assert telemetry['source_daily']['reason'] == 'provider_http_failure'
    assert telemetry['coverage_status'] == 'partial'
    assert telemetry['status'] == 'recovered'
    assert telemetry['returned_count'] == 1
    assert calls[-1][1] == ['AAPL', 'MSFT']


def test_blocked_source_empty_cache_retains_blocked(monkeypatch):
    daily = daily_result((), 'blocked', 'official_calendar_unavailable')
    daily['missing_symbols'] = ['AAPL', 'MSFT']
    adapter, _ = stub_sources(monkeypatch, daily)
    telemetry = {}
    assert adapter.fetch_free_report_daily(['AAPL', 'MSFT'], Calendar(), observed_at=NOW, telemetry=telemetry) == {}
    assert telemetry['status'] == 'blocked'
    assert telemetry['coverage_status'] != 'complete'
    assert telemetry['source_daily']['reason'] == 'official_calendar_unavailable'


def test_direct_free_report_selects_adapter_and_preserves_calendar(monkeypatch):
    import us_market_data
    import us_free_report_data
    import us_delayed_market_data
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(us_market_data, '_credentials', lambda: ('key', 'secret'))
    monkeypatch.setattr(us_delayed_market_data, 'fetch_free_us_quotes', fail)
    calls = []
    def fetch(symbols, calendar=None, **kwargs):
        calls.append((symbols, calendar, kwargs))
        return {'AAPL': {'us_live_data_available': False}}
    monkeypatch.setattr(us_free_report_data, 'fetch_free_report_daily', fetch)
    calendar, session, telemetry, shadow = Calendar(), Calendar(), {}, {}
    output = us_market_data.fetch_us_sip_snapshots(['AAPL'], calendar=calendar, session=session,
        observed_at=NOW, free_diagnostics=telemetry, daily_shadow_samples=shadow, free_purpose=PURPOSE)
    assert output['AAPL']['us_live_data_available'] is False
    assert calls[0][1] is calendar
    assert calls[0][2]['session'] is session and calls[0][2]['observed_at'] == NOW
    assert calls[0][2]['telemetry'] is telemetry and shadow == {}


@pytest.mark.parametrize('free', [True, False])
def test_relay_forwards_purpose_only_for_free_mode(monkeypatch, free):
    import us_market_data
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed' if free else 'paid')
    monkeypatch.setattr(us_market_data, '_credentials', lambda: None)
    calls = []
    def relay(kind, payload, timeout, **kwargs):
        calls.append(payload)
        return {'AAPL': {'available': True}}
    monkeypatch.setattr(us_market_data, '_relay_request', relay)
    us_market_data.fetch_us_sip_snapshots(['AAPL'], free_purpose=PURPOSE)
    assert (calls[0].get('purpose') == PURPOSE) is free
    if not free:
        assert 'purpose' not in calls[0]


def handler_for(payload, sent, calendar):
    return SimpleNamespace(path='/api/internal/market-data', headers={'Authorization': 'Bearer test-oidc'},
        rate_limiter=SimpleNamespace(allow=lambda: True), _read_json=lambda: payload,
        large_buy_service=SimpleNamespace(weight_shadow=SimpleNamespace(calendar=calendar)),
        _send=lambda status, result: sent.append((status, result)))


def test_internal_report_requires_oidc_and_beats_shadow(monkeypatch):
    import live_api
    import us_free_report_data
    import us_delayed_market_data
    sent, calls, calendar = [], [], Calendar()
    payload = {'kind': 'sip', 'symbols': ['AAPL'], 'data_mode': 'free_delayed',
               'purpose': PURPOSE, 'include_daily_shadow': True, 'include_free_diagnostics': True}
    handler = handler_for(payload, sent, calendar)
    def fetch(symbols, calendar=None, **kwargs):
        calls.append((symbols, calendar, kwargs))
        kwargs['telemetry'].update(status='complete', returned_count=1)
        return {'AAPL': {'us_live_data_available': False}}
    monkeypatch.setattr(us_free_report_data, 'fetch_free_report_daily', fetch)
    monkeypatch.setattr(us_delayed_market_data, 'fetch_free_us_quotes', fail)
    monkeypatch.setattr(live_api, 'fetch_us_sip_snapshots', fail)
    def deny(token):
        raise PermissionError('denied')
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', deny)
    live_api.LiveRequestHandler.do_POST(handler)
    assert sent[-1][0] == 403 and not calls
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: None)
    live_api.LiveRequestHandler.do_POST(handler)
    assert sent[-1][0] == 200 and len(calls) == 1
    assert calls[0][1] is calendar
    assert sent[-1][1]['private_free_diagnostics']['returned_count'] == 1
    assert 'private_daily_shadow_samples' not in sent[-1][1]


def test_internal_free_default_keeps_minute_path(monkeypatch):
    import live_api
    import us_free_report_data
    import us_delayed_market_data
    sent, calls = [], []
    payload = {'kind': 'sip', 'symbols': ['AAPL'], 'data_mode': 'free_delayed'}
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: None)
    monkeypatch.setattr(us_free_report_data, 'fetch_free_report_daily', fail)
    def fetch(symbols, **kwargs):
        calls.append(symbols)
        return {}
    monkeypatch.setattr(us_delayed_market_data, 'fetch_free_us_quotes', fetch)
    live_api.LiveRequestHandler.do_POST(handler_for(payload, sent, Calendar()))
    assert sent[-1][0] == 200 and calls == [['AAPL']]


def test_internal_paid_ignores_free_purpose(monkeypatch):
    import live_api
    import us_free_report_data
    import us_delayed_market_data
    sent, calls = [], []
    payload = {'kind': 'sip', 'symbols': ['AAPL'], 'purpose': PURPOSE}
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: None)
    monkeypatch.setattr(us_free_report_data, 'fetch_free_report_daily', fail)
    monkeypatch.setattr(us_delayed_market_data, 'fetch_free_us_quotes', fail)
    def snapshots(symbols, **kwargs):
        calls.append(symbols)
        return {'AAPL': {'us_live_data_available': True}}
    monkeypatch.setattr(live_api, 'fetch_us_sip_snapshots', snapshots)
    live_api.LiveRequestHandler.do_POST(handler_for(payload, sent, Calendar()))
    assert sent[-1][0] == 200 and calls == [['AAPL']]


def test_default_calendar_cannot_refresh_or_access_network(monkeypatch, tmp_path):
    import config
    import market_calendar
    adapter, calls = stub_sources(monkeypatch, daily_result())
    monkeypatch.setattr(config, 'SETTINGS', SimpleNamespace(reports_dir=tmp_path))
    constructed, calendar = [], Calendar()
    def local_calendar(path, **kwargs):
        constructed.append((path, kwargs))
        return calendar
    monkeypatch.setattr(market_calendar, 'OfficialMarketCalendar', local_calendar)
    assert adapter.fetch_free_report_daily(['AAPL'], observed_at=NOW)
    assert constructed == [(tmp_path / 'official_market_calendar.json',
                            {'auto_refresh': False, 'allow_network': False})]
    assert calls[0][2] is calendar


@pytest.mark.parametrize('observation,available', [
    ('2026-10-16T04:15:58+00:00', False),
    ('2026-10-16T04:15:59+00:00', True),
])
def test_complete_session_requires_full_delay_buffer(monkeypatch, observation, available):
    adapter, _ = stub_sources(monkeypatch, daily_result())
    result = adapter.fetch_free_report_daily(['AAPL'], Calendar(),
        observed_at=datetime.fromisoformat(observation))
    assert bool(result) is available


def test_naive_observation_rejected_before_source(monkeypatch):
    adapter, calls = stub_sources(monkeypatch, daily_result())
    with pytest.raises(ValueError):
        adapter.fetch_free_report_daily(['AAPL'], Calendar(), observed_at=datetime(2026, 10, 16))
    assert calls == []


def test_paid_direct_ignores_report_purpose(monkeypatch):
    import us_market_data
    import us_free_report_data
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid')
    monkeypatch.setattr(us_market_data, '_credentials', lambda: ('key', 'secret'))
    monkeypatch.setattr(us_free_report_data, 'fetch_free_report_daily', fail)
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {'AAPL': {'latestTrade': {'p': 110}}})
    output = us_market_data.fetch_us_sip_snapshots(['AAPL'],
        session=SimpleNamespace(get=get), observed_at=NOW, free_purpose=PURPOSE)
    assert output['AAPL']['us_live_data_available'] is True
    assert len(calls) == 1 and calls[0].endswith('/v2/stocks/snapshots')


def test_early_close_calendar_preserved(monkeypatch):
    adapter, _ = stub_sources(monkeypatch, daily_result())
    calendar = Calendar()
    calendar.relay_us_year = lambda year: {
        'year': year, 'status': 'verified_alpaca', 'sources': ['Alpaca Market Calendar'],
        'sessions': ['2026-10-15'],
        'session_details': {'2026-10-15': {'open': '09:30', 'close': '13:00'}}}
    row = adapter.fetch_free_report_daily(['AAPL'], calendar, observed_at=NOW)['AAPL']
    assert row['us_delayed_asof'] == '2026-10-15T13:00:00-04:00'
    assert row['us_delayed_session_close'] == row['us_delayed_asof']


def test_sip_reference_cache_precedes_yahoo_and_only_fills_missing(monkeypatch):
    import us_cached_daily_fallback
    adapter, calls = stub_sources(monkeypatch, daily_result(status='partial'))
    cached = {'us_delayed_source': 'Alpaca SIP completed daily report cache',
              'us_delayed_price': 10, 'us_delayed_cached': True}
    reference_calls = []
    def references(symbols, **kwargs):
        reference_calls.append(symbols)
        return {'MSFT': cached}
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily_references', references)
    result = adapter.fetch_free_report_daily(['AAPL', 'MSFT'], Calendar(), observed_at=NOW)
    assert result['AAPL']['us_delayed_price'] == 110
    assert result['MSFT'] == cached
    assert reference_calls == [['MSFT']]
    assert not any(call[0] == 'cache' for call in calls)


def test_calendar_attestation_failure_keeps_cache_fallback(monkeypatch):
    adapter, calls = stub_sources(monkeypatch, daily_result(),
        {'AAPL': {'us_delayed_source': 'Yahoo Finance completed daily report cache'}})
    calendar = Calendar()
    def unavailable(year):
        raise RuntimeError('calendar unavailable')
    calendar.relay_us_year = unavailable
    telemetry = {}
    result = adapter.fetch_free_report_daily(['AAPL'], calendar,
        observed_at=NOW, telemetry=telemetry)
    assert result['AAPL']['us_delayed_source'] == 'Yahoo Finance completed daily report cache'
    assert calls[-1][1] == ['AAPL']
    assert telemetry['daily_available_count'] == 0


@pytest.mark.parametrize('purpose', [PURPOSE, None])
def test_relay_failure_uses_report_reference_cache_only_for_report(monkeypatch, purpose):
    import us_market_data
    import us_cached_daily_fallback
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(us_market_data, '_credentials', lambda: None)
    monkeypatch.setattr(us_market_data, '_relay_request', lambda *a, **kw: {})
    calls, telemetry = [], {}
    def sip_cache(symbols, **kwargs):
        calls.append(('sip', symbols))
        return {'AAPL': {'us_delayed_source': 'Alpaca SIP completed daily report cache'}}
    def yahoo_cache(symbols, **kwargs):
        calls.append(('yahoo', symbols))
        return {s: {'us_delayed_source': 'Yahoo Finance completed daily report cache'} for s in symbols}
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily_references', sip_cache)
    monkeypatch.setattr(us_cached_daily_fallback, 'fetch_cached_us_daily', yahoo_cache)
    result = us_market_data.fetch_us_sip_snapshots(['AAPL', 'MSFT'], free_purpose=purpose,
        observed_at=NOW, free_diagnostics=telemetry)
    assert set(result) == {'AAPL', 'MSFT'}
    if purpose == PURPOSE:
        assert calls == [('sip', ['AAPL', 'MSFT']), ('yahoo', ['MSFT'])]
        assert result['AAPL']['us_delayed_source'] == 'Alpaca SIP completed daily report cache'
    else:
        assert calls == [('yahoo', ['AAPL', 'MSFT'])]
    assert telemetry['client_returned_count'] == 2
