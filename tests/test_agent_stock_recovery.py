from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
import json

import pytest
import requests

from agent_stock_recovery import inspect, reserve, execute_reserved, save, probe_public, GitHubExecutor, RETRY_LIMIT
from briefing_watchdog import TAIPEI

NOW = datetime(2026, 10, 3, 20, 15, tzinfo=TAIPEI)
WEB = {'ok': True, 'endpoints': {}, 'scope': 'test'}


def setup_report(root: Path):
    save(root / 'latest.json', {'period': 'evening', 'updated_at': '2026-10-03 20:05:00',
                               'data_status': {'us_sip_count': 187, 'us_opra_count': 29}})
    save(root / 'system_guard.json', {'checks': []})


def receipt(root: Path):
    save(root / 'delivery_receipts/2026-10-03-evening.json', {'last_success': {
        'period': 'evening', 'channel': 'telegram_v6', 'state': 'delivered', 'delivered': True,
        'report_updated_at': '2026-10-03 20:05:00', 'checked_at': '2026-10-03T20:06:00+08:00'}})


def planned(root, now=NOW):
    state = inspect(root, now, runs=[], web=WEB)
    reserve(state, now, 'test-run')
    save(root / 'agent_recovery.json', state)
    return state


class Executor:
    def __init__(self, active=False, failure=False):
        self.active, self.failure, self.sent = active, failure, []

    def runs(self):
        return [{'status': 'queued'}] if self.active else []

    def dispatch(self, period):
        if self.failure:
            raise requests.HTTPError('403')
        self.sent.append(period)


def test_missing_report_is_diagnosed_and_only_existing_workflow_is_reserved(tmp_path):
    state = planned(tmp_path)
    assert state['next_actions'] == [{'incident_key': '2026-10-03:evening', 'period': 'evening'}]
    assert 'missing_report' in state['diagnosis']
    assert 'missing_sip_or_opra' in state['diagnosis']
    assert all(v is False for v in state['safety'].values())
    assert state['incidents']['2026-10-03:evening']['attempts'][0]['state'] == 'reserved'


def test_accepted_dispatch_needs_new_evidence_and_is_not_duplicated(tmp_path):
    setup_report(tmp_path)
    planned(tmp_path)
    executor = Executor()
    state = execute_reserved(tmp_path, NOW, executor)
    assert executor.sent == ['evening']
    assert state['incidents']['2026-10-03:evening']['status'] == 'awaiting_verification'
    execute_reserved(tmp_path, NOW, executor)
    assert executor.sent == ['evening']
    after = inspect(tmp_path, NOW + timedelta(minutes=1), runs=[], web=WEB)
    assert after['next_actions'] == []
    assert after['incidents']['2026-10-03:evening']['status'] == 'cooldown'
    receipt(tmp_path)
    verified = inspect(tmp_path, NOW + timedelta(minutes=2), runs=[], web=WEB)
    assert verified['incidents']['2026-10-03:evening']['status'] == 'verified_recovered'
    assert verified['next_actions'] == []


def test_two_failures_stop_across_separate_invocations(tmp_path):
    setup_report(tmp_path)
    executor = Executor(failure=True)
    for offset in (0, 21):
        now = NOW + timedelta(minutes=offset)
        planned(tmp_path, now)
        state = execute_reserved(tmp_path, now, executor)
        assert state['incidents']['2026-10-03:evening']['status'] == 'dispatch_failed'
    stopped = inspect(tmp_path, NOW + timedelta(minutes=42), runs=[], web=WEB)
    assert stopped['incidents']['2026-10-03:evening']['status'] == 'exhausted'
    assert len(stopped['incidents']['2026-10-03:evening']['attempts']) == RETRY_LIMIT
    assert stopped['next_actions'] == []


@pytest.mark.parametrize('race', ['active', 'delivered', 'midnight'])
def test_rechecks_before_side_effect_and_never_sends_expired_report(tmp_path, race):
    setup_report(tmp_path)
    planned(tmp_path)
    now = NOW
    if race == 'delivered':
        receipt(tmp_path)
    if race == 'midnight':
        now = datetime(2026, 10, 4, 0, 1, tzinfo=TAIPEI)
    executor = Executor(active=race == 'active')
    state = execute_reserved(tmp_path, now, executor)
    assert executor.sent == []
    assert state['incidents']['2026-10-03:evening']['attempts'][0]['state'].startswith('skipped_')


def test_missing_actions_permission_blocks_dispatch_with_reason(tmp_path):
    setup_report(tmp_path)
    state = inspect(tmp_path, NOW, runs=None, web=WEB, capability_error='actions read blocked: HTTPError')
    assert state['next_actions'] == []
    assert state['incidents']['2026-10-03:evening']['status'] == 'blocked_permission'
    assert state['blockers'][0] == 'actions read blocked: HTTPError'
    with pytest.raises(PermissionError):
        GitHubExecutor('').runs()
    with pytest.raises(PermissionError):
        GitHubExecutor('test').dispatch('broker_order')


def test_active_report_and_future_period_do_not_dispatch(tmp_path):
    setup_report(tmp_path)
    state = inspect(tmp_path, NOW, runs=[{'status': 'in_progress'}], web=WEB)
    assert state['next_actions'] == []
    assert state['incidents']['2026-10-03:evening']['status'] == 'waiting_active_run'
    future = inspect(tmp_path, NOW.replace(hour=19), runs=[], web=WEB)
    assert future['next_actions'] == []


def test_web_read_retries_and_retains_explicit_permanent_failure():
    html = Mock(text='<script src="decision_hub.js"></script><div id="filters"></div>')
    html.raise_for_status.return_value = None
    data = Mock()
    data.raise_for_status.return_value = None
    data.json.return_value = {'updated_at': '2026-10-03 12:00:00'}
    get = Mock(side_effect=[requests.Timeout(), html, data])
    recovered = probe_public(get)
    assert recovered['ok'] is True
    assert recovered['endpoints']['html']['read_attempts'] == 2
    failed = probe_public(Mock(side_effect=requests.Timeout()))
    assert failed['ok'] is False
    assert all(r['read_attempts'] == 2 for r in failed['endpoints'].values())
    assert 'not browser' in failed['scope']


def test_workflow_persists_reservation_before_dispatch_and_reuses_guard():
    workflow = Path('.github/workflows/system-guard.yml').read_text()
    assert workflow.index('Publish reservation before any external action') < workflow.index('python agent_stock_recovery.py --execute-reserved')
    assert 'gh workflow run stock-briefing.yml' not in workflow
    assert 'actions: write' in workflow
    assert 'reports/agent_recovery.json' in workflow
    assert 'if: ${{ always() }}' in workflow


def test_only_reserving_workflow_may_execute_the_action(tmp_path):
    setup_report(tmp_path)
    planned(tmp_path)
    executor = Executor()
    state = execute_reserved(tmp_path, NOW, executor, expected_run_id='another-run')
    assert executor.sent == []
    assert state['incidents']['2026-10-03:evening']['status'] == 'blocked_permission'


def test_execution_rechecks_retry_limit_even_if_plan_was_modified(tmp_path):
    setup_report(tmp_path)
    state = planned(tmp_path)
    item = state['incidents']['2026-10-03:evening']
    item['attempts'].extend([dict(item['attempts'][0]), dict(item['attempts'][0])])
    save(tmp_path / 'agent_recovery.json', state)
    executor = Executor()
    result = execute_reserved(tmp_path, NOW, executor)
    assert executor.sent == []
    assert result['incidents']['2026-10-03:evening']['status'] == 'exhausted'
