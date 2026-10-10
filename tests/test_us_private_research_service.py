import io
import json
import hashlib
from datetime import datetime, timedelta, timezone, date
from email.message import Message
from types import SimpleNamespace
import pytest
import live_api
import us_private_research_service as private

NOW = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
DAYS = [(date(2026, 6, 1) + timedelta(days=i)).isoformat() for i in range(131)
        if (date(2026, 6, 1) + timedelta(days=i)).weekday() < 5]


class Calendar:
    def relay_us_year(self, year):
        if year != 2026:
            raise RuntimeError('no cached year')
        return {'year': year, 'status': 'verified_alpaca', 'sources': ['Alpaca Market Calendar'],
                'sessions': DAYS, 'session_details': {d: {'open': '09:30', 'close': '16:00'} for d in DAYS}}


@pytest.fixture
def service(tmp_path, monkeypatch):
    path = tmp_path / 'universe.json'
    path.write_text(json.dumps({'data': [{'symbol': 'AAPL', 'market': 'US', 'type': 'stock'},
                                        {'symbol': 'SPY', 'market': 'US', 'type': 'ETF'}]}))
    monkeypatch.setattr(private.history, '_credentials', lambda: ('fake-key', 'fake-secret'))
    client = SimpleNamespace(close=lambda: None)
    service = private.PrivateResearchService(path, session_factory=lambda: client)
    def fetch(symbols, sessions, bounded, credentials, deadline, counts):
        bounded.count += 1
        return {s: {d: (100., 102., 98., 100., 1000.) for d in sessions} for s in symbols}
    monkeypatch.setattr(private.history, '_fetch', fetch)
    return service


def test_private_projection_transient_and_non_actionable(service, caplog):
    result = service.research('AAPL', Calendar(), now=NOW)
    assert result['status'] == 'projected' and result['features'] and result['plan']
    assert result['decision_eligible'] is result['affects_formal'] is result['durable_retention'] is False
    assert result['provenance']['adjustment'] == 'split'
    assert result['request_count'] == 2
    assert not any(isinstance(v, dict) for v in vars(service).values())
    assert not caplog.records
    with pytest.raises(private.ResearchUnavailable, match='research_cooldown'):
        service.research('AAPL', Calendar(), now=NOW)


@pytest.mark.parametrize('symbol', ['../AAPL', 'AAPL,SPY', '2330.TW', 'UNKNOWN', ['AAPL'], ''])
def test_unknown_symbols_make_no_provider_request(service, symbol, monkeypatch):
    monkeypatch.setattr(private.history, '_fetch', lambda *a: pytest.fail('network must not run'))
    with pytest.raises(private.ResearchUnavailable):
        service.research(symbol, Calendar(), now=NOW)


def test_single_flight_and_request_budget(service):
    service._lock.acquire()
    try:
        with pytest.raises(private.ResearchUnavailable, match='research_busy'):
            service.research('AAPL', Calendar(), now=NOW)
    finally:
        service._lock.release()
    budget = private._RequestBudget(SimpleNamespace(get=lambda *a, **kw: None))
    budget.get('fixed'); budget.get('fixed')
    with pytest.raises(private.history.HistoryBlocked):
        budget.get('fixed')
    assert budget.count == 2


def test_hold_preserved_without_features(service, monkeypatch):
    def fetch(symbols, sessions, bounded, *args):
        bounded.count += 1
        return {s: {d: (100., 102., 98., 100., 1000.) for d in (sessions if len(sessions) == 4 else [])} for s in symbols}
    monkeypatch.setattr(private.history, '_fetch', fetch)
    result = service.research('AAPL', Calendar(), now=NOW)
    assert result['status'] == 'blocked'
    assert result['features'] is result['plan'] is result['provenance'] is None
    assert result['reasons'] == ['insufficient_contiguous_indicator_history']


def handler(headers=None):
    h = object.__new__(live_api.LiveRequestHandler)
    h.headers = Message()
    for key, value in (headers or {}).items():
        h.headers[key] = value
    h.path = '/api/private/us-research'
    h.device_pairing = SimpleNamespace(token_valid=lambda value: value == 'already-paired')
    h.large_buy_service = SimpleNamespace(weight_shadow=SimpleNamespace(calendar=Calendar()))
    h.rate_limiter = SimpleNamespace(allow=lambda: True)
    h._read_json = lambda: {'symbol': 'AAPL'}
    h.private_research_service = SimpleNamespace(research=lambda *a: {'decision_eligible': False})
    sent = []
    h._send = lambda status, body: sent.append((status, body))
    return h, sent


@pytest.mark.parametrize('headers,accepted', [({}, False), ({'X-Live-Token': 'wrong'}, False),
    ({'X-Live-Token': 'already-paired'}, True), ({'Authorization': 'Bearer owner-existing'}, True),
    ({'X-Site-Live-Token': 'shared'}, False)])
def test_private_auth_ignores_shared_and_public_fallback(monkeypatch, headers, accepted):
    monkeypatch.setenv('LIVE_ACCESS_TOKEN', 'owner-existing')
    monkeypatch.setenv('LIVE_PUBLIC_READ', '1')
    monkeypatch.setenv('LIVE_SITE_TOKEN_SHA256', hashlib.sha256(b'shared').hexdigest())
    h, _ = handler(headers)
    assert h._private_research_authorized() is accepted


def test_expired_or_tampered_device_rejected(monkeypatch):
    monkeypatch.setenv('LIVE_ACCESS_TOKEN', 'x' * 32)
    pairing = live_api.DevicePairingService(lambda _: pytest.fail('no pairing'), clock=lambda: 100)
    h, _ = handler({'X-Live-Token': 'wude-device-v1.99.nonce.invalid'})
    h.device_pairing = pairing
    assert h._private_research_authorized() is False


def test_route_auth_precedes_body_and_provider(monkeypatch):
    monkeypatch.setenv('LIVE_ACCESS_TOKEN', 'owner-existing')
    h, sent = handler()
    h._read_json = lambda: pytest.fail('unauthenticated body read')
    h.do_POST()
    assert sent[0][0] == 401


@pytest.mark.parametrize('patch,expected', [({'symbol': 'AAPL', 'raw': True}, 400),
                                           ({'symbol': 'AAPL', 'url': 'https://evil.invalid'}, 400)])
def test_private_route_rejects_extra_fields(monkeypatch, patch, expected):
    h, sent = handler({'X-Live-Token': 'already-paired', 'Origin': 'https://a0953883367.github.io'})
    h._read_json = lambda: patch
    h.do_POST()
    assert sent[0][0] == expected


def test_private_route_exception_no_values_logs_or_response(caplog):
    h, sent = handler({'X-Live-Token': 'already-paired', 'Origin': 'https://a0953883367.github.io'})
    def fail(*args):
        raise RuntimeError('SECRET_MARKET_VALUE_123456')
    h.private_research_service.research = fail
    h.do_POST()
    assert sent[0][0] == 503
    assert 'SECRET_MARKET' not in json.dumps(sent) + caplog.text


def test_bad_origin_and_busy_denied_without_values():
    h, sent = handler({'X-Live-Token': 'already-paired', 'Origin': 'https://evil.invalid'})
    h.do_POST()
    assert sent[0][0] == 403
    h, sent = handler({'X-Live-Token': 'already-paired', 'Origin': 'https://a0953883367.github.io'})
    def busy(*args):
        raise private.ResearchUnavailable('research_busy')
    h.private_research_service.research = busy
    h.do_POST()
    assert sent[0][0] == 429


def test_response_no_store_even_for_errors():
    h, _ = handler()
    headers = {}
    h.send_response = lambda code: None
    h.send_header = lambda key, value: headers.update({key: value})
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    live_api.LiveRequestHandler._send(h, 401, {'ok': False})
    assert headers['Cache-Control'] == 'no-store'


def test_private_auth_rejects_unproven_proxy_header_when_owner_missing(monkeypatch):
    monkeypatch.delenv('LIVE_ACCESS_TOKEN', raising=False)
    monkeypatch.setenv('LIVE_TRUSTED_AUTH_HEADER', 'X-Trusted-User')
    monkeypatch.setenv('LIVE_PUBLIC_READ', '1')
    h, _ = handler({'X-Trusted-User': 'attacker-claimed-owner'})
    h.device_pairing = SimpleNamespace(token_valid=lambda _: False)
    assert h._owner_authorized() is True
    assert h._private_research_authorized() is False


def test_close_failure_never_leaves_singleflight_locked(service):
    def failed_close():
        raise RuntimeError('close failure')
    service.session_factory = lambda: SimpleNamespace(close=failed_close)
    with pytest.raises(RuntimeError, match='close failure'):
        service.research('AAPL', Calendar(), now=NOW)
    assert service._lock.acquire(blocking=False)
    service._lock.release()


@pytest.mark.parametrize('headers', [{'Authorization': 'Bearer 非ASCII'}, {'X-Live-Token': '非ASCII'}])
def test_malformed_non_ascii_auth_fails_closed(monkeypatch, headers):
    monkeypatch.setenv('LIVE_ACCESS_TOKEN', 'owner-existing')
    h, sent = handler(headers)
    h.do_POST()
    assert sent[0][0] == 401


def test_private_route_query_is_rejected_and_never_logged(caplog):
    h, sent = handler({'X-Live-Token': 'already-paired'})
    h.path += '?SECRET_MARKET_TEXT=123456'
    h.do_POST()
    h.log_message('%s', 'POST ' + h.path)
    assert sent[0][0] == 400
    assert not caplog.records
    assert 'SECRET_MARKET_TEXT' not in json.dumps(sent)
