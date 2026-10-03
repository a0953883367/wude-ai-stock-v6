"""Bounded stock maintenance executor attached to the existing system guard.

Only the existing protected briefing workflow may be dispatched. No arbitrary
shell/model output is executed and no trading/shadow policy is written here.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from agent_control import authorize_task
from briefing_watchdog import TAIPEI, TARGETS, delivery_is_current, load_daily_delivery, load_report, target_datetime, _parse_updated_at

REPOSITORY = "a0953883367/wude-ai-stock-v6"
RETRY_LIMIT = 2
COOLDOWN_MINUTES = 20
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}
PUBLIC_BASE = "https://a0953883367.github.io/wude-ai-stock-v6/"


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def probe_public(get: Callable = requests.get) -> dict:
    """Two read attempts per endpoint; HTTP/JSON validation is not browser QA."""
    results = {}
    for name, path in (("html", "decision-hub.html"), ("json", "reports/latest.json")):
        result = {"ok": False, "read_attempts": 0}
        for attempt in range(1, 3):
            result['read_attempts'] = attempt
            try:
                response = get(PUBLIC_BASE + path, timeout=12, params={"check": datetime.now().timestamp()})
                response.raise_for_status()
                if name == 'json':
                    payload = response.json()
                    if not isinstance(payload, dict) or not payload.get('updated_at'):
                        raise ValueError('invalid report JSON')
                    result['updated_at'] = payload['updated_at']
                elif 'decision_hub.js' not in response.text or 'id="filters"' not in response.text:
                    raise ValueError('required HTML nodes missing')
                result.update(ok=True, error=None)
                break
            except (requests.RequestException, ValueError) as exc:
                # Never persist raw HTTP responses, tokens or user content.
                result['error'] = type(exc).__name__
        results[name] = result
    return {"ok": all(x['ok'] for x in results.values()), "endpoints": results,
            "scope": "HTTP / JSON / required DOM nodes; not browser JavaScript execution"}


class GitHubExecutor:
    def __init__(self, token: str):
        self.token = token

    def runs(self) -> list[dict]:
        if not self.token:
            raise PermissionError('actions credential missing')
        response = requests.get(
            f'https://api.github.com/repos/{REPOSITORY}/actions/workflows/stock-briefing.yml/runs',
            headers={'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json'},
            params={'per_page': 30, 'branch': 'main'}, timeout=15,
        )
        response.raise_for_status()
        return response.json()['workflow_runs']

    def dispatch(self, period: str) -> None:
        if period not in TARGETS or not self.token or not authorize_task('stock_shadow', 'protected_briefing_retry')['executable']:
            raise PermissionError('workflow/period not authorized')
        response = requests.post(
            f'https://api.github.com/repos/{REPOSITORY}/actions/workflows/stock-briefing.yml/dispatches',
            headers={'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json'},
            json={'ref': 'main', 'inputs': {'period': period, 'recovery': 'true'}}, timeout=15,
        )
        response.raise_for_status()


def inspect(reports: Path, now: datetime, *, runs: list[dict] | None, web: dict,
            capability_error: str = '') -> dict:
    now = now.astimezone(TAIPEI)
    path = reports / 'agent_recovery.json'
    previous = load_report(path)
    incidents = previous.get('incidents')
    incidents = incidents if isinstance(incidents, dict) else {}
    delivery = load_daily_delivery(reports / 'report_delivery_status.json', now)
    latest = load_report(reports / 'latest.json')
    guard = load_report(reports / 'system_guard.json')
    failures = [x.get('code') for x in guard.get('checks', [])
                if x.get('level') in {'warning', 'critical'} and x.get('code') in {
                    'report_freshness', 'report_consistency', 'market_session_consistency', 'tw_core_data', 'analysis_output', 'live_backend', 'decision_hub'}]
    quality = latest.get('data_status') or {}
    if not latest.get('updated_at'):
        failures.append('missing_report')
    if not quality.get('us_sip_count') or not quality.get('us_opra_count'):
        failures.append('missing_sip_or_opra')
    actions = []
    active = any(r.get('status') in ACTIVE for r in runs or [])
    for period in TARGETS:
        target = target_datetime(now, period)
        key = f'{now.date().isoformat()}:{period}'
        incident = incidents.setdefault(key, {'period': period, 'attempts': [], 'status': 'waiting'})
        if delivery_is_current(delivery, period=period, target=target):
            incident.update(status='verified_recovered' if incident['attempts'] else 'verified_delivered',
                            verified_at=now.isoformat(), reason='fresh verified Telegram delivery receipt')
            continue
        if now < target + timedelta(minutes=10):
            incident.update(status='waiting_window', reason='primary delivery grace / future period')
            continue
        if now > target + timedelta(minutes=120):
            incident.update(status='expired', reason='no valid receipt; expired reports must not be sent')
            continue
        incident['diagnosis'] = sorted(set(['missing_verified_delivery'] + failures))
        if runs is None:
            incident.update(status='blocked_permission', reason=capability_error or 'cannot confirm active workflows')
            continue
        attempts = incident['attempts']
        if active:
            incident.update(status='waiting_active_run', reason='an existing briefing is active; no competing dispatch')
            continue
        if len(attempts) >= RETRY_LIMIT:
            incident.update(status='exhausted', reason='two recovery attempts used; wait for human review or next fixed period')
            continue
        last = _parse_updated_at(attempts[-1].get('at')) if attempts else None
        if last and now < last + timedelta(minutes=COOLDOWN_MINUTES):
            incident.update(status='cooldown', reason='wait for fresh evidence; accepted dispatch is not successful recovery')
            continue
        incident.update(status='ready', reason='retry existing verified briefing inside fixed delivery window')
        actions.append({'incident_key': key, 'period': period})
    state = {
        'schema': 'wude.stock_agent_recovery.v1', 'checked_at': now.isoformat(),
        'executor': 'existing system-guard workflow / stock_shadow maintenance',
        'incidents': incidents, 'next_actions': actions[:1], 'web_probe': web,
        'diagnosis': sorted(set(failures)),
        'data_recovery_status': 'waiting_next_eligible_fixed_report' if failures else 'no_required_data_fault_detected',
        'capabilities': {'actions_read_confirmed': runs is not None,
                         'protected_briefing_retry': True,
                         'actions_write_confirmed': previous.get('capabilities', {}).get('actions_write_confirmed', False),
                         'arbitrary_code_repair': False, 'paid_model_executor': False},
        'blockers': ([capability_error] if capability_error else []) +
                    ([] if web.get('ok') else ['網頁兩次讀取仍失敗；瀏覽器程式錯誤需已驗收的修復執行器']) +
                    ['一般程式自動修復尚未接通；此執行器不能生成或合併未測試程式'],
        'limits': {'recovery_attempts_per_date_period': RETRY_LIMIT, 'cooldown_minutes': COOLDOWN_MINUTES, 'max_late_minutes': 120},
        'safety': {'changes_rankings': False, 'changes_weights': False, 'changes_shadow_source_data': False,
                   'changes_promotion_thresholds': False, 'places_orders': False, 'executes_model_generated_code': False},
    }
    return state


def reserve(state: dict, now: datetime, run_id: str) -> None:
    for action in state['next_actions']:
        item = state['incidents'][action['incident_key']]
        item['attempts'].append({'at': now.isoformat(), 'guard_run_id': run_id, 'state': 'reserved'})
        item['status'] = 'reserved'


def execute_reserved(reports: Path, now: datetime, executor: GitHubExecutor) -> dict:
    state = load_report(reports / 'agent_recovery.json')
    # Recheck both delivery and active runs after reservation is published.
    runs = executor.runs()
    delivery = load_daily_delivery(reports / 'report_delivery_status.json', now)
    for action in state.get('next_actions', [])[:1]:
        period, key = action['period'], action['incident_key']
        item = state['incidents'][key]
        attempt = item['attempts'][-1]
        if item['status'] != 'reserved' or attempt['state'] != 'reserved':
            continue
        if len(item['attempts']) > RETRY_LIMIT:
            attempt['state'] = 'skipped_retry_limit'
            item['status'] = 'exhausted'
            continue
        expected_run = os.environ.get('GITHUB_RUN_ID')
        if expected_run and attempt.get('guard_run_id') != expected_run:
            attempt['state'] = 'skipped_reservation_owner'
            item['status'] = 'blocked_permission'
            continue
        target = target_datetime(now, period)
        if key != f'{now.astimezone(TAIPEI).date().isoformat()}:{period}' or now > target + timedelta(minutes=120):
            attempt['state'] = 'skipped_expired'
        elif delivery_is_current(delivery, period=period, target=target):
            attempt['state'] = 'skipped_already_delivered'
        elif any(r.get('status') in ACTIVE for r in runs):
            attempt['state'] = 'skipped_active_run'
        else:
            try:
                executor.dispatch(period)
                attempt['state'] = 'dispatch_accepted'
                state['capabilities']['actions_write_confirmed'] = True
            except (requests.RequestException, PermissionError) as exc:
                attempt['state'] = 'dispatch_failed'
                attempt['error'] = type(exc).__name__
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    attempt['http_status'] = exc.response.status_code
        item['status'] = 'awaiting_verification' if attempt['state'] == 'dispatch_accepted' else attempt['state']
    state['next_actions'] = []
    state['checked_at'] = now.isoformat()
    save(reports / 'agent_recovery.json', state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reports-dir', type=Path, default=Path('reports'))
    parser.add_argument('--execute-reserved', action='store_true')
    args = parser.parse_args()
    now = datetime.now(TAIPEI)
    executor = GitHubExecutor(os.environ.get('GH_TOKEN', ''))
    if args.execute_reserved:
        try:
            state = execute_reserved(args.reports_dir, now, executor)
        except (requests.RequestException, PermissionError) as exc:
            state = load_report(args.reports_dir / 'agent_recovery.json')
            state['blockers'] = [f'active-run check failed: {type(exc).__name__}; no dispatch performed']
            save(args.reports_dir / 'agent_recovery.json', state)
    else:
        try:
            runs, error = executor.runs(), ''
        except (requests.RequestException, PermissionError) as exc:
            runs, error = None, f'actions read blocked: {type(exc).__name__}'
        state = inspect(args.reports_dir, now, runs=runs, web=probe_public(), capability_error=error)
        reserve(state, now, os.environ.get('GITHUB_RUN_ID', 'local'))
        save(args.reports_dir / 'agent_recovery.json', state)
    print(json.dumps({'checked_at': state.get('checked_at'), 'reserved_actions': len(state.get('next_actions', [])),
                      'capabilities': state.get('capabilities')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
