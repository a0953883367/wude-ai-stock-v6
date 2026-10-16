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
    path.write_text(json.dumps({'updated_at': '2026-10-16 04:16:00', 'data': rows}))
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
    path.write_text(json.dumps({'updated_at': '2026-10-16 04:16:00', 'data': [row()]}))
    assert fetch_cached_us_daily(['HNHPF'], NOW)
    for raw in ('broken', '[]', '{"data": {}}', 'x' * (8 * 1024 * 1024 + 1)):
        path.write_text(raw)
        assert fetch_cached_us_daily(['HNHPF'], NOW) == {}
    assert fetch_cached_us_daily('HNHPF', NOW, path) == {}


def save_report(tmp_path, name, rows, key='watchlist'):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'updated_at': '2026-10-16 04:16:00', key: rows}))
    return path


def test_intraday_latest_keeps_completed_evening(tmp_path):
    save_report(tmp_path, 'evening.json', [row()])
    data = fetch(tmp_path, [row(day='2026-10-16')])['HNHPF']
    assert data['us_delayed_asof'].startswith('2026-10-15')
    assert data['us_delayed_cache_source_kind'] == 'current_evening'


def test_newest_valid_across_archives_and_partial_newer(tmp_path):
    save_report(tmp_path, 'morning.json', [row(day='2026-10-13')])
    save_report(tmp_path, 'archive/2026-10-16-evening.json', [row(), row('EXTRA')])
    partial = row(day='2026-10-16')
    partial['source_daily_ohlcv_complete'] = False
    data = fetch(tmp_path, [partial])
    assert set(data) == {'HNHPF'}
    assert data['HNHPF']['us_delayed_asof'].startswith('2026-10-15')
    assert data['HNHPF']['us_delayed_cache_source_kind'] == 'archived_evening'


def test_archive_expiry_and_safety_still_apply(tmp_path):
    save_report(tmp_path, 'evening.json', [row(day='2026-10-08')])
    assert fetch(tmp_path, [row(day='2026-10-16')]) == {}
    save_report(tmp_path, 'evening.json', [row()])
    assert fetch(tmp_path, [], now=datetime.fromisoformat('2026-10-16T04:15:59+00:00')) == {}
    assert fetch(tmp_path, [], now=datetime.fromisoformat('2026-10-16T04:16:00+00:00'))


def test_malformed_and_oversized_latest_do_not_mask_archive(tmp_path):
    path = tmp_path / 'all_analysis.json'
    save_report(tmp_path, 'archive/2026-10-16-evening.json', [row()])
    for raw in ('{broken', 'x' * (8 * 1024 * 1024 + 1)):
        path.write_text(raw)
        assert fetch_cached_us_daily(['HNHPF'], NOW, path)


def test_within_file_duplicate_rejected_older_valid_remains(tmp_path):
    save_report(tmp_path, 'evening.json', [row(), row()])
    save_report(tmp_path, 'morning.json', [row(day='2026-10-14')])
    data = fetch(tmp_path, [])['HNHPF']
    assert data['us_delayed_asof'].startswith('2026-10-14')


def test_across_files_conflicting_same_session_rejected(tmp_path):
    conflicting = row()
    conflicting['official_close_price'] = 10.5
    save_report(tmp_path, 'evening.json', [conflicting])
    assert fetch(tmp_path, [row()]) == {}
    save_report(tmp_path, 'morning.json', [row(day='2026-10-14')])
    assert fetch(tmp_path, [row()])['HNHPF']['us_delayed_asof'].startswith('2026-10-14')


def test_missing_latest_still_reads_completed_archive(tmp_path):
    save_report(tmp_path, 'archive/2026-10-16-evening.json', [row()])
    assert fetch_cached_us_daily(['HNHPF'], NOW, tmp_path / 'all_analysis.json')


def test_total_budget_and_fixed_file_count(tmp_path, monkeypatch):
    import us_cached_daily_fallback as module
    path = tmp_path / 'all_analysis.json'
    path.write_bytes(b'x' * 200)
    save_report(tmp_path, 'evening.json', [row()])
    monkeypatch.setattr(module, 'MAX_TOTAL_BYTES', 100)
    assert fetch_cached_us_daily(['HNHPF'], NOW, path) == {}
    monkeypatch.setattr(module, 'MAX_TOTAL_BYTES', 64 * 1024 * 1024)
    monkeypatch.setattr(module, 'MAX_FILES', 1)
    assert fetch_cached_us_daily(['HNHPF'], NOW, path) == {}


def test_does_not_scan_outside_bounded_archive_dates(tmp_path):
    save_report(tmp_path, 'archive/2026-10-01-evening.json', [row()])
    save_report(tmp_path, 'unrelated.json', [row()])
    assert fetch(tmp_path, []) == {}


@pytest.mark.parametrize('publication,accepted', [
    ('2026-10-15T15:30:00-04:00', False),
    ('2026-10-15T16:15:59-04:00', False),
    ('2026-10-15T16:16:00-04:00', True),
    ('2026-10-15T20:16:00Z', True),
    ('2026-10-16 04:16:00', True),
    ('2026-10-16T15:30:01+00:00', False),
    ('2026-10-16T15:30:00+00:00', True),
    ('2026-10-15', False), ('invalid', False), (None, False),
])
def test_snapshot_publication_proves_bar_was_completed(tmp_path, publication, accepted):
    path = tmp_path / 'all_analysis.json'
    path.write_text(json.dumps({'updated_at': publication, 'data': [row()]}))
    assert bool(fetch_cached_us_daily(['HNHPF'], NOW, path)) is accepted


def test_partial_archive_cannot_age_into_completed_bar(tmp_path):
    path = tmp_path / 'archive/2026-10-15-evening.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'updated_at': '2026-10-15 22:00:00', 'watchlist': [row()]}))
    assert fetch(tmp_path, [row(day='2026-10-16')]) == {}
    save_report(tmp_path, 'morning.json', [row(day='2026-10-14')])
    assert fetch(tmp_path, [])['HNHPF']['us_delayed_asof'].startswith('2026-10-14')


def test_missing_publication_fails_closed(tmp_path):
    path = tmp_path / 'all_analysis.json'
    path.write_text(json.dumps({'data': [row()]}))
    assert fetch_cached_us_daily(['HNHPF'], NOW, path) == {}
