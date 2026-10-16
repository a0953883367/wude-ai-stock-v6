import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from us_cached_daily_fallback import fetch_cached_us_daily

NOW = datetime.fromisoformat('2026-10-16T15:30:00+00:00')


def row(symbol='HNHPF', day='2026-10-15', **changes):
    return dict(symbol=symbol, market='US', us_daily_source='Yahoo Finance daily bars',
                us_daily_price_available=True, source_daily_ohlcv_complete=True,
                us_daily_session_date=day, source_daily_ohlcv_session_date=day,
                official_session_date=day, official_open_price=10, official_high_price=12,
                official_low_price=9, official_close_price=11, official_volume=1000, **changes)


def fetch(tmp_path, rows, symbols=('HNHPF',), now=NOW):
    path = tmp_path / 'all_analysis.json'
    path.write_text(json.dumps({'data': rows}))
    return fetch_cached_us_daily(symbols, observed_at=now, report_path=path)


def test_valid_schema_and_isolation(tmp_path):
    data = fetch(tmp_path, [row()])['HNHPF']
    assert data['us_delayed_price'] == 11
    assert data['us_delayed_asof'] == '2026-10-15T04:00:00+00:00'
    assert data['us_delayed_interval'] == '1Day'
    assert data['us_delayed_coverage'] == 'provider_defined'
    assert data['us_delayed_cached'] is True
    assert data['us_live_data_available'] is False
    assert data['us_delayed_minimum_seconds'] == 960
    assert data['us_delayed_source'] == 'Yahoo Finance completed daily report cache'
    assert set(data['us_delayed_bars'][0]) == {'t', 'o', 'h', 'l', 'c', 'v'}
    assert not any(k in data for k in ('score', 'price', 'adjustment', 'feed'))


@pytest.mark.parametrize('field,value', [
    ('market', 'TW'), ('us_daily_source', 'Alpaca SIP'),
    ('us_daily_price_available', 1), ('source_daily_ohlcv_complete', False),
    ('official_session_date', '2026-10-14'), ('source_daily_ohlcv_session_date', None),
    ('official_open_price', 0), ('official_close_price', True),
    ('official_high_price', 10), ('official_low_price', 12),
    ('official_volume', -1), ('official_volume', '100'),
    ('official_close_price', float('nan')), ('official_volume', float('inf')),
    ('official_open_price', None), ('symbol', 'HNHPF.TW'),
])
def test_invalid_rows_stay_missing(tmp_path, field, value):
    item = row()
    item[field] = value
    assert fetch(tmp_path, [item]) == {}


@pytest.mark.parametrize('day', ['2026-10-16', '2026-10-17', '2026-10-08', '2026-13-15', '20261015'])
def test_date_rejections(tmp_path, day):
    assert fetch(tmp_path, [row(day=day)]) == {}


def test_seven_days_and_ny_boundary(tmp_path):
    assert fetch(tmp_path, [row(day='2026-10-09')])
    assert not fetch(tmp_path, [row()], now=datetime.fromisoformat('2026-10-16T04:15:59+00:00'))
    assert fetch(tmp_path, [row()], now=datetime.fromisoformat('2026-10-16T04:16:00+00:00'))
    assert not fetch(tmp_path, [row()], now=datetime.fromisoformat('2026-10-16T03:59:00+00:00'))
    assert not fetch(tmp_path, [row()], now=datetime(2026, 10, 16))


def test_duplicates_cross_market_and_missing(tmp_path):
    other = row()
    other['market'] = 'TW'
    assert fetch(tmp_path, [row(), row()]) == {}
    assert fetch(tmp_path, [row(), other]) == {}
    assert fetch(tmp_path, [None, {}, row('AAPL')]) == {}
    assert fetch(tmp_path, [row()], symbols=['HNHPF', 'HNHPF', 'MISSING']).keys() == {'HNHPF'}


def test_universe_and_no_unrequested_symbols(tmp_path):
    symbols = [f'TEST{i}' for i in range(188)]
    result = fetch(tmp_path, [row(s) for s in symbols] + [row('EXTRA')], symbols=symbols)
    assert set(result) == set(symbols)


def test_default_path_and_read_failures(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'config', SimpleNamespace(SETTINGS=SimpleNamespace(reports_dir=tmp_path)))
    assert fetch_cached_us_daily(['HNHPF'], NOW) == {}
    path = tmp_path / 'all_analysis.json'
    path.write_text(json.dumps({'data': [row()]}))
    assert fetch_cached_us_daily(['HNHPF'], NOW)
    for raw in ('broken', '[]', '{"data": {}}', 'x' * (8 * 1024 * 1024 + 1)):
        path.write_text(raw)
        assert fetch_cached_us_daily(['HNHPF'], NOW) == {}
    assert fetch_cached_us_daily('HNHPF', NOW, path) == {}
