import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

import agent_stock_recovery as recovery
from briefing_watchdog import data_refresh_needed, silent_refresh_needed
from tests.test_us_cached_sip_references import reference
from watchlist import WATCHLIST

NOW = datetime.fromisoformat('2026-10-17T09:00:00+00:00')
SYMBOLS = json.loads((Path(__file__).parent / 'fixtures/us_free_probe_universe.json').read_text())['symbols']


@pytest.fixture
def reports(tmp_path, monkeypatch):
    rows = [dict(reference(s), price=200) if s != 'HNHPF' else
            dict(symbol=s, market='US', price=None, us_live_data_available=False,
                 us_delayed_data_available=False) for s in SYMBOLS]
    analysis = dict(updated_at=NOW.isoformat(), period='noon',
                    us_daily_reference_published_at='2026-10-16T18:53:35.443031+00:00', data=rows)
    latest = {k: v for k, v in analysis.items() if k != 'data'}
    watched = {r['symbol'] for r in WATCHLIST if r['market'] == 'US'}
    latest.update(watchlist=copy.deepcopy([r for r in rows if r['symbol'] in watched]),
                  data_status=dict(us_data_mode='free_delayed', us_sip_count=0, us_opra_count=0))
    for name, value in [('latest', latest), ('all_analysis', analysis)]:
        recovery.save(tmp_path / (name + '.json'), value)
    direction = dict(needs_silent_refresh=False, status='verified_current', reason='test')
    monkeypatch.setattr(recovery, 'inspect_us_direction', lambda *a: direction)
    monkeypatch.setattr('us_direction_agent.inspect_us_direction', lambda *a: direction)
    return tmp_path, latest, analysis


def test_healthy_free_across_gates_and_reserved_recheck(reports):
    root, latest, _ = reports
    assert not data_refresh_needed(latest, now=NOW, reports=root)
    assert not silent_refresh_needed(latest, now=NOW, reports=root)
    state = recovery.inspect(root, NOW, runs=[], web={'ok': True})
    assert state['diagnosis'] == []
    assert state['next_actions'] == []
    key = next(iter(state['data_incidents']))
    state['next_actions'] = [dict(incident_key=key, period='noon', kind='data_refresh')]
    recovery.reserve(state, NOW, 'test')
    recovery.save(root / 'agent_recovery.json', state)
    executor = Mock()
    executor.runs.return_value = []
    result = recovery.execute_reserved(root, NOW, executor, expected_run_id='test')
    executor.dispatch.assert_not_called()
    assert result['data_incidents'][key]['attempts'][-1]['state'] == 'skipped_already_fresh'


@pytest.mark.parametrize('fault', ['stale_report', 'future_report', 'stale_reference', 'future_fetch',
    'future_publication', 'partial', 'duplicate', 'missing_hnhpf', 'bad_source', 'bad_price',
    'live', 'opra', 'raw_bars', 'unrelated', 'broken_consumer', 'missing_consumer', 'malformed'])
def test_invalid_free_data_never_satisfies_guard(reports, fault):
    root, latest, analysis = reports
    row = next(r for r in analysis['data'] if r['symbol'] != 'HNHPF')
    if fault == 'stale_report': latest['updated_at'] = (NOW - timedelta(minutes=121)).isoformat()
    elif fault == 'future_report': latest['updated_at'] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == 'stale_reference':
        # Keep fresh publication and report time; age must be checked at observation.
        analysis['data'] = [dict(reference(r['symbol'], '2026-10-08'), price=200) if r['symbol'] != 'HNHPF' else r for r in analysis['data']]
        latest['watchlist'] = [copy.deepcopy(next(r for r in analysis['data'] if r['symbol'] == old['symbol'])) for old in latest['watchlist']]
    elif fault == 'future_fetch': row['us_delayed_fetched_at'] = '2026-10-18T00:00:00Z'
    elif fault == 'future_publication': latest['us_daily_reference_published_at'] = '2026-10-18T00:00:00Z'
    elif fault == 'partial': analysis['data'].remove(row)
    elif fault == 'duplicate': analysis['data'].append(copy.deepcopy(row))
    elif fault == 'missing_hnhpf': analysis['data'] = [r for r in analysis['data'] if r['symbol'] != 'HNHPF']
    elif fault == 'bad_source': row['us_delayed_source'] = 'unverified'
    elif fault == 'bad_price': row['price'] = float('nan')
    elif fault == 'live': row['us_live_data_available'] = True
    elif fault == 'opra': row['us_option_data_available'] = True
    elif fault == 'raw_bars': row['us_delayed_bars'] = []
    elif fault == 'unrelated': analysis['updated_at'] = '2026-10-16T09:00:00Z'
    elif fault == 'broken_consumer': latest['watchlist'][0]['price'] = None
    elif fault == 'missing_consumer': latest['watchlist'] = []
    elif fault == 'malformed': analysis['data'] = [None]
    recovery.save(root / 'all_analysis.json', analysis)
    recovery.save(root / 'latest.json', latest)
    assert data_refresh_needed(latest, now=NOW, reports=root)
    assert recovery.inspect(root, NOW, runs=[], web={'ok': True})['next_actions']


def test_seven_ny_date_boundary(reports):
    root, latest, _ = reports
    for stamp, needed in [('2026-10-23T03:59:59Z', False), ('2026-10-23T04:00:00Z', True)]:
        now = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        latest['updated_at'] = now.isoformat()
        analysis = json.loads((root / 'all_analysis.json').read_text())
        analysis['updated_at'] = latest['updated_at']
        recovery.save(root / 'all_analysis.json', analysis)
        assert data_refresh_needed(latest, now=now, reports=root) is needed


def test_paid_unknown_and_taiwan_checks_preserved(reports):
    root, latest, _ = reports
    for mode in ['paid_live', 'unknown', None]:
        latest['data_status']['us_data_mode'] = mode
        assert data_refresh_needed(latest, now=NOW, reports=root)
    latest['data_status'].update(us_data_mode='paid_live', us_sip_count=187, us_opra_count=30)
    assert not data_refresh_needed(latest, now=NOW, reports=root)
    latest['data_status']['us_data_mode'] = 'free_delayed'
    recovery.save(root / 'latest.json', latest)
    recovery.save(root / 'system_guard.json', {'checks': [{'code': 'tw_core_data', 'level': 'critical'}]})
    assert 'tw_core_data' in recovery.inspect(root, NOW, runs=[], web={'ok': True})['diagnosis']


def test_silent_generation_receipt_uses_same_free_evidence(reports):
    from report_delivery import record_delivery, validate_fixed_report
    root, latest, analysis = reports
    latest['run_mode'] = 'scheduled_report'
    recovery.save(root / 'latest.json', latest)
    (root / 'latest.md').write_text('test report')
    assert validate_fixed_report(latest, expected_period='noon', now=NOW, reports=root)[0]
    record_delivery(root, period='noon', report_updated_at=latest['updated_at'],
                    state='suppressed', delivered=False, expected_delivery=False,
                    detail='test', checked_at=NOW, relay_health={})
    receipt = json.loads((root / 'delivery_receipts/2026-10-17-noon.json').read_text())
    assert receipt['last_generation']['generation_validated'] is True
    assert receipt['last_success'] == {}
    analysis['data'].pop()
    recovery.save(root / 'all_analysis.json', analysis)
    assert not validate_fixed_report(latest, expected_period='noon', now=NOW, reports=root)[0]


@pytest.mark.parametrize('cached_source', ['sip', 'yahoo'])
def test_cached_sources_keep_original_age_and_provenance(reports, cached_source):
    root, latest, analysis = reports
    for row in analysis['data']:
        if row['symbol'] == 'HNHPF':
            continue
        row['us_delayed_cached'] = True
        if cached_source == 'sip':
            row.update(us_delayed_source='Alpaca SIP completed daily report cache',
                       us_delayed_origin_source='Alpaca SIP completed daily bars',
                       us_delayed_origin_published_at='2026-10-16T16:00:00Z',
                       us_delayed_cache_read_at='2026-10-16T18:00:00Z')
        else:
            row.update(us_delayed_source='Yahoo Finance completed daily report cache',
                       us_delayed_coverage='provider_defined', us_delayed_asof='2026-10-15T04:00:00Z')
    latest['watchlist'] = [copy.deepcopy(next(r for r in analysis['data'] if r['symbol'] == old['symbol'])) for old in latest['watchlist']]
    recovery.save(root / 'all_analysis.json', analysis)
    assert not data_refresh_needed(latest, now=NOW, reports=root)
    latest['watchlist'][0]['us_delayed_bars'] = None
    assert data_refresh_needed(latest, now=NOW, reports=root)


@pytest.mark.parametrize('offset,valid', [(0, True), (1, False), (-120, True), (-121, False)])
def test_matching_generation_times_checked_by_helper_and_silent_receipt(reports, offset, valid):
    from report_delivery import record_delivery, validate_fixed_report
    from us_free_recovery_validation import healthy_free_report
    root, latest, analysis = reports
    latest['updated_at'] = analysis['updated_at'] = (NOW + timedelta(minutes=offset)).isoformat()
    latest['run_mode'] = 'scheduled_report'
    for name, value in [('latest', latest), ('all_analysis', analysis)]:
        recovery.save(root / (name + '.json'), value)
    (root / 'latest.md').write_text('synthetic report')
    assert healthy_free_report(latest, analysis, now=NOW) is valid
    assert validate_fixed_report(latest, expected_period='noon', now=NOW, reports=root)[0] is valid
    record_delivery(root, period='noon', report_updated_at=latest['updated_at'],
                    state='suppressed', delivered=False, expected_delivery=False,
                    detail='test', checked_at=NOW, relay_health={})
    receipt = json.loads((root / 'delivery_receipts/2026-10-17-noon.json').read_text())
    assert (receipt['last_generation'].get('generation_validated') is True) is valid
    assert not receipt['last_success']


@pytest.mark.parametrize('stamp,valid', [
    ('2026-10-17T09:00:00Z', True),
    ('2026-10-23T03:59:59Z', True),
    ('2026-10-23T04:00:00Z', False),
    ('2026-10-24T09:00:00Z', False),
    ('2026-10-15T19:00:00Z', False),
])
def test_standalone_acceptance_observation_age_matches_recovery(reports, stamp, valid):
    from tools.validate_free_report import summarize
    from us_free_recovery_validation import healthy_free_report
    _, latest, analysis = reports
    observed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    latest['updated_at'] = analysis['updated_at'] = observed.isoformat()
    result = summarize(analysis, SYMBOLS, observed_at=observed)
    assert result['reference_freshness_valid'] is valid
    assert result['passed'] is valid
    assert healthy_free_report(latest, analysis, now=observed) is valid


def test_paid_future_tolerance_unchanged(reports):
    from report_delivery import validate_fixed_report
    root, latest, _ = reports
    latest.update(run_mode='scheduled_report', updated_at=(NOW + timedelta(minutes=1)).isoformat())
    latest['data_status'].update(us_data_mode='paid_live', us_sip_count=187, us_opra_count=30)
    assert validate_fixed_report(latest, expected_period='noon', now=NOW, reports=root)[0]
