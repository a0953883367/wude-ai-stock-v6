"""Offline close-time regressions; no provider calls or historical rewrites."""
import json
import sqlite3
from pathlib import Path

import pytest

from market_calendar import OfficialMarketCalendar
from prediction_engine.engine import _checkpoint_close_reason, run_prediction_engine
from prediction_engine.models import HORIZONS
from test_prediction_engine import _row


def calendar_file(reports):
    reports.mkdir(exist_ok=True)
    us = {day: {'open': '09:30', 'close': close} for day, close in [
        ('2026-03-06', '16:00'), ('2026-03-09', '16:00'),
        ('2026-10-30', '16:00'), ('2026-11-02', '16:00'), ('2026-11-27', '13:00')]}
    payload = {'version': 1, 'markets': {
        'US': {'years': {'2026': {'status': 'verified_alpaca', 'sessions': sorted(us), 'session_details': us}}},
        'TW': {'years': {'2026': {'status': 'verified_twse_tpex', 'sessions': ['2026-07-10', '2026-11-02'], 'session_details': {}}}},
    }}
    path = reports / 'official_market_calendar.json'
    path.write_text(json.dumps(payload))
    return path


def row(market, day, symbol='LEU'):
    value = _row(symbol, market)
    value['official_session_date'] = day
    return value


@pytest.mark.parametrize('market,day,stamp,reason', [
    ('US', '2026-11-02', '2026-11-03 02:38:25', 'session_not_completed'),
    ('US', '2026-11-02', '2026-11-03 04:59:59', 'session_not_completed'),
    ('US', '2026-11-02', '2026-11-03 05:00:00', None),
    ('US', '2026-11-02', '2026-11-02T21:00:00Z', None),
    ('US', '2026-03-06', '2026-03-06T20:59:59Z', 'session_not_completed'),
    ('US', '2026-03-06', '2026-03-06T21:00:00Z', None),
    ('US', '2026-03-09', '2026-03-09T19:59:59Z', 'session_not_completed'),
    ('US', '2026-03-09', '2026-03-09T20:00:00Z', None),
    ('US', '2026-10-30', '2026-10-30T20:00:00Z', None),
    ('US', '2026-11-27', '2026-11-27T17:59:59Z', 'session_not_completed'),
    ('US', '2026-11-27', '2026-11-27T18:00:00Z', None),
    ('US', '2026-11-26', '2026-11-27T22:00:00Z', 'not_official_session'),
    ('TW', '2026-11-02', '2026-11-02 13:29:59', 'session_not_completed'),
    ('TW', '2026-11-02', '2026-11-02 13:30:00', None),
    ('TW', '2026-07-10', '2026-07-10 20:00:00', 'not_official_session'),
    ('TW', '2026-11-01', '2026-11-02 20:00:00', 'not_official_session'),
    ('US', '2026-11-02', '2026-11-03', 'invalid_checkpoint_timestamp'),
    ('US', '2026-11-02', 'invalid', 'invalid_checkpoint_timestamp'),
])
def test_verified_local_close_including_dst_early_close_and_holidays(tmp_path, market, day, stamp, reason):
    calendar = OfficialMarketCalendar(calendar_file(tmp_path), auto_refresh=False, allow_network=False)
    assert _checkpoint_close_reason(calendar, market, [row(market, day)], stamp) == reason


@pytest.mark.parametrize('fault', ['missing', 'unverified', 'close_missing', 'close_invalid', 'reversed'])
def test_uncertain_calendar_never_authorizes_checkpoint(tmp_path, fault):
    path = calendar_file(tmp_path)
    payload = json.loads(path.read_text())
    year = payload['markets']['US']['years']['2026']
    if fault == 'missing': path.unlink()
    else:
        if fault == 'unverified': year['status'] = 'cached'
        if fault == 'close_missing': year['session_details']['2026-11-02'].pop('close')
        if fault == 'close_invalid': year['session_details']['2026-11-02']['close'] = '16:00garbage'
        if fault == 'reversed': year['session_details']['2026-11-02']['close'] = '08:00'
        path.write_text(json.dumps(payload))
    calendar = OfficialMarketCalendar(path, auto_refresh=False, allow_network=False)
    assert _checkpoint_close_reason(calendar, 'US', [row('US', '2026-11-02')], '2026-11-03 06:00:00')


def test_live_gate_preserves_frozen_records_and_period_rules(tmp_path):
    reports = tmp_path / 'reports'; calendar_file(reports)
    db = tmp_path / 'engine.sqlite3'
    def run(day, stamp, period='morning', intraday=False):
        return run_prediction_engine(reports, [row('US', day)], period=period,
                                     updated_at=stamp, intraday=intraday, db_path=db)
    first = run('2026-10-30', '2026-10-31 06:00:00')
    assert first['run_summary']['inserted_predictions'] == len(HORIZONS)
    with sqlite3.connect(db) as connection:
        before = connection.execute('SELECT * FROM predictions ORDER BY id').fetchall()
    blocked = run('2026-11-02', '2026-11-03 02:38:25')
    assert blocked['market_status']['US']['reason'] == 'session_not_completed'
    assert blocked['run_summary']['inserted_predictions'] == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT * FROM predictions ORDER BY id').fetchall() == before
    assert run('2026-11-02', '2026-11-03 06:00:00', period='evening')['run_summary']['inserted_predictions'] == 0
    assert run('2026-11-02', '2026-11-03 06:00:00', intraday=True)['run_summary']['inserted_predictions'] == 0
    assert run('2026-11-02', '2026-11-03 06:00:00')['run_summary']['inserted_predictions'] == len(HORIZONS)
    assert blocked['policy']['existing_frozen_records_revalidated'] is False


@pytest.mark.parametrize('stamp,expected', [('2026-11-03 02:38:25', 0), ('2026-11-03 05:00:00', 1), ('2026-11-03', 0)])
def test_archive_uses_original_timestamp_not_current_clock(tmp_path, stamp, expected):
    reports = tmp_path / 'reports'; calendar_file(reports)
    archive = reports / 'archive'; archive.mkdir()
    document = archive / '2026-11-03-morning.json'
    document.write_text(json.dumps({'period': 'morning', 'updated_at': stamp, 'data': [row('US', '2026-11-02')]}))
    before = document.read_bytes()
    result = run_prediction_engine(reports, [], period='morning', updated_at='2026-11-04 06:00:00',
                                   intraday=False, db_path=tmp_path / 'engine.sqlite3')
    summary = result['run_summary']['archive_bootstrap']
    assert summary['sessions'] == expected
    assert summary['predictions'] == expected * len(HORIZONS)
    assert bool(summary['rejected_close_checks']) is (not expected)
    assert document.read_bytes() == before


@pytest.mark.parametrize('dates', [[None], ['2026-11-02bad'], ['2026-11-02', '2026-10-30']])
def test_missing_or_mixed_official_dates_cannot_use_cached_date(tmp_path, dates):
    calendar = OfficialMarketCalendar(calendar_file(tmp_path), auto_refresh=False, allow_network=False)
    rows = [row('US', day) for day in dates]
    for value in rows:
        value['cached_at'] = '2026-11-02'
    assert _checkpoint_close_reason(calendar, 'US', rows, '2026-11-03 06:00:00') == 'invalid_checkpoint_sessions'
