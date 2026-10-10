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


def test_capability_probe_dispatch_is_non_delivering_and_once(tmp_path, monkeypatch):
    from agent_stock_recovery import reserve_permission_probe, execute_permission_probe
    setup_report(tmp_path)
    state = planned(tmp_path)
    reserve_permission_probe(state, NOW, 'probe-owner', {'status': 'blocked', 'reason': 'missing OPENAI_API_KEY'})
    post = Mock(return_value=Mock())
    monkeypatch.setattr('agent_stock_recovery.requests.post', post)
    executor = GitHubExecutor('masked-test')
    execute_permission_probe(state, executor, 'wrong-owner')
    assert not post.called
    execute_permission_probe(state, executor, 'probe-owner')
    assert post.call_args.kwargs['json']['inputs']['validation_only'] == 'true'
    assert state['capabilities']['actions_write_confirmed']
    execute_permission_probe(state, executor, 'probe-owner')
    assert post.call_count == 1
    assert '程式修復模型憑證阻塞：missing OPENAI_API_KEY' in state['blockers']
    save(tmp_path / 'agent_recovery.json', state)
    verified = inspect(tmp_path, NOW, runs=[{'id': 123, 'display_title': 'Agent permission probe probe-owner',
                         'status': 'completed', 'conclusion': 'success'}], web=WEB)
    assert verified['permission_probe']['status'] == 'verified'
    assert verified['incidents']['2026-10-03:evening']['status'] != 'verified_delivered'


def test_model_permission_probe_reports_missing_and_denied_without_key_content():
    from agent_stock_recovery import model_access_probe
    get = Mock()
    assert model_access_probe('', get)['reason'] == 'missing OPENAI_API_KEY'
    assert not get.called
    error = requests.HTTPError('secret response must not be persisted')
    error.response = Mock(status_code=401)
    get.side_effect = error
    result = model_access_probe('test-secret', get)
    assert result['http_status'] == 401
    assert 'test-secret' not in json.dumps(result)
    assert 'secret response' not in json.dumps(result)


def test_validation_mode_bypasses_all_report_production():
    workflow = Path('.github/workflows/stock-briefing.yml').read_text()
    check = workflow.split('name: Prevent duplicate or expired fixed report', 1)[1].split('  briefing:', 1)[0]
    assert check.index('should_run=false') < check.index('args=(--mode gate')
    assert 'exit 0' in check.split('args=(--mode gate')[0]
    assert "needs.gate.outputs.should_run == 'true'" in workflow
    assert "inputs.validation_only == true" in workflow


def test_agent_reports_telegram_scope_without_claiming_chatgpt_failure(tmp_path):
    setup_report(tmp_path)
    state = inspect(tmp_path, NOW, runs=[], web=WEB)
    assert state['delivery_scope']['channel']=='telegram_v6'
    assert state['delivery_scope']['chatgpt_delivery_status']=='not_observable'
    assert state['delivery_scope']['cross_channel_deduplication'] is False
    assert 'missing_fixed_generation' in state['incidents']['2026-10-03:evening']['diagnosis']


class SilentExecutor(Executor):
    def dispatch(self, period, *, data_refresh=False):
        if self.failure:
            raise requests.HTTPError('403')
        self.sent.append((period, data_refresh))


def stale_data(root):
    setup_report(root)
    save(root / 'latest.json', {'period': 'evening', 'updated_at': '2026-10-02 20:00:00',
                               'data_status': {'us_sip_count': 187, 'us_opra_count': 29}})


def test_expired_report_recovers_data_without_sending_or_faking_receipt(tmp_path):
    stale_data(tmp_path)
    now = NOW.replace(hour=17)
    state = planned(tmp_path, now)
    assert state['incidents']['2026-10-03:morning']['status'] == 'expired'
    assert state['incidents']['2026-10-03:noon']['status'] == 'expired'
    assert state['next_actions'] == [{'incident_key': '2026-10-03:noon', 'period': 'noon', 'kind': 'data_refresh'}]
    executor = SilentExecutor()
    result = execute_reserved(tmp_path, now, executor, expected_run_id='test-run')
    assert executor.sent == [('noon', True)]
    assert result['data_incidents']['2026-10-03:noon']['status'] == 'awaiting_verification'
    assert not (tmp_path / 'report_delivery_status.json').exists()
    execute_reserved(tmp_path, now, executor)
    assert len(executor.sent) == 1
    save(tmp_path / 'latest.json', {'period': 'noon', 'updated_at': '2026-10-03 17:16:00',
                                 'data_status': {'us_sip_count': 190, 'us_opra_count': 28}})
    verified = inspect(tmp_path, now + timedelta(minutes=2), runs=[], web=WEB)
    assert verified['data_incidents']['2026-10-03:noon']['status'] == 'verified_fresh'
    assert verified['incidents']['2026-10-03:noon']['status'] == 'expired'
    assert verified['next_actions'] == []


def test_silent_refresh_failures_are_preserved_and_stop_after_two_attempts(tmp_path):
    stale_data(tmp_path)
    now = NOW.replace(hour=17)
    executor = SilentExecutor(failure=True)
    for offset in (0, 21):
        clock = now + timedelta(minutes=offset)
        planned(tmp_path, clock)
        result = execute_reserved(tmp_path, clock, executor, expected_run_id='test-run')
        assert result['data_incidents']['2026-10-03:noon']['status'] == 'dispatch_failed'
    stopped = inspect(tmp_path, now + timedelta(minutes=42), runs=[], web=WEB)
    assert stopped['data_recovery_status'] == 'exhausted'
    assert len(stopped['data_incidents']['2026-10-03:noon']['attempts']) == 2
    assert stopped['next_actions'] == []


@pytest.mark.parametrize('race', ['active', 'fresh', 'owner', 'midnight', 'period'])
def test_silent_refresh_rechecks_reservation_before_dispatch(tmp_path, race):
    stale_data(tmp_path)
    now = NOW.replace(hour=17)
    planned(tmp_path, now)
    if race == 'fresh':
        save(tmp_path / 'latest.json', {'period': 'noon', 'updated_at': now.isoformat(),
                                     'data_status': {'us_sip_count': 190, 'us_opra_count': 28}})
    if race == 'midnight':
        now += timedelta(days=1)
    if race == 'period':
        now = now.replace(hour=20)
    executor = SilentExecutor(active=race == 'active')
    result = execute_reserved(tmp_path, now, executor, expected_run_id='wrong' if race == 'owner' else 'test-run')
    assert executor.sent == []
    assert result['data_incidents']['2026-10-03:noon']['attempts'][-1]['state'].startswith('skipped_')


def test_missing_permissions_block_silent_refresh_and_primary_has_priority(tmp_path):
    stale_data(tmp_path)
    now = NOW.replace(hour=17)
    blocked = inspect(tmp_path, now, runs=None, web=WEB, capability_error='actions read blocked: HTTPError')
    assert blocked['data_recovery_status'] == 'blocked_permission'
    assert blocked['next_actions'] == []
    primary = inspect(tmp_path, NOW, runs=[], web=WEB)
    assert primary['next_actions'][0].get('kind') is None
    preparing = inspect(tmp_path, NOW.replace(hour=19), runs=[], web=WEB)
    assert preparing['next_actions'] == []


def test_silent_dispatch_uses_existing_workflow_and_explicit_non_delivery_input(monkeypatch):
    post = Mock(return_value=Mock())
    monkeypatch.setattr('agent_stock_recovery.requests.post', post)
    GitHubExecutor('masked-test').dispatch('noon', data_refresh=True)
    inputs = post.call_args.kwargs['json']['inputs']
    assert inputs == {'period': 'noon', 'recovery': 'true', 'data_refresh': 'true'}
    assert post.call_args.args[0].endswith('/stock-briefing.yml/dispatches')
