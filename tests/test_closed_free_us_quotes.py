from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import live_api
from live_api import LiveDataService, LiveRequestHandler
from tests.test_us_cached_sip_references import reference


def request(monkeypatch, service, *, mode='free_delayed', market='US', symbol='NVDA', authorized=True, allowed=True):
    monkeypatch.setenv('ALPACA_DATA_MODE', mode)
    monkeypatch.setattr(live_api, 'market_live_window', lambda market: False)
    result = []
    limiter = Mock()
    limiter.allow.return_value = allowed
    handler = SimpleNamespace(path=f'/api/live?symbol={symbol}&market={market}',
        _authorized=Mock(return_value=authorized), rate_limiter=limiter, service=service,
        _send=lambda status, payload: result.append((int(status), payload)))
    LiveRequestHandler.do_GET(handler)
    return result[0], handler


def service():
    return LiveDataService(us_fetcher=Mock(return_value={'NVDA': reference('NVDA')}),
                           option_fetcher=Mock(side_effect=AssertionError('no options')),
                           fubon_login=Mock(side_effect=AssertionError('no Taiwan fetch')))


@pytest.mark.parametrize('mode', ['free_delayed', ' FREE_DELAYED '])
def test_closed_free_us_returns_only_delayed_quote(monkeypatch, mode):
    data = service()
    (status, payload), handler = request(monkeypatch, data, mode=mode)
    assert status == 200
    assert payload['quote_status'] == 'delayed' and payload['realtime'] is False
    assert payload['options'] == {}
    assert payload['quote']['us_live_data_available'] is False
    assert payload['quote']['bids'] == payload['quote']['asks'] == []
    assert payload['quote']['orderBookComplete'] is False
    data.option_fetcher.assert_not_called()
    handler._authorized.assert_called_once()
    handler.rate_limiter.allow.assert_called_once()


@pytest.mark.parametrize('mode,market', [('paid_live', 'US'), ('', 'US'), ('invalid', 'US'),
                                         ('free_delayed', 'TW'), ('paid_live', 'TW')])
def test_closed_paid_us_and_all_tw_stay_blocked(monkeypatch, mode, market):
    data = Mock()
    (status, payload), _ = request(monkeypatch, data, mode=mode, market=market)
    assert status == 425 and payload['code'] == 'MARKET_CLOSED'
    data.fetch.assert_not_called()


@pytest.mark.parametrize('authorized,allowed,status', [(False, True, 401), (True, False, 429)])
def test_free_closed_access_preserves_auth_and_rate_order(monkeypatch, authorized, allowed, status):
    data = Mock()
    (actual, _), handler = request(monkeypatch, data, authorized=authorized, allowed=allowed)
    assert actual == status
    data.fetch.assert_not_called()
    assert handler.rate_limiter.allow.call_count == int(authorized)


def test_free_closed_access_still_validates_symbol(monkeypatch):
    data = service()
    (status, _), _ = request(monkeypatch, data, symbol='INVALID!')
    assert status == 400
    data.us_fetcher.assert_not_called()


def test_paid_cache_cannot_cross_into_free_mode_and_same_mode_cache_works(monkeypatch):
    data = service()
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid_live')
    data.us_fetcher.return_value = {'NVDA': {'us_live_data_available': True, 'us_live_price': 200}}
    paid = data.fetch('NVDA', 'US', include_options=False)
    assert paid['quote']['us_live_data_available'] is True
    data.us_fetcher.return_value = {'NVDA': reference('NVDA')}
    (status, free), _ = request(monkeypatch, data)
    assert status == 200 and free['realtime'] is False and free['cached'] is False
    assert data.us_fetcher.call_count == 2
    again = data.fetch('NVDA', 'US')
    assert again['cached'] is True and again['realtime'] is False
    assert data.us_fetcher.call_count == 2


def test_paid_provider_response_cannot_be_served_as_free_history(monkeypatch):
    data = service()
    data.us_fetcher.return_value = {'NVDA': {'us_live_data_available': True, 'us_live_price': 200}}
    (status, payload), _ = request(monkeypatch, data)
    assert status == 503 and payload['ok'] is False
    data.option_fetcher.assert_not_called()


def test_free_cache_cannot_cross_back_to_paid_mode(monkeypatch):
    data = service()
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    assert data.fetch('NVDA', 'US')['realtime'] is False
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid_live')
    data.us_fetcher.return_value = {'NVDA': {'us_live_data_available': True, 'us_live_price': 200}}
    paid = data.fetch('NVDA', 'US', include_options=False)
    assert paid['quote']['us_live_data_available'] is True and paid['cached'] is False
    assert data.us_fetcher.call_count == 2
