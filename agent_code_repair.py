"""Approved maintenance recipes for the existing stock Agent; no arbitrary code.

Only the exact historical DOM-null bug is repairable. Unknown failures stop.
Each source fingerprint gets one persisted attempt, a separate branch and the
existing full CI. No protected financial files or report data enter the diff.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os
from datetime import datetime, timedelta
from pathlib import Path
import requests
from agent_stock_recovery import REPOSITORY, TAIPEI, load_report, save

PATH = 'decision_hub.js'
BAD = "filters.closest('.filter-details').hidden=shadowMode;"
GOOD = "var filterDetails=filters.closest('.filter-details');if(filterDetails)filterDetails.hidden=shadowMode;"
API = f'https://api.github.com/repos/{REPOSITORY}'
# One authorized recheck after the owner enabled Actions PR creation on 2026-10-03.
AUTHORIZED_RECHECK_OWNER = '37121682596'


def repair(source: str) -> str | None:
    if source.count(BAD) != 1 or GOOD in source:
        return None
    return source.replace(BAD, GOOD, 1)


class GitHub:
    def __init__(self, token):
        self.token = token
    def call(self, method, path, body=None):
        if not self.token:
            raise PermissionError('missing workflow GitHub token')
        response = requests.request(method, API + path,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json'},
            json=body, timeout=20)
        response.raise_for_status()
        return response.json() if response.content else {}
    def file(self, path, ref):
        data = self.call('GET', f'/contents/{path}?ref={ref}')
        return data, base64.b64decode(data['content']).decode()


def plan(root: Path, run_id: str) -> dict:
    state = load_report(root / 'reports/agent_code_repair.json')
    state.setdefault('attempts', {})
    state.setdefault('permission_probe', {})
    probe = state['permission_probe']
    if (probe.get('status') == 'blocked_stopped'
            and probe.get('owner') == AUTHORIZED_RECHECK_OWNER
            and not state.get('permission_recheck_20261003')):
        state.setdefault('permission_probe_history', []).append(dict(probe))
        state['permission_recheck_20261003'] = {'reason': 'owner confirmed Actions PR setting enabled', 'owner': run_id}
        state['permission_probe'] = {}
    if not state['permission_probe']:
        state['permission_probe'] = {'status': 'reserved', 'owner': run_id}
    source = (root / PATH).read_text()
    fixed = repair(source)
    state['checked_at'] = datetime.now(TAIPEI).isoformat()
    state['scope'] = 'exact approved DOM-null repair only; unknown bugs require review'
    state['allowed_paths'] = [PATH]
    if fixed:
        fingerprint = hashlib.sha256(source.encode()).hexdigest()
        if fingerprint not in state['attempts']:
            state['attempts'][fingerprint] = {'status': 'reserved', 'owner': run_id, 'started_at': state['checked_at']}
        state['candidate'] = fingerprint
    elif 'candidate' not in state:
        state['status'] = 'healthy_no_matching_fault'
    save(root / 'reports/agent_code_repair.json', state)
    return state


def permission_probe(state, api, run_id):
    probe = state['permission_probe']
    if probe.get('status') != 'reserved' or probe['owner'] != run_id:
        return
    probe['status'] = 'started'
    try:
        main = api.call('GET', '/git/ref/heads/main')['object']['sha']
        branch = 'agent-repair/permission-' + run_id
        api.call('POST', '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': main})
        probe['branch_write_verified'] = True
        api.call('PUT', '/contents/tests/fixtures/agent_repair_permission_probe.json', {
            'branch': branch, 'message': 'test: isolated Agent PR permission probe',
            'content': base64.b64encode(b'{"validation_only":true,"production_changed":false}\n').decode()})
        pull = api.call('POST', '/pulls', {'head': branch, 'base': 'main', 'draft': True,
            'title': 'Agent 修復權限驗證（不合併）',
            'body': 'Only an isolated test fixture. No stock logic changes. This probe is closed without merging.'})
        probe.update(pr_write_verified=True, pr_number=pull['number'], url=pull['html_url'])
        api.call('PATCH', '/pulls/' + str(pull['number']), {'state': 'closed'})
        probe['status'] = 'verified_branch_and_pr'
    except (requests.RequestException, PermissionError) as exc:
        probe.update(status='blocked_stopped', reason=type(exc).__name__,
            http_status=getattr(getattr(exc, 'response', None), 'status_code', None),
            required_permission='contents:write; pull-requests:write; repository Allow GitHub Actions to create pull requests')


def _advance(state, api, run_id):
    key = state.get('candidate')
    if not key:
        return
    item = state['attempts'][key]
    if state['permission_probe'].get('status') != 'verified_branch_and_pr':
        item.update(status='blocked_permission')
        return
    if item['status'] in {'waiting_full_ci', 'waiting_public_verification'} and item.get('started_at') and datetime.now(TAIPEI) > datetime.fromisoformat(item['started_at']) + timedelta(minutes=120):
        item['status'] = 'stopped_verification_timeout'
        return
    if item['status'] == 'reserved' and item['owner'] == run_id:
        data, source = api.file(PATH, 'main')
        fixed = repair(source)
        if hashlib.sha256(source.encode()).hexdigest() != key or not fixed:
            item['status'] = 'stopped_source_changed'
            return
        main = api.call('GET', '/git/ref/heads/main')['object']['sha']
        branch = 'agent-repair/dom-null-' + key[:16]
        api.call('POST', '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': main})
        commit = api.call('PUT', '/contents/' + PATH, {'branch': branch, 'sha': data['sha'],
            'message': 'fix: guard missing optional filter wrapper',
            'content': base64.b64encode(fixed.encode()).decode()})
        item.update(branch=branch, base_blob=data['sha'], head=commit['commit']['sha'])
        pull = api.call('POST', '/pulls', {'head': branch, 'base': 'main',
            'title': 'Agent 安全修復：選單容器不存在時不中斷讀取',
            'body': 'Exact approved DOM-null recipe. Protected stock policies and data are unchanged. Full existing CI must pass before merge.'})
        item.update(pr_number=pull['number'], url=pull['html_url'])
        api.call('POST', '/actions/workflows/ci.yml/dispatches', {'ref': branch})
        item['status'] = 'waiting_full_ci'
    elif item['status'] == 'waiting_full_ci':
        runs = api.call('GET', '/actions/workflows/ci.yml/runs?per_page=50')['workflow_runs']
        matching = [r for r in runs if r['head_sha'] == item['head'] and r['event'] == 'workflow_dispatch']
        if not matching or matching[0]['status'] != 'completed':
            return
        item['ci_url'] = matching[0]['html_url']
        if matching[0]['conclusion'] != 'success':
            item['status'] = 'stopped_ci_failed'
            return
        current, source = api.file(PATH, 'main')
        _, candidate = api.file(PATH, item['branch'])
        files = api.call('GET', f"/pulls/{item['pr_number']}/files")
        pull = api.call('GET', f"/pulls/{item['pr_number']}")
        if current['sha'] != item['base_blob'] or candidate != repair(source) or \
           [f['filename'] for f in files] != [PATH] or pull['head']['sha'] != item['head']:
            item['status'] = 'stopped_diff_changed'
            return
        merged = api.call('PUT', f"/pulls/{item['pr_number']}/merge", {'sha': item['head'], 'merge_method': 'squash'})
        if not merged.get('merged'):
            item['status'] = 'blocked_merge_permission'
            return
        item.update(status='merged_waiting_publish', merge_sha=merged['sha'])
        api.call('POST', '/pages/builds', {})
        item['status'] = 'waiting_public_verification'
    elif item['status'] == 'waiting_public_verification':
        response = requests.get(f'https://a0953883367.github.io/wude-ai-stock-v6/{PATH}', timeout=20)
        response.raise_for_status()
        _, current = api.file(PATH, 'main')
        if response.text == current and GOOD in current and BAD not in current:
            item['status'] = 'verified_published'
    state['status'] = item['status']


def advance(state, api, run_id):
    try:
        _advance(state, api, run_id)
    finally:
        if state.get('candidate'):
            state['status'] = state['attempts'][state['candidate']]['status']


def execute(root, run_id, api):
    path = root / 'reports/agent_code_repair.json'
    state = load_report(path)
    try:
        permission_probe(state, api, run_id)
        if state['permission_probe'].get('status') == 'blocked_stopped':
            state['status'] = 'blocked_permission'
        advance(state, api, run_id)
    except (requests.RequestException, PermissionError) as exc:
        state.update(status='blocked_stopped', reason=type(exc).__name__,
            http_status=getattr(getattr(exc, 'response', None), 'status_code', None))
        if state.get('candidate'):
            state['attempts'][state['candidate']]['status'] = 'blocked_stopped'
    state['checked_at'] = datetime.now(TAIPEI).isoformat()
    save(path, state)
    print(json.dumps({'status': state.get('status'), 'permission_probe': state['permission_probe']}, ensure_ascii=False))
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    run_id = os.environ.get('GITHUB_RUN_ID', 'local')
    if args.execute:
        execute(Path('.'), run_id, GitHub(os.environ.get('GH_TOKEN', '')))
    else:
        plan(Path('.'), run_id)
