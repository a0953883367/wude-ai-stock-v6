import hashlib
from unittest.mock import Mock
from pathlib import Path
from agent_code_repair import BAD, GOOD, repair, plan, advance, permission_probe


def test_exact_dom_recipe_does_not_change_surrounding_logic():
    source = 'protectedRanking();' + BAD + 'protectedWeights();'
    assert repair(source) == 'protectedRanking();' + GOOD + 'protectedWeights();'
    assert repair(GOOD) is None
    assert repair(BAD + BAD) is None
    assert repair('unknown bug') is None


def test_one_attempt_per_source_fingerprint_and_no_unapproved_path(tmp_path):
    (tmp_path / 'decision_hub.js').write_text(BAD)
    first = plan(tmp_path, 'one')
    second = plan(tmp_path, 'two')
    assert len(second['attempts']) == 1
    assert second['attempts'][second['candidate']]['owner'] == 'one'
    assert second['allowed_paths'] == ['decision_hub.js']


def test_permission_probe_never_merges_and_keeps_denied_reason():
    import requests
    api = Mock()
    api.call.side_effect = [{'object': {'sha': 'base'}}, {}, {}, {'number': 123, 'html_url': 'pr'}, {}]
    state = {'permission_probe': {'status': 'reserved', 'owner': 'test'}}
    permission_probe(state, api, 'test')
    assert state['permission_probe']['status'] == 'verified_branch_and_pr'
    assert api.call.call_args.args == ('PATCH', '/pulls/123', {'state': 'closed'})
    assert not any('/merge' in c.args[1] for c in api.call.call_args_list)
    api.call.side_effect = requests.HTTPError()
    state = {'permission_probe': {'status': 'reserved', 'owner': 'test'}}
    permission_probe(state, api, 'test')
    assert state['permission_probe']['status'] == 'blocked_stopped'
    assert 'Allow GitHub Actions' in state['permission_probe']['required_permission']


def pending():
    return {'candidate': 'key', 'permission_probe': {'status': 'verified_branch_and_pr'},
            'attempts': {'key': {'status': 'waiting_full_ci', 'head': 'expected', 'base_blob': 'blob',
                                 'branch': 'branch', 'pr_number': 123}}}


def test_failed_ci_stops_without_merge():
    api = Mock()
    api.call.return_value = {'workflow_runs': [{'head_sha': 'expected', 'event': 'workflow_dispatch',
       'status': 'completed', 'conclusion': 'failure', 'html_url': 'ci'}]}
    state = pending()
    advance(state, api, 'test')
    assert state['status'] == 'stopped_ci_failed'
    assert api.call.call_count == 1


def test_changed_or_protected_diff_cannot_be_merged():
    api = Mock()
    api.file.side_effect = [({'sha': 'blob'}, BAD), ({}, GOOD)]
    api.call.side_effect = [{'workflow_runs': [{'head_sha': 'expected', 'event': 'workflow_dispatch',
       'status': 'completed', 'conclusion': 'success', 'html_url': 'ci'}]},
       [{'filename': 'decision_hub.js'}, {'filename': 'strategy.py'}], {'head': {'sha': 'expected'}}]
    state = pending()
    advance(state, api, 'test')
    assert state['status'] == 'stopped_diff_changed'
    assert not any(c.args[0] == 'PUT' for c in api.call.call_args_list)


def test_merge_only_after_full_ci_and_exact_diff_then_verify_publish():
    api = Mock()
    api.file.side_effect = [({'sha': 'blob'}, BAD), ({}, GOOD)]
    api.call.side_effect = [{'workflow_runs': [{'head_sha': 'expected', 'event': 'workflow_dispatch',
       'status': 'completed', 'conclusion': 'success', 'html_url': 'ci'}]},
       [{'filename': 'decision_hub.js'}], {'head': {'sha': 'expected'}}, {'merged': True, 'sha': 'merge'}, {}]
    state = pending()
    advance(state, api, 'test')
    assert state['status'] == 'waiting_public_verification'
    assert api.call.call_args.args == ('POST', '/pages/builds', {})


def test_existing_workflow_persists_code_reservation_before_execution():
    workflow = Path('.github/workflows/system-guard.yml').read_text()
    assert workflow.index('python agent_code_repair.py\n') < workflow.index('git add reports/agent_recovery.json reports/agent_code_repair.json') < workflow.index('python agent_code_repair.py --execute')
    assert 'pull-requests: write' in workflow and 'pages: write' in workflow
    assert 'workflow_dispatch:' in Path('.github/workflows/ci.yml').read_text()


def test_waiting_ci_has_deadline_and_stops_without_more_api_calls():
    from datetime import datetime, timedelta
    from agent_code_repair import TAIPEI
    state = pending()
    state['attempts']['key']['started_at'] = (datetime.now(TAIPEI) - timedelta(hours=3)).isoformat()
    api = Mock()
    advance(state, api, 'test')
    assert state['status'] == 'stopped_verification_timeout'
    assert not api.call.called


def test_authorized_permission_recheck_preserves_failure_and_only_reserves_once(tmp_path):
    from agent_code_repair import AUTHORIZED_RECHECK_OWNER, save
    old = {'status': 'blocked_stopped', 'owner': AUTHORIZED_RECHECK_OWNER, 'http_status': 403}
    (tmp_path / 'decision_hub.js').write_text(GOOD)
    save(tmp_path / 'reports/agent_code_repair.json', {'permission_probe': old})
    first = plan(tmp_path, 'new-run')
    assert first['permission_probe_history'] == [old]
    assert first['permission_probe'] == {'status': 'reserved', 'owner': 'new-run'}
    first['permission_probe']['status'] = 'blocked_stopped'
    save(tmp_path / 'reports/agent_code_repair.json', first)
    second = plan(tmp_path, 'later-run')
    assert second['permission_probe']['owner'] == 'new-run'
    assert second['permission_probe']['status'] == 'blocked_stopped'
    assert len(second['permission_probe_history']) == 1


def test_other_permission_failures_remain_stopped(tmp_path):
    from agent_code_repair import save
    old = {'status': 'blocked_stopped', 'owner': 'unrelated', 'http_status': 403}
    (tmp_path / 'decision_hub.js').write_text(GOOD)
    save(tmp_path / 'reports/agent_code_repair.json', {'permission_probe': old})
    state = plan(tmp_path, 'new-run')
    assert state['permission_probe'] == old
    assert not state.get('permission_probe_history')
