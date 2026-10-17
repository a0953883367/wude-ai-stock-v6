import json
import sys
from pathlib import Path

import pytest
import briefing_watchdog as watchdog
import agent_stock_recovery as recovery
from tests.test_free_recovery_guards import reports, NOW


def paid_report(mode='paid_live'):
    quality = {'us_sip_count': 187, 'us_opra_count': 30}
    if mode is not None:
        quality['us_data_mode'] = mode
    return {'updated_at': NOW.isoformat(), 'period': 'noon', 'data_status': quality}


@pytest.mark.parametrize('actual', ['paid_live', None, '', 'invalid'])
@pytest.mark.parametrize('expected', ['free_delayed', ' FREE_DELAYED '])
def test_healthy_paid_or_historical_missing_mode_refreshes_for_free(actual, expected):
    report = paid_report(actual)
    assert watchdog.data_refresh_needed(report, now=NOW, expected_mode=expected)
    assert watchdog.us_data_fault(report, now=NOW, expected_mode=expected) == 'us_data_mode_mismatch'


@pytest.mark.parametrize('expected', ['paid_live', ' PAID_LIVE ', '', 'invalid', None])
def test_paid_defaults_deduplicate_but_require_both_feeds(expected):
    report = paid_report(None)
    assert not watchdog.data_refresh_needed(report, now=NOW, expected_mode=expected)
    for field in ['us_sip_count', 'us_opra_count']:
        broken = paid_report()
        broken['data_status'][field] = 0
        assert watchdog.data_refresh_needed(broken, now=NOW, expected_mode=expected)


def test_free_same_mode_deduplicates_reverse_transition_refreshes(reports):
    root, latest, _ = reports
    assert not watchdog.silent_refresh_needed(latest, reports=root, now=NOW, expected_mode='free_delayed')
    assert watchdog.data_refresh_needed(latest, reports=root, now=NOW, expected_mode='paid_live')
    latest['watchlist'][0]['price'] = None
    assert watchdog.data_refresh_needed(latest, reports=root, now=NOW, expected_mode='free_delayed')


def test_cli_quiet_gate_reads_configured_mode(reports, monkeypatch, capsys):
    root, _, _ = reports
    recovery.save(root / 'latest.json', paid_report())
    monkeypatch.setattr(sys, 'argv', ['briefing_watchdog.py', '--mode', 'gate', '--data-refresh',
        '--report', str(root / 'latest.json'), '--period', 'noon', '--now', NOW.isoformat()])
    for mode, expected in [('free_delayed', 'true'), ('paid_live', 'false'), ('invalid', 'false')]:
        monkeypatch.setenv('ALPACA_DATA_MODE', mode)
        assert watchdog.main() == 0
        assert json.loads(capsys.readouterr().out)['should_run'] == expected


def test_recovery_diagnosis_and_reserved_recheck_use_current_config(reports, monkeypatch):
    from unittest.mock import Mock
    root, _, _ = reports
    recovery.save(root / 'latest.json', paid_report())
    state = recovery.inspect(root, NOW, runs=[], web={'ok': True})
    assert 'us_data_mode_mismatch' in state['diagnosis']
    assert state['next_actions'][0]['kind'] == 'data_refresh'
    recovery.reserve(state, NOW, 'transition')
    recovery.save(root / 'agent_recovery.json', state)
    # A configuration reversal after reservation must cancel needless refresh.
    monkeypatch.setenv('ALPACA_DATA_MODE', 'paid_live')
    executor = Mock()
    executor.runs.return_value = []
    result = recovery.execute_reserved(root, NOW, executor, expected_run_id='transition')
    executor.dispatch.assert_not_called()
    key = state['next_actions'][0]['incident_key']
    assert result['data_incidents'][key]['attempts'][-1]['state'] == 'skipped_already_fresh'


def test_gate_and_recovery_receive_same_repository_mode_as_generator():
    root = Path(__file__).parents[1]
    workflow = (root / '.github/workflows/stock-briefing.yml').read_text()
    gate = workflow.split('  gate:\n', 1)[1].split('  briefing:\n', 1)[0]
    assignment = "ALPACA_DATA_MODE: ${{ vars.ALPACA_DATA_MODE || 'paid_live' }}"
    assert assignment in gate
    assert assignment in workflow.split('  briefing:\n', 1)[1]
    guard = (root / '.github/workflows/system-guard.yml').read_text()
    for name in ['Diagnose and reserve bounded stock recovery', 'Execute only the reserved protected recovery']:
        assert assignment in guard.split('- name: ' + name, 1)[1].split('- name:', 1)[0]
