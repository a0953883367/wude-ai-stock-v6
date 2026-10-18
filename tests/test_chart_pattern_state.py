import pandas as pd
import pytest
from chart_pattern_state import pattern_state
from strategy import _chart_pattern_shadow_features


def row(direction='bearish', price=133):
    return {'chart_pattern_shadow_direction': direction, 'chart_pattern_shadow_source_close': price,
            'chart_pattern_shadow_entry': 172.77, 'chart_pattern_shadow_stop': 180.17,
            'chart_pattern_shadow_target': 165.36}


def test_umc_target_crossed_is_not_an_active_setup():
    r = pattern_state(row())
    assert r['chart_pattern_shadow_lifecycle'] == 'target_crossed'
    assert r['chart_pattern_shadow_active_reference'] is False
    assert r['chart_pattern_shadow_distant'] is True


@pytest.mark.parametrize('price,expected', [(169,'reference'), (181,'invalidated')])
def test_bearish_reference_and_opposite_invalidation(price, expected):
    assert pattern_state(row(price=price))['chart_pattern_shadow_lifecycle'] == expected


def test_bullish_target_and_invalidation_are_directional():
    r = row('bullish', 184)
    r.update(chart_pattern_shadow_stop=165.36, chart_pattern_shadow_target=180.17)
    assert pattern_state(r)['chart_pattern_shadow_target_crossed'] is True
    r['chart_pattern_shadow_source_close'] = 160
    assert pattern_state(r)['chart_pattern_shadow_invalidated'] is True


def test_missing_price_and_legacy_basis_do_not_become_active():
    r = row(); r.pop('chart_pattern_shadow_source_close')
    assert pattern_state(r)['chart_pattern_shadow_lifecycle'] == 'unknown'
    r['official_adjusted_close_price'] = 169
    assert not pattern_state(r)['chart_pattern_shadow_active_reference']


def test_detector_records_input_and_marks_old_target():
    close = pd.Series([90,92,95,98,101,104,102,99,96,94,96,99,102,104.5,103,100,97,94,92,91,90,89,88,87,86,75],
                      index=pd.bdate_range('2026-09-01', periods=26), dtype=float)
    r = _chart_pattern_shadow_features(close+1, close-1, close, close*0+1000)
    assert r['chart_pattern_shadow_name'] == '雙重頂'
    assert r['chart_pattern_shadow_source_close'] == 75
    assert r['chart_pattern_shadow_session_date'] == close.index[-1].date().isoformat()
    assert r['chart_pattern_shadow_identity']
    assert r['chart_pattern_shadow_lifecycle'] == 'target_crossed'
    assert r['chart_pattern_shadow_status'].startswith('歷史型態')
    assert r['chart_pattern_shadow_affects_formal'] is False


def test_extrapolation_warning_and_pattern_input_price_precedence():
    r = row(price=169)
    r['official_adjusted_close_price'] = 133
    r['chart_pattern_shadow_extrapolation_bars'] = 16
    state = pattern_state(r)
    assert state['chart_pattern_shadow_lifecycle'] == 'historical'
    assert state['chart_pattern_shadow_comparison_price'] == 169
    assert state['chart_pattern_shadow_target_crossed'] is False
