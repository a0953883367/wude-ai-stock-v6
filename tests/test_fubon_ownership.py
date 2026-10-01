from datetime import datetime, timedelta
from types import SimpleNamespace
import json
import pytest

from config import TAIPEI
from fubon_ownership import collect_ownership

NOW = datetime(2026, 10, 1, 20, tzinfo=TAIPEI)
POOL = [{"symbol": "2330.TW", "type": "個股"}, {"symbol": "00662.TW", "type": "ETF"}, {"symbol": "AAPL"}]


def test_relay_service_reuses_login_and_rejects_non_pool_symbols(tmp_path, monkeypatch):
    import live_api
    calls = []
    sdk = SimpleNamespace(marketdata=SimpleNamespace(rest_client=SimpleNamespace(stock=object())))
    service = live_api.LiveDataService(fubon_login=lambda: calls.append('login') or sdk)
    monkeypatch.setattr(live_api, 'configure_fubon_certificate', lambda: None)
    monkeypatch.setattr(live_api, 'load_watchlist', lambda: POOL)
    monkeypatch.setattr(live_api, '_runtime_state_path', lambda name: tmp_path / name)
    def collect(rest, pool, path):
        assert rest is sdk.marketdata.rest_client.stock
        calls.append(pool[0]['symbol'])
        return {'data': {pool[0]['symbol']: {'institutional_trades': {'status': 'available'}}}}
    monkeypatch.setattr(live_api, 'collect_ownership', collect)
    assert service.ownership(['2330.TW', '2330.TW'])['2330.TW']
    service.ownership(['00662.TW'])
    assert calls == ['login', '2330.TW', '00662.TW']
    for symbols in [['AAPL'], ['2330.TW'] * 6, None, ['../2330.TW']]:
        with pytest.raises(ValueError):
            service.ownership(symbols)
    assert len(calls) == 3


def test_relay_failure_stops_requests_and_preserves_history(tmp_path, monkeypatch):
    import fubon_ownership_relay as relay
    (tmp_path / 'reports').mkdir()
    path = tmp_path / 'reports' / 'fubon_ownership.json'
    path.write_text(json.dumps({'data': {'2330.TW': {'institutional_trades':
                    {'status': 'available', 'rows': [{'date': '2026-09-30', 'total': 7}]}}}}))
    monkeypatch.setattr(relay, 'ROOT', tmp_path)
    monkeypatch.setattr(relay, 'load_watchlist', lambda: [{'symbol': f'{i}.TW'} for i in range(10)] + POOL)
    calls = []
    monkeypatch.setattr(relay, '_relay_request', lambda *a, **kw: calls.append(a) or {})
    relay.refresh()
    report = json.loads(path.read_text())
    assert len(calls) == 1
    assert report['available_count'] == 0
    row = report['data']['2330.TW']['institutional_trades']
    assert row['status'] == 'relay_unavailable'
    assert row['rows'][0]['total'] == 7
    assert 'AAPL' not in report['data']


def client(fn):
    return SimpleNamespace(ownership=SimpleNamespace(**{key: fn for key in
        ("institutional_trades", "tdcc_distribution", "director_holdings")}))


def test_calls_cache_and_etf_exclusion(tmp_path):
    calls = []
    def fetch(**params):
        calls.append(params)
        return {"symbol": params["symbol"], "data": [{"date": "2026-09-30", "total": 0}]}
    path = tmp_path / "report.json"
    report = collect_ownership(client(fetch), POOL, path, now=NOW, sleep=lambda _: None)
    assert len(calls) == 5
    assert report["available_count"] == 5
    assert report["data"]["00662.TW"]["director_holdings"]["status"] == "not_applicable"
    assert calls[0]["from"] == "2026-04-04"
    assert report["data"]["2330.TW"]["institutional_trades"]["rows"][0]["total"] == 0
    collect_ownership(client(fetch), POOL, path, now=NOW, sleep=lambda _: None)
    assert len(calls) == 5


def test_empty_is_missing_and_old_sdk_is_explicit(tmp_path):
    report = collect_ownership(client(lambda **p: {"symbol": p["symbol"], "data": []}), POOL,
                               tmp_path / "empty.json", now=NOW, sleep=lambda _: None)
    assert report["available_count"] == 0
    assert report["data"]["2330.TW"]["institutional_trades"]["status"] == "no_data"
    report = collect_ownership(SimpleNamespace(), POOL, tmp_path / "old.json", now=NOW)
    assert report["data"]["2330.TW"]["institutional_trades"]["status"] == "sdk_upgrade_required"


def test_rate_limit_stops_batch_preserves_old_data_and_redacts(tmp_path):
    path = tmp_path / "report.json"
    collect_ownership(client(lambda **p: {"symbol": p["symbol"], "data": [{"date": "2026-09-29"}]}),
                      POOL, path, now=NOW-timedelta(days=2), sleep=lambda _: None)
    calls = []
    class RateLimit(Exception):
        status_code = 429
    def fail(**params):
        calls.append(params)
        raise RateLimit("secret-token")
    result = collect_ownership(client(fail), POOL, path, now=NOW, sleep=lambda _: None)
    assert len(calls) == 1
    entry = result["data"]["2330.TW"]["institutional_trades"]
    assert entry["status"] == "rate_limited"
    assert entry["data_date"] == "2026-09-29"
    assert "secret-token" not in path.read_text()
    assert result["available_count"] == 0


def test_wrong_symbol_future_date_and_invalid_schema_are_rejected(tmp_path):
    for payload in [{"symbol": "9999", "data": []},
                    {"symbol": "2330", "data": [{"date": "2026-10-02"}]},
                    {"symbol": "2330", "data": [{"date": "invalid"}]}]:
        result = collect_ownership(client(lambda **p: payload), POOL[:1], tmp_path / "bad.json",
                                  now=NOW, sleep=lambda _: None)
        assert result["available_count"] == 0
        assert result["data"]["2330.TW"]["institutional_trades"]["status"] == "fetch_error"
