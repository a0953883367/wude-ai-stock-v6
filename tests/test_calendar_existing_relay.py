import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import live_api
from market_calendar import OfficialMarketCalendar, us_calendar_refresh_available
from us_direction_agent import inspect_us_direction
from zoneinfo import ZoneInfo


def calendar_row():
    return {'year': 2026, 'status': 'verified_alpaca', 'sources': ['Alpaca Market Calendar'],
            'sessions': ['2026-10-08', '2026-10-09', '2026-10-12'],
            'session_details': {day: {'open': '09:30', 'close': close} for day, close in
                                [('2026-10-08', '16:00'), ('2026-10-09', '13:00'), ('2026-10-12', '16:00')]},
            'fetched_at': '2026-10-09T10:59:43+00:00', 'private_key': 'must-never-leave'}


def calendar(root):
    path = root / 'official_market_calendar.json'
    path.write_text(json.dumps({'version': 1, 'markets': {'TW': {'years': {}}, 'US': {'years': {'2026': calendar_row()}}}}))
    return OfficialMarketCalendar(path, auto_refresh=False, allow_network=False,
        clock=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp())


def test_verified_cached_calendar_relay_is_bounded_and_sanitized(tmp_path):
    cal = calendar(tmp_path)
    exported = cal.relay_us_year(2026)
    assert exported['session_details']['2026-10-09']['close'] == '13:00'
    assert 'private_key' not in exported
    exported['sessions'].append('tampered')
    assert 'tampered' not in cal.relay_us_year(2026)['sessions']
    for year in [True, '2026', 1900, 2030]:
        with pytest.raises(ValueError):
            cal.relay_us_year(year)
    with pytest.raises(RuntimeError):
        cal.relay_us_year(2025)


def test_calendar_kind_requires_same_existing_oidc_before_reading_cache(tmp_path, monkeypatch):
    cal = calendar(tmp_path)
    responses = []
    handler = SimpleNamespace(path='/api/internal/market-data',
        rate_limiter=SimpleNamespace(allow=lambda: True), headers={'Authorization': 'Bearer valid-test'},
        _read_json=lambda: {'kind': 'calendar', 'year': 2026},
        _send=lambda code, payload: responses.append((code, payload)),
        large_buy_service=SimpleNamespace(weight_shadow=SimpleNamespace(calendar=cal)))
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', lambda token: {'repository': 'test'})
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 200
    assert responses[-1][1]['data']['status'] == 'verified_alpaca'
    assert 'private_key' not in responses[-1][1]['data']
    def deny(token):
        raise PermissionError('not allowed')
    monkeypatch.setattr(live_api, 'verify_github_oidc_token', deny)
    handler._read_json = lambda: pytest.fail('Unauthorized request reached payload/cache')
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 403


def test_github_fetches_calendar_over_existing_oidc_without_alpaca_secrets(tmp_path, monkeypatch):
    import us_market_data
    monkeypatch.delenv('ALPACA_API_KEY_ID', raising=False)
    monkeypatch.delenv('ALPACA_API_SECRET_KEY', raising=False)
    monkeypatch.setenv('WUDE_LIVE_API_BASE', 'https://live.example')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://identity.example')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'test-runtime')
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    monkeypatch.setattr(us_market_data.requests, 'get', lambda *a, **k: Response({'value': 'test-signed-oidc'}))
    posted = []
    def post(url, **kwargs):
        posted.append((url, kwargs))
        return Response({'ok': True, 'data': calendar_row()})
    monkeypatch.setattr(us_market_data.requests, 'post', post)
    cal = OfficialMarketCalendar(tmp_path / 'empty.json', auto_refresh=False, allow_network=False)
    row = cal._fetch_us(2026)
    assert us_calendar_refresh_available() is True
    assert row['transport'] == 'github_oidc_existing_railway'
    assert row['session_details']['2026-10-09']['early_close'] is True
    assert 'private_key' not in row
    assert posted[0][1]['json'] == {'kind': 'calendar', 'year': 2026}
    assert posted[0][1]['headers'] == {'Authorization': 'Bearer test-signed-oidc'}


def test_unverified_relay_never_becomes_official_calendar(tmp_path, monkeypatch):
    monkeypatch.delenv('ALPACA_API_KEY_ID', raising=False)
    monkeypatch.delenv('ALPACA_API_SECRET_KEY', raising=False)
    monkeypatch.setattr('us_market_data._relay_request', lambda *a: {'year': 2026, 'status': 'guessed_weekdays'})
    cal = OfficialMarketCalendar(tmp_path / 'empty.json', auto_refresh=False, allow_network=False)
    with pytest.raises(RuntimeError):
        cal._fetch_us(2026)
    assert cal.session_status('US', '2026-10-09')['available'] is False


def test_verified_existing_relay_allows_one_bootstrap_with_old_failure_preserved(tmp_path, monkeypatch):
    import us_direction_agent
    monkeypatch.setattr(us_direction_agent, '_calendar_relay_ready', lambda: True)
    (tmp_path / 'all_analysis.json').write_text(json.dumps({'data': [
        {'market': 'US', 'symbol': 'NVDA', 'type': '個股', 'official_session_date': '2026-10-09', 'market_contract_valid': True}]}))
    original = {'calendar_credentials_available': False, 'calendar_refresh_attempts': [], 'blocked_history': [{'reason': 'official_calendar_unavailable'}]}
    path = tmp_path / 'us_direction_progress.json'
    path.write_text(json.dumps(original))
    result = inspect_us_direction(tmp_path, datetime(2026, 10, 9, 23, tzinfo=ZoneInfo('Asia/Taipei')))
    assert result['needs_silent_refresh'] is True
    assert result['verified_recovered'] is False
    assert json.loads(path.read_text()) == original
