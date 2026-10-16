import json
from datetime import date, datetime, timedelta

import pytest

from us_cached_daily_fallback import fetch_cached_us_daily_references

NOW = datetime.fromisoformat('2026-10-16T16:00:00+00:00')


def reference(symbol='AAPL', day='2026-10-15'):
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return dict(symbol=symbol, market='US', us_delayed_data_available=True,
                us_delayed_source='Alpaca SIP completed daily bars', us_delayed_price=200,
                us_delayed_asof=f'{day}T16:00:00-04:00', us_delayed_interval='1Day',
                us_delayed_session_date=day, us_delayed_session_close=f'{day}T16:00:00-04:00',
                us_delayed_calendar_verified=True, us_delayed_calendar_source='Alpaca Market Calendar',
                us_delayed_session_completed=True, us_delayed_fetched_at=f'{next_day}T04:16:00+00:00',
                us_delayed_coverage='consolidated', us_delayed_minimum_seconds=960, us_live_data_available=False)


def save(tmp_path, rows, name='all_analysis.json', publication='2026-10-16T15:00:00+00:00'):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # updated_at is deliberately earlier than fetched_at: generation start
    # must not be mistaken for publication.
    path.write_text(json.dumps({'updated_at': '2026-10-15 12:00:00',
                               'us_daily_reference_published_at': publication,
                               'data' if name == 'all_analysis.json' else 'watchlist': rows}))
    return path


def fetch(tmp_path, now=NOW, symbols=('AAPL',)):
    return fetch_cached_us_daily_references(symbols, now, tmp_path / 'all_analysis.json')


def test_exact_origin_and_close_only(tmp_path):
    original = reference()
    save(tmp_path, [original])
    result = fetch(tmp_path)['AAPL']
    for key in ('us_delayed_asof', 'us_delayed_fetched_at', 'us_delayed_session_date', 'us_delayed_session_close'):
        assert result[key] == original[key]
    assert result['us_delayed_source'] == 'Alpaca SIP completed daily report cache'
    assert result['us_delayed_origin_source'] == 'Alpaca SIP completed daily bars'
    assert result['us_delayed_origin_published_at'] == '2026-10-16T15:00:00+00:00'
    assert result['us_delayed_cached'] is True
    assert result['us_live_data_available'] is False
    assert not {'us_delayed_bars', 'price', 'score', 'official_close_price'} & result.keys()


@pytest.mark.parametrize('field,value', [
    ('market', 'TW'), ('us_delayed_source', 'Yahoo Finance completed daily report cache'),
    ('us_delayed_calendar_verified', 1), ('us_delayed_session_completed', False),
    ('us_delayed_calendar_source', 'unverified'), ('us_live_data_available', True),
    ('us_delayed_data_available', False), ('us_delayed_interval', '1Min'),
    ('us_delayed_coverage', 'provider_defined'), ('us_delayed_price', True),
    ('us_delayed_price', float('nan')), ('us_delayed_price', float('inf')),
    ('us_delayed_price', 0), ('us_delayed_price', '200'),
    ('us_delayed_session_date', '2026-10-14'), ('us_delayed_session_date', '2026-10-16'),
    ('us_delayed_asof', '2026-10-15T00:00:00-04:00'),
    ('us_delayed_session_close', '2026-10-15T16:00:00'),
    ('us_delayed_fetched_at', '2026-10-15T20:15:59+00:00'),
    ('us_delayed_fetched_at', '2026-10-17T00:00:00+00:00'),
    ('us_delayed_cached', True),
])
def test_invalid_attestation_fails_closed(tmp_path, field, value):
    row = reference()
    row[field] = value
    save(tmp_path, [row])
    assert fetch(tmp_path) == {}


@pytest.mark.parametrize('publication', [None, 'invalid', '2026-10-16 15:00:00',
    '2026-10-15T20:15:59+00:00', '2026-10-16T16:00:01+00:00'])
def test_publication_must_be_aware_after_fetch_and_not_future(tmp_path, publication):
    save(tmp_path, [reference()], publication=publication)
    assert fetch(tmp_path) == {}


def test_republication_preserves_origin_and_cannot_refresh_expiry(tmp_path):
    save(tmp_path, [reference()])
    cached = fetch(tmp_path)['AAPL']
    cached.update(symbol='AAPL', market='US')
    save(tmp_path, [cached], publication='2026-10-17T15:00:00+00:00')
    later = datetime.fromisoformat('2026-10-17T16:00:00+00:00')
    result = fetch(tmp_path, later)['AAPL']
    assert result['us_delayed_origin_published_at'] == cached['us_delayed_origin_published_at']
    assert result['us_delayed_fetched_at'] == cached['us_delayed_fetched_at']
    assert result['us_delayed_asof'] == cached['us_delayed_asof']
    save(tmp_path, [cached], publication='2026-10-23T15:00:00+00:00')
    assert fetch(tmp_path, datetime.fromisoformat('2026-10-23T16:00:00+00:00')) == {}


def test_cached_origin_required_and_ordered(tmp_path):
    save(tmp_path, [reference()])
    cached = fetch(tmp_path)['AAPL']
    cached.update(symbol='AAPL', market='US')
    for value in (None, '2026-10-15T20:15:59+00:00', '2026-10-17T00:00:00+00:00'):
        cached['us_delayed_origin_published_at'] = value
        save(tmp_path, [cached])
        assert fetch(tmp_path) == {}


def test_duplicate_conflict_and_newest_selection(tmp_path):
    save(tmp_path, [reference(), reference()])
    save(tmp_path, [reference(day='2026-10-14')], name='morning.json')
    assert fetch(tmp_path)['AAPL']['us_delayed_session_date'] == '2026-10-14'
    save(tmp_path, [reference()])
    conflicting = reference()
    conflicting['us_delayed_price'] = 201
    save(tmp_path, [conflicting], name='evening.json')
    assert fetch(tmp_path)['AAPL']['us_delayed_session_date'] == '2026-10-14'
    save(tmp_path, [reference()], name='evening.json')
    assert fetch(tmp_path)['AAPL']['us_delayed_session_date'] == '2026-10-15'


def test_midnight_delay_and_early_close(tmp_path):
    row = reference()
    row['us_delayed_asof'] = row['us_delayed_session_close'] = '2026-10-15T13:00:00-04:00'
    row['us_delayed_fetched_at'] = '2026-10-16T04:15:59+00:00'
    save(tmp_path, [row], publication='2026-10-16T04:15:59+00:00')
    assert not fetch(tmp_path, datetime.fromisoformat('2026-10-16T04:15:58+00:00'))
    assert fetch(tmp_path, datetime.fromisoformat('2026-10-16T04:16:00+00:00'))


def test_requested_only_and_unattested_missing(tmp_path):
    save(tmp_path, [reference(), reference('MSFT'), {'symbol': 'NVDA', 'market': 'US'}])
    assert set(fetch(tmp_path, symbols=['AAPL', 'NVDA'])) == {'AAPL'}
    assert fetch(tmp_path, symbols='AAPL') == {}


def test_bounded_io_and_bad_latest_can_use_archive(tmp_path, monkeypatch):
    import us_cached_daily_fallback as module
    save(tmp_path, [reference()], name='archive/2026-10-16-evening.json')
    (tmp_path / 'all_analysis.json').write_text('broken')
    assert fetch(tmp_path)
    monkeypatch.setattr(module, 'MAX_TOTAL_BYTES', 3)
    assert fetch(tmp_path) == {}


def test_preserve_exact_publication_text_and_original_fetch_gate(tmp_path):
    save(tmp_path, [reference()], publication='2026-10-16T15:00:00Z')
    assert fetch(tmp_path)['AAPL']['us_delayed_origin_published_at'] == '2026-10-16T15:00:00Z'
    row = reference()
    row['us_delayed_fetched_at'] = '2026-10-15T20:16:00+00:00'
    save(tmp_path, [row])
    assert fetch(tmp_path) == {}


@pytest.mark.parametrize('minimum', [None, True, False, float('nan'), float('inf'), -1, 0, 959, 961, 1920, '960'])
def test_original_minimum_delay_must_be_exact_contract(tmp_path, minimum):
    row = reference()
    row['us_delayed_minimum_seconds'] = minimum
    save(tmp_path, [row])
    assert fetch(tmp_path) == {}


def test_missing_minimum_delay_rejected_and_float960_valid(tmp_path):
    row = reference()
    row.pop('us_delayed_minimum_seconds')
    save(tmp_path, [row])
    assert fetch(tmp_path) == {}
    row['us_delayed_minimum_seconds'] = 960.0
    save(tmp_path, [row])
    assert fetch(tmp_path)
