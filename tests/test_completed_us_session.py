import pytest
from completed_us_session import completed_us_session
from million_simulation import empty_state, update_state
from test_million_simulation import universe
from test_validation_progress_monitor import _source, _rows
from validation_progress_monitor import update_validation_progress_monitor


@pytest.mark.parametrize('stamp,expected', [
    ('2026-10-06 03:59:59', False),
    ('2026-10-06 04:00:00', True),
    ('2026-10-06 12:00:00', True),
    ('2026-10-06 20:00:00', True),
    ('2026-10-05T20:00:00+00:00', True),
    ('bad', False),
])
def test_eastern_close_is_independent_of_report_period(stamp, expected):
    assert completed_us_session(_rows('US', '2026-10-05'), stamp) is expected


def test_winter_close_and_mixed_dates_fail_closed():
    rows = _rows('US', '2026-12-01')
    assert not completed_us_session(rows, '2026-12-02 04:59:59')
    assert completed_us_session(rows, '2026-12-02 05:00:00')
    rows[0]['official_session_date'] = '2026-11-30'
    assert not completed_us_session(rows, '2026-12-02 12:00:00')
    assert not completed_us_session([], '2026-12-02 12:00:00')


@pytest.mark.parametrize('period', ['noon', 'evening'])
def test_missed_morning_settles_oct5_once_and_keeps_tw_gate(period):
    state = empty_state()
    update_state(state, universe('2026-10-02'), period='morning', updated_at='2026-10-03 06:00:00')
    update_state(state, universe('2026-10-05'), period=period, updated_at='2026-10-06 12:00:00')
    us = state['markets']['US']
    assert us['completed_days'] == 1
    assert us['days'][0]['signal_session_date'] == '2026-10-02'
    assert us['days'][0]['session_date'] == '2026-10-05'
    update_state(state, universe('2026-10-05'), period=period, updated_at='2026-10-06 20:00:00')
    assert us['completed_days'] == 1
    if period == 'noon':
        assert state['markets']['TW']['pending'] is None


@pytest.mark.parametrize('intraday,stamp', [(True, '2026-10-06 12:00:00'), (False, '2026-10-06 03:59:59')])
def test_in_progress_prices_never_settle(intraday, stamp):
    state = empty_state()
    update_state(state, universe('2026-10-02'), period='morning', updated_at='2026-10-03 06:00:00')
    update_state(state, universe('2026-10-05'), period='evening', updated_at=stamp, intraday=intraday)
    assert state['markets']['US']['completed_days'] == 0
    assert state['markets']['US']['pending']['signal_session_date'] == '2026-10-02'


def test_missing_benchmark_still_blocks_non_morning_settlement():
    state = empty_state()
    update_state(state, universe('2026-10-02'), period='morning', updated_at='2026-10-03 06:00:00')
    rows = [x for x in universe('2026-10-05') if x['symbol'] != 'VOO']
    update_state(state, rows, period='noon', updated_at='2026-10-06 12:00:00')
    assert state['markets']['US']['completed_days'] == 0
    assert state['markets']['US']['status'] == 'waiting_data'


def test_monitor_detects_stall_without_morning_and_deduplicates(tmp_path):
    _source(tmp_path, 31, 26)
    update_validation_progress_monitor(tmp_path, _rows('US', '2026-10-02'), period='morning', updated_at='2026-10-03 06:00:00')
    state = update_validation_progress_monitor(tmp_path, _rows('US', '2026-10-05'), period='noon', updated_at='2026-10-06 12:00:00')
    assert state['markets']['US']['stalled_sessions'] == 1
    assert state['markets']['US']['status'] == 'warning'
    state = update_validation_progress_monitor(tmp_path, _rows('US', '2026-10-05'), period='evening', updated_at='2026-10-06 20:00:00')
    assert state['markets']['US']['stalled_sessions'] == 1
    _source(tmp_path, 31, 27)
    state = update_validation_progress_monitor(tmp_path, _rows('US', '2026-10-05'), period='evening', updated_at='2026-10-06 20:01:00')
    assert state['markets']['US']['last_completed_days'] == 27
    assert state['markets']['US']['stalled_sessions'] == 0


def test_monitor_rejects_intraday_date_even_without_intraday_flag(tmp_path):
    _source(tmp_path, 31, 26)
    state = update_validation_progress_monitor(tmp_path, _rows('US', '2026-10-06'), period='evening', updated_at='2026-10-06 23:55:04')
    assert state['markets']['US']['last_session_date'] == ''


def test_delayed_report_uses_exact_oct5_history_not_oct6_prices():
    import pandas as pd
    state = empty_state()
    update_state(state, universe('2026-10-02'), period='morning', updated_at='2026-10-03 06:00:00')
    history = {
        row['symbol']: pd.DataFrame({
            'Open': [row['official_open_price']],
            'Close': [row['official_close_price']],
        }, index=pd.to_datetime(['2026-10-05']))
        for row in universe('2026-10-05') if row['market'] == 'US'
    }
    update_state(state, universe('2026-10-06'), period='noon',
                 updated_at='2026-10-07 12:00:00', price_history=history)
    us = state['markets']['US']
    assert us['completed_days'] == 1
    assert us['days'][0]['session_date'] == '2026-10-05'
    assert us['pending']['signal_session_date'] == '2026-10-06'
    update_state(state, universe('2026-10-06'), period='evening',
                 updated_at='2026-10-07 20:00:00', price_history=history)
    assert us['completed_days'] == 1


def test_holdings_completed_close_advances_without_morning():
    from holding_simulation import empty_state as holding_empty, update_state as holding_update
    from test_holding_simulation import universe as holding_universe
    state = holding_empty()
    holding_update(state, holding_universe('2026-10-02'), period='noon', updated_at='2026-10-03 06:00:00')
    holding_update(state, holding_universe('2026-10-05'), period='evening', updated_at='2026-10-06 20:00:00')
    assert state['medium']['US']['last_valuation_date'] == '2026-10-05'
