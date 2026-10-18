from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from stock_prediction_evidence import load_catalog, build_evidence
from stock_agent import analyze_stock, verify_result
from prediction_engine.engine import _compact_prediction, _make_predictions
from prediction_engine.storage import PredictionStore
from tests.test_prediction_engine import _row

NOW = datetime(2026, 10, 18, 2, tzinfo=timezone.utc)


def write(path, value):
    path.write_text(json.dumps(value))


def frozen(symbol='IREN', code='NEXT_1D', day='2026-10-16'):
    return {'symbol': symbol, 'name': symbol, 'market': 'US', 'asset_group': 'US_STOCK',
            'session_date': day, 'horizon_code': code, 'model_version': 'frozen-v1',
            'target_side': 'UP', 'expected_return_pct': -0.35, 'probability_pct': 35,
            'trade_blocked': False, 'factor_explanation': {
                'upward_drivers': [{'label': '新聞', 'expected_return_contribution_pct': .1}],
                'downward_drivers': [{'label': '趨勢', 'expected_return_contribution_pct': -.2}]}}


@pytest.fixture
def reports(tmp_path):
    folder = tmp_path / 'reports'; folder.mkdir()
    manifest = {'updated_at': '2026-10-18 06:30:12', 'contract_version': 'test',
                'data_files': {'US_STOCK': {'NEXT_1D': {'parts': [
                    'prediction_engine_data_US_STOCK_NEXT_1D_part01.json',
                    'prediction_engine_data_US_STOCK_NEXT_1D_part02.json']}}}}
    write(folder / 'prediction_engine.json', manifest)
    for n, sym in [(1, 'MSFT'), (2, 'IREN')]:
        write(folder / manifest['data_files']['US_STOCK']['NEXT_1D']['parts'][n-1],
              {'group': 'US_STOCK', 'horizon_code': 'NEXT_1D', 'updated_at': manifest['updated_at'],
               'contract_version': 'test', 'predictions': [frozen(sym)], 'rankings': []})
    write(folder / 'all_analysis.json', {'updated_at': manifest['updated_at'], 'data': []})
    return folder, manifest


def test_unranked_symbol_second_shard_query_is_read_only_and_signed_direction(reports):
    folder, _ = reports
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    r = analyze_stock('IREN', root=folder.parent, now=NOW)
    f = r['frozen_predictions']['forecasts'][0]
    assert r['verified'] and f['direction'] == 'DOWN' and f['event_target'] == 'UP'
    assert f['event_probability_pct'] == 35 and f['probability_calibrated'] is False
    assert f['trade_feasibility'] == 'unknown_frozen_guard_evidence'
    assert f['trigger'] is None and f['invalidation'] is None
    assert r['chart'] is None
    again = analyze_stock('IREN', root=folder.parent, now=NOW)
    assert again['task_key'] == r['task_key']
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}
    f['direction'] = 'UP'
    assert not verify_result(r, {})


@pytest.mark.parametrize('failure', ['missing_part', 'wrong_generation', 'duplicate', 'wrong_horizon', 'path_escape'])
def test_missing_mixed_duplicate_or_untrusted_parts_never_give_partial_horizon(reports, failure):
    folder, manifest = reports
    name = manifest['data_files']['US_STOCK']['NEXT_1D']['parts'][1]
    chunk = json.loads((folder / name).read_text())
    if failure == 'missing_part': (folder / name).unlink()
    elif failure == 'path_escape':
        manifest['data_files']['US_STOCK']['NEXT_1D']['parts'][1] = '../private.json'
        write(folder / 'prediction_engine.json', manifest)
    else:
        if failure == 'wrong_generation': chunk['updated_at'] = '2026-10-17 06:30:12'
        elif failure == 'wrong_horizon': chunk['predictions'][0]['horizon_code'] = 'UP_5D'
        else: chunk['predictions'][0]['symbol'] = 'MSFT'
        write(folder / name, chunk)
    c = load_catalog(folder)
    assert not c['records'] and c['issues']


def test_future_forecast_and_mismatched_history_are_excluded(reports):
    folder, _ = reports; c = load_catalog(folder)
    row = c['records'][1]
    old = {**frozen(day='2026-10-15'), 'status': 'matured', 'realized_return_pct': -2,
           'outcome_session_date': '2026-10-16', 'direction_correct': 0}
    row['recent_outcomes'] = [old, {**old, 'horizon_code': 'UP_5D'}, {**old, 'symbol': 'MSFT'}]
    f = build_evidence(c, 'IREN', 'US', NOW, folder)['forecasts'][0]
    assert len(f['recent_outcomes']) == 1
    o = f['recent_outcomes'][0]['outcome']
    assert o['expected_direction_correct'] is True and o['legacy_event_target_correct'] == 0
    assert o['return_error_pp'] == -1.65
    row['created_at'] = '2026-10-19T12:00:00Z'
    assert not build_evidence(c, 'IREN', 'US', NOW, folder)['forecasts']
    row.pop('created_at');c['manifest']['updated_at'] = '2026-10-20 10:00:00'
    assert not build_evidence(c, 'IREN', 'US', NOW, folder)['forecasts']


def test_storage_roundtrip_preserves_guard_and_legacy_event_label_without_rewriting(tmp_path):
    store = PredictionStore(tmp_path / 'test.sqlite3')
    row = _row('IREN', market='US'); row['trade_guard_blocked'] = True
    prediction = _make_predictions([row], market='US', date='2026-10-15',
        updated_at='2026-10-16T00:30:00Z', flow_by_symbol={})[0]
    prediction['expected_return_pct'] = -1
    assert prediction['evidence']['trade_blocked'] is True
    store.insert_predictions([prediction]); store.record_session('US', '2026-10-15', 'test')
    store.record_session('US', '2026-10-16', 'test')
    row['official_close_price'] = 98; store.record_prices([row], '2026-10-16')
    assert store.settle_matured('US') == 1
    saved = store.latest_predictions()[0]
    assert saved['direction_correct'] == 0  # legacy UP-event result remains false
    assert _compact_prediction(saved)['trade_blocked'] is True
    changed = {**prediction, 'expected_return_pct': 10, 'trade_blocked': False,
               'evidence': {'trade_blocked': False}}
    assert store.insert_predictions([changed]) == 0
    assert store.latest_predictions()[0]['expected_return_pct'] == -1
    current = {**prediction, 'session_date': '2026-10-16', 'created_at': '2026-10-17T00:30:00Z'}
    store.insert_predictions([current])
    latest = _compact_prediction(store.latest_predictions()[0])
    assert len(latest['recent_outcomes']) == 1
    old = latest['recent_outcomes'][0]
    assert old['session_date'] == '2026-10-15' and old['direction_correct'] == 0
    assert old['expected_return_pct'] == -1 and old['trade_blocked'] is True
    assert _compact_prediction({**saved, 'trade_blocked': False})['trade_blocked'] is True
    assert _compact_prediction({**saved, 'evidence': {}})['trade_blocked'] is None


def test_wrong_system_diagnostics_do_not_become_forecast_error_cause(reports):
    folder, _ = reports
    write(folder / 'performance.json', {'updated_at': '2026-10-18', 'error_cases': {'event_clusters': [
        {'symbol': 'IREN', 'market': 'US', 'event_id': 'different-system', 'primary_cause': 'event_gap_risk',
         'source_start_date': '2026-09-01', 'evaluated_end_date': '2026-09-05', 'horizons': [1, 5]}]}})
    write(folder / 'model_learning.json', {'updated_at': '2026-10-18', 'shadow_execution_audit': {'candidates': {'event_gap_risk': {'status': 'collecting'}}}})
    result = build_evidence(load_catalog(folder), 'IREN', 'US', NOW, folder)
    assert result['forecasts'][0]['outcome']['status'] == 'not_published_or_pending'
    assert result['related_historical_diagnostics'][0]['candidate']['status'] == 'collecting'
    assert '不同預測系統' in result['related_historical_diagnostics'][0]['scope']
    future = json.loads((folder / 'performance.json').read_text())
    future['updated_at'] = '2026-10-20'
    write(folder / 'performance.json', future)
    assert not build_evidence(load_catalog(folder), 'IREN', 'US', NOW, folder)['related_historical_diagnostics']


def test_normal_report_producer_publishes_frozen_guard_and_same_horizon_history(tmp_path):
    from prediction_engine.engine import run_prediction_engine
    folder = tmp_path / 'reports'; folder.mkdir()
    db = tmp_path / 'private.sqlite3'
    row = _row('IREN', market='US'); row['trade_guard_blocked'] = True
    run_prediction_engine(folder, [row], period='test', updated_at='2026-01-03T01:00:00Z',
                          intraday=False, db_path=db)
    row['official_session_date'] = '2026-01-05'; row['official_close_price'] = 98
    run_prediction_engine(folder, [row], period='test', updated_at='2026-01-06T01:00:00Z',
                          intraday=False, db_path=db)
    manifest = json.loads((folder / 'prediction_engine.json').read_text())
    chunk = json.loads((folder / manifest['data_files']['US_STOCK']['NEXT_1D']).read_text())
    item = chunk['predictions'][0]
    assert item['trade_blocked'] is True and item['trade_block_evidence_recorded'] is True
    assert item['session_date'] == '2026-01-05'
    assert item['status'] == 'pending'
    assert len(item['recent_outcomes']) == 1
    old = item['recent_outcomes'][0]
    assert old['session_date'] == '2026-01-02' and old['outcome_session_date'] == '2026-01-05'
    assert old['status'] == 'matured' and old['realized_return_pct'] == pytest.approx(-2)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    view = build_evidence(load_catalog(folder), 'IREN', 'US', NOW, folder)
    assert view['forecasts'][0]['recent_outcomes'][0]['key']['session_date'] == '2026-01-02'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_frozen_judgment_remains_queryable_when_quote_report_is_missing(reports):
    folder, _ = reports
    (folder / 'all_analysis.json').unlink()
    r = analyze_stock('IREN', root=folder.parent, now=NOW)
    assert r['verified'] and r['chart'] is None
    assert r['frozen_predictions']['forecasts'][0]['direction'] == 'DOWN'
    assert any('報價報表缺失' in x for x in r['missing_data'])


def test_investment_horizons_are_not_extrapolated_from_short_forecasts(reports):
    folder, _ = reports; catalog = load_catalog(folder)
    result = build_evidence(catalog, 'IREN', 'US', NOW, folder)
    assert result['investment_scope']['quarter_model_codes'] == []
    assert result['investment_scope']['long_term_status'] == 'not_implemented_or_validated'
    catalog['records'].append(frozen(code='UP_63D'))
    result = build_evidence(catalog, 'IREN', 'US', NOW, folder)
    assert result['investment_scope']['quarter_model_codes'] == ['UP_63D']
    assert [(p['key']['horizon_code'], p['horizon_sessions']) for p in result['forecasts']] == [('NEXT_1D', 1), ('UP_63D', 63)]
