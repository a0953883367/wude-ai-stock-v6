from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
import json
import us_daily_shadow_history as history

NOW = datetime(2026, 10, 10, 1, tzinfo=timezone.utc)
SESSIONS = []
day = date(2026, 6, 1)
while day <= date(2026, 10, 9):
    if day.weekday() < 5:
        SESSIONS.append(day.isoformat())
    day += timedelta(days=1)


class Calendar:
    def relay_us_year(self, year):
        if year != 2026:
            raise RuntimeError('no cache')
        return {'year': year, 'status': 'verified_alpaca', 'sources': ['Alpaca Market Calendar'],
                'sessions': SESSIONS, 'session_details': {d: {'open': '09:30', 'close': '16:00'} for d in SESSIONS}}


def bar(day, value=100):
    stamp = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('America/New_York')).isoformat()
    return {'t': stamp, 'o': value, 'h': value + 1, 'l': value - 1, 'c': value, 'v': 1000}


class Client:
    def __init__(self, mode=None):
        self.calls = []
        self.mode = mode
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        params = kwargs['params']
        days = [d for d in SESSIONS if params['start'] <= d <= params['end'][:10]]
        rows = {s: [bar(d) for d in days] for s in params['symbols'].split(',')}
        status = self.mode if isinstance(self.mode, int) else 200
        payload = {'bars': rows, 'next_page_token': None}
        if self.mode == 'missing':
            rows['AAPL'].pop()
        elif self.mode == 'duplicate':
            rows['AAPL'].append(rows['AAPL'][0])
        elif self.mode == 'bad_price':
            rows['AAPL'][0]['c'] = float('nan')
        elif self.mode == 'pagination':
            payload['next_page_token'] = 'repeated'
        return SimpleNamespace(status_code=status, headers={'X-RateLimit-Limit': '200', 'X-RateLimit-Remaining': '199'},
                               iter_content=lambda chunk_size: (bytes([b]) for b in json.dumps(payload).encode()), close=lambda: None)


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setattr(history, '_credentials', lambda: ('existing-test-key', 'existing-test-secret'))


def run(client=None, symbols=None):
    return history.collect_daily_shadow_status(symbols or ['AAPL', 'MSFT'], Calendar(), now=NOW, session=client or Client(), collect_history=True)


def test_probe_before_universe_and_no_market_values_or_formal_effect():
    client = Client()
    result = run(client)
    assert result['status'] == 'computed_in_memory'
    assert result['history_complete_count'] == result['indicator_complete_count'] == 2
    assert result['session_date'] == '2026-10-08'
    assert result['probe_session_count'] == 4
    assert client.calls[0][1]['params']['symbols'] == 'AAPL'
    assert client.calls[0][1]['params']['start'] == '2026-10-05'
    assert client.calls[1][1]['params']['symbols'] == 'AAPL,MSFT'
    assert all(url == history.ENDPOINT for url, _ in client.calls)
    assert all(not kw['allow_redirects'] for _, kw in client.calls)
    assert all(kw['params']['feed'] == 'sip' and kw['params']['adjustment'] == 'split' for _, kw in client.calls)
    assert result['decision_eligible'] is result['durable_raw_retention'] is result['affects_formal'] is False
    assert not {'bars', 'ohlcv', 'indicators', 'close', 'ma20', 'credentials'} & result.keys()
    assert 'existing-test-secret' not in str(result)


@pytest.mark.parametrize('status,reason', [(401, 'provider_authentication_denied'), (403, 'provider_entitlement_denied'),
                                         (429, 'provider_rate_limited'), (302, 'provider_http_failure')])
def test_denied_provider_never_retry_upgrade_or_fetch_universe(status, reason):
    client = Client(status)
    result = run(client)
    assert result['reason'] == reason and result['provider_http_status'] == status
    assert result['probe_status'] == 'blocked' and len(client.calls) == 1


@pytest.mark.parametrize('mode,reason', [('missing', 'probe_incomplete'), ('duplicate', 'duplicate_provider_session'),
                                       ('bad_price', 'invalid_ohlcv'), ('pagination', 'duplicate_provider_session')])
def test_bad_history_fails_closed(mode, reason):
    assert run(Client(mode))['reason'] == reason


@pytest.mark.parametrize('symbols', [[], ['aapl'], ['AAPL', 'AAPL'], ['https://evil.test'], ['AAPL'] * 201, 'AAPL'])
def test_symbol_bounds(symbols):
    with pytest.raises(ValueError):
        history.collect_daily_shadow_status(symbols, Calendar(), now=NOW)


def test_missing_existing_credentials_never_requests(monkeypatch):
    monkeypatch.setattr(history, '_credentials', lambda: None)
    client = Client()
    assert run(client)['reason'] == 'existing_credentials_unavailable'
    assert not client.calls


def test_calendar_unverified_no_provider_call():
    calendar = SimpleNamespace(relay_us_year=lambda year: {'year': year, 'status': 'guessed_weekdays'})
    client = Client()
    assert history.collect_daily_shadow_status(['AAPL'], calendar, now=NOW, session=client)['reason'] == 'official_calendar_unverified'
    assert not client.calls


def test_relay_authorizes_before_daily_operation(monkeypatch):
    import live_api
    responses = []
    handler = SimpleNamespace(path='/api/internal/market-data', rate_limiter=SimpleNamespace(allow=lambda: True),
        headers={'Authorization': 'Bearer fixture'}, _read_json=lambda: {'kind': 'daily_shadow_status', 'symbols': ['AAPL']},
        _send=lambda code, payload: responses.append((code, payload)),
        large_buy_service=SimpleNamespace(weight_shadow=SimpleNamespace(calendar=Calendar())))
    monkeypatch.setattr(history, 'collect_daily_shadow_status', lambda symbols, calendar, **kwargs: {'status': 'computed_in_memory'})
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: {})
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 200
    def denied(token):
        raise PermissionError('denied')
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', denied)
    handler._read_json = lambda: pytest.fail('must authorize before parse/provider')
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 403


def test_default_invocation_probes_four_sessions_only():
    client = Client()
    result = history.collect_daily_shadow_status(['AAPL'], Calendar(), now=NOW, session=client)
    assert result['status'] == 'probe_verified'
    assert len(client.calls) == 1 and result['indicator_complete_count'] == 0
    assert result['next_stage'] == 'bounded_history_collection_not_started'


def test_response_byte_budget(monkeypatch):
    monkeypatch.setattr(history, 'MAX_RESPONSE_BYTES', 12)
    assert run()['reason'] == 'response_byte_budget'


def test_deadline_after_response_cannot_claim_computed(monkeypatch):
    calls = iter([0, 0, 0, 76, 76])
    monkeypatch.setattr(history.clock, 'monotonic', lambda: next(calls))
    assert run()['reason'] == 'request_time_budget'


def test_publication_strict_schema_rejects_arbitrary_scalars():
    from tools.collect_us_daily_shadow_status import sanitize_status
    result = run()
    clean = sanitize_status(result, 2)
    assert clean['status'] == 'computed_in_memory'
    assert clean['blocked_reasons'] == {}
    for key, value in [('source', 'price 123.45'), ('reason', 'secret 123'),
                       ('indicator_complete_count', True), ('history_complete_count', float('nan')),
                       ('requested_count', 188), ('request_count', -1)]:
        bad = sanitize_status({**result, key: value}, 2)
        assert bad['reason'] == 'invalid_or_unavailable_relay_status'
        assert '123' not in str(bad)


def test_publication_malformed_enum_and_false_invariants_fail_closed():
    from tools.collect_us_daily_shadow_status import sanitize_status
    result = run()
    for patch in ({'status': []}, {'reason': []}, {'probe_status': []}, {'durable_raw_retention': True}, {'request_count': 0}):
        assert sanitize_status({**result, **patch}, 2)['reason'] == 'invalid_or_unavailable_relay_status'
    for key in ('observed_at', 'session_date', 'calendar_session_count'):
        bad = dict(result)
        del bad[key]
        assert sanitize_status(bad, 2)['reason'] == 'invalid_or_unavailable_relay_status'


def test_health_capability_never_claims_entitlement_or_retention():
    import live_api
    status = live_api.LiveDataService.health(SimpleNamespace())
    assert status['us_daily_shadow_probe'] == {
        'version': history.VERSION, 'supported': True,
        'durable_raw_retention': False, 'entitlement_verified': False,
    }


def test_recent_listing_counts_are_distinct_and_public_safe(monkeypatch):
    from tools.collect_us_daily_shadow_status import sanitize_status
    original = history._fetch
    def listing(symbols, sessions, *args):
        result = original(symbols, sessions, *args)
        if len(sessions) > 4:
            return {s: {d: bars[d] for d in sessions[-60:]} for s, bars in result.items()}
        return result
    monkeypatch.setattr(history, '_fetch', listing)
    result = run()
    assert result['history_complete_count'] == 0
    assert result['indicator_complete_count'] == result['research_plan_complete_count'] == 2
    assert result['coverage_notes']['requested_window_incomplete'] == 2
    clean = sanitize_status(result, 2)
    assert clean['status'] == 'computed_in_memory'
    assert clean['history_complete_count'] == 0 and clean['indicator_complete_count'] == 2
    banned = {'features', 'plan', 'provenance', 'entry_low', 'entry_high', 'stop', 'target1', 'target2', 'close', 'ma60', 'bars', 'ohlcv'}
    def check(value):
        if isinstance(value, dict):
            assert not set(value) & banned
            for item in value.values(): check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(clean)


def test_split_adjusted_fractional_volume_is_not_rounded():
    day = '2026-10-08'
    raw = bar(day)
    raw['v'] = 10.25
    assert history._normalize(raw, {day})[1][-1] == 10.25


def test_projection_request_does_not_write_files(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('private collector attempted filesystem access')
    monkeypatch.setattr('builtins.open', denied)
    result = run()
    assert result['research_plan_complete_count'] == 2


def test_first_week_calendar_membership_not_clipped_to_history_cutoff():
    from datetime import date, timedelta
    observed = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)
    start = observed.astimezone(history.NY).date() - timedelta(days=history.MAX_DAYS)
    monday = start - timedelta(days=start.weekday())
    assert start.weekday() != 0
    class FullCalendar:
        def relay_us_year(self, year):
            days = []
            d = date(year, 1, 1)
            while d.year == year:
                if d.weekday() < 5: days.append(d.isoformat())
                d += timedelta(days=1)
            return {'year': year, 'status': 'verified_alpaca', 'sources': ['Alpaca Market Calendar'],
                    'sessions': days, 'session_details': {d: {'open': '09:30', 'close': '16:00'} for d in days}}
    weeks = {}
    chosen = history._sessions(FullCalendar(), observed, verified_weeks=weeks)
    assert monday.isoformat() in weeks[monday.isoformat()]
    assert monday.isoformat() not in chosen
    assert len(weeks[monday.isoformat()]) == 5


def test_188_symbol_paginated_synthetic_projection_no_network():
    class Paginated:
        def get(self, url, **kw):
            params = kw['params']
            days = [d for d in SESSIONS if params['start'] <= d <= params['end'][:10]]
            pairs = [(s, d) for s in params['symbols'].split(',') for d in days]
            first = int(params.get('page_token', 0))
            rows = {}
            for symbol, day in pairs[first:first + 10000]:
                rows.setdefault(symbol, []).append(bar(day))
            token = str(first + 10000) if len(pairs) > first + 10000 else None
            body = json.dumps({'bars': rows, 'next_page_token': token}).encode()
            return SimpleNamespace(status_code=200, headers={},
                                   iter_content=lambda chunk_size: (bytes([b]) for b in body), close=lambda: None)
    result = history.collect_daily_shadow_status([f'TEST{i}' for i in range(188)], Calendar(), now=NOW,
                                                session=Paginated(), collect_history=True)
    assert result['status'] == 'computed_in_memory'
    assert result['history_complete_count'] == result['indicator_complete_count'] == 188
    assert result['research_plan_complete_count'] == result['daily_momentum_complete_count'] == 188
    assert result['weekly_indicator_complete_count'] == 188
    assert result['request_count'] == 3
    assert result['corporate_actions_independently_verified'] is False
    assert result['durable_raw_retention'] is result['market_values_exported'] is False


def test_manifest_includes_153_stocks_and_35_etfs_and_is_order_stable():
    from tools.collect_us_daily_shadow_status import build_manifest, sanitize_status
    rows = [{'symbol': f'STOCK{i}', 'market': 'US', 'type': '股票'} for i in range(153)]
    rows += [{'symbol': f'FUND{i}', 'market': 'US', 'type': 'ETF'} for i in range(35)]
    rows += [{'symbol': '2330', 'market': 'TW', 'type': '股票'}]
    symbols, categories, digest = build_manifest(rows)
    assert len(symbols) == 188
    assert list(categories.values()).count('stock') == 153
    assert list(categories.values()).count('etf') == 35
    assert build_manifest(list(reversed(rows)))[2] == digest
    result = history.collect_daily_shadow_status(symbols, Calendar(), now=NOW,
                                                 session=Client(), instrument_types=categories)
    clean = sanitize_status(result, 188, expected_manifest=digest)
    assert clean['status'] == 'probe_verified'
    assert type(clean['elapsed_ms']) is int and clean['elapsed_ms'] >= 0
    assert (clean['stock_count'], clean['etf_count'], clean['unclassified_count']) == (153, 35, 0)
    assert sanitize_status(result, 188, expected_manifest='0' * 64)['status'] == 'blocked'
    assert sanitize_status({**result, 'stock_count': 154}, 188)['status'] == 'blocked'
    assert sanitize_status({**result, 'stock_count': 154, 'etf_count': 34}, 188,
                           expected_manifest=digest, expected_categories=categories)['status'] == 'blocked'
    assert sanitize_status({**result, 'universe_manifest_sha256': 'private text'}, 188)['status'] == 'blocked'


def test_manifest_rejects_invalid_and_conflicting_categories():
    from tools.collect_us_daily_shadow_status import build_manifest
    for rows in ([], [{'symbol': '../AAPL', 'market': 'US'}],
                 [{'symbol': 'AAPL', 'market': 'US'}, {'symbol': 'AAPL', 'market': 'US', 'type': 'ETF'}],
                 [{'symbol': f'TEST{i}', 'market': 'US'} for i in range(201)]):
        with pytest.raises(ValueError):
            build_manifest(rows)
    assert build_manifest([], probe_only=True)[:2] == (['AAPL'], {'AAPL': 'stock'})
    with pytest.raises(ValueError):
        history.collect_daily_shadow_status(['AAPL'], Calendar(), now=NOW,
                                             session=Client(), instrument_types={'AAPL': 'fundamentals'})


def test_quality_diagnostics_are_allowlisted_operational_metadata(monkeypatch):
    from tools.collect_us_daily_shadow_status import sanitize_status
    original = history._fetch
    def anomalous(symbols, sessions, *args):
        result = original(symbols, sessions, *args)
        if len(sessions) > 4:
            result['AAPL'][sessions[-2]] = (200, 201, 199, 200, 1000)
            result['MSFT'] = {sessions[-1]: result['MSFT'][sessions[-1]]}
        return result
    monkeypatch.setattr(history, '_fetch', anomalous)
    result = run()
    categories = {'AAPL': 'unknown', 'MSFT': 'unknown'}
    clean = sanitize_status(result, 2, expected_categories=categories)
    assert clean['status'] == 'partial_in_memory'
    assert clean['quality_diagnostics'] == [
        {'symbol': 'AAPL', 'reason': 'source_continuity_review', 'sessions': SESSIONS[-3:-1]},
        {'symbol': 'MSFT', 'reason': 'history_window_insufficient', 'sessions': SESSIONS[-3:-1]}]
    import copy
    def rejected(items):
        bad = copy.deepcopy(result)
        bad['quality_diagnostics'] = items
        assert sanitize_status(bad, 2, expected_categories=categories)['status'] == 'blocked'
    good = clean['quality_diagnostics']
    for patch in ({'price': 100}, {'reason': 'up'}, {'reason': '50percent'},
                  {'symbol': 'UNREQUESTED'}, {'sessions': ['2026-10-11']},
                  {'sessions': ['2026-10-09T00:00:00']}, {'sessions': [123]},
                  {'sessions': ['2020-01-01']}, {'sessions': []},
                  {'sessions': ['2026-10-09', '2026-10-09']}):
        rejected([{**good[0], **patch}, good[1]])
    rejected([good[0], good[0]])
    rejected([good[0]])
    rejected('provider error raw payload')
    assert sanitize_status(result, 2)['status'] == 'blocked'


@pytest.mark.parametrize('mode', ['empty', 'missing_latest', 'short59', 'one_bar', 'many_transitions'])
def test_quality_diagnostic_boundaries(mode):
    from us_private_shadow_projection import project_symbol
    days = SESSIONS[:-1]
    bars = {day: (100., 101., 99., 100., 1000.) for day in days}
    if mode == 'empty':
        bars = {}
    elif mode == 'missing_latest':
        bars.pop(days[-1])
    elif mode == 'short59':
        bars = {day: bars[day] for day in days[-59:]}
    elif mode == 'one_bar':
        bars = {days[-1]: bars[days[-1]]}
    else:
        bars = {day: (v, v + 1, v - 1, v, 1000.)
                for i, day in enumerate(days) for v in [100. if i % 2 else 200.]}
    result = project_symbol('AAPL', bars, days, observed=NOW, adjustment='split')
    assert result.status == 'blocked'
    assert 1 <= len(result.quality_sessions) <= 16
    assert list(result.quality_sessions) == sorted(set(result.quality_sessions))
    assert set(result.quality_sessions) <= set(days)
    if mode == 'many_transitions':
        assert result.quality_sessions == tuple(days[1:17])
    if mode in {'empty', 'missing_latest'}:
        assert result.quality_sessions == (days[-1],)
