"""Deterministic workflow routing and fake-git checks; no external writes."""
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest

WORKFLOW = Path(__file__).parents[1] / '.github/workflows/stock-briefing.yml'
TEXT = WORKFLOW.read_text()

def job(name):
    return re.search(r'^  ' + name + r':\n(.*?)(?=^  [a-z_]+:|\Z)', TEXT, re.M | re.S).group(1)

def condition(name, flags, event='workflow_dispatch', ref='refs/heads/main', gate='true'):
    expression = re.search(r'^    if: \$\{\{ (.*?) \}\}', job(name), re.M).group(1)
    expression = re.sub(r'inputs\.(\w+)', lambda m: repr(flags.get(m[1], False)), expression)
    expression = expression.replace('github.event_name', repr(event)).replace('github.ref', repr(ref))
    expression = expression.replace('needs.gate.outputs.should_run', repr(gate))
    expression = re.sub(r'\btrue\b', 'True', expression.replace("'true'", "'TRUE_STRING'"))
    expression = re.sub(r'\bfalse\b', 'False', expression).replace("'TRUE_STRING'", "'true'")
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', ' not ', expression)
    return eval(expression, {'__builtins__': {}}, {})


def test_cache_job_only_exact_optin_and_no_production_gate():
    names = ['data_refresh', 'us_free_data', 'validation_only', 'source_validation_only',
             'us_free_report_validation', 'us_daily_history', 'tw_cohort_diagnostics', 'recovery']
    for bits in itertools.product([False, True], repeat=len(names)):
        flags = dict(zip(names, bits))
        expected = all(bits[:2]) and not any(bits[2:])
        assert condition('free_reference_cache', flags) is expected
        if all(bits[:2]):
            assert not condition('gate', flags)
            assert not condition('briefing', flags)
        if expected:
            assert not any(condition(name, flags) for name in
                ['permission_validation', 'free_report_validation', 'source_validation', 'gate', 'briefing'])
    flags = {'data_refresh': True, 'us_free_data': True}
    assert not condition('free_reference_cache', flags, event='schedule')
    assert not condition('free_reference_cache', flags, ref='refs/heads/feature')


def test_existing_source_report_paid_and_schedule_modes_preserved():
    assert condition('source_validation', {'source_validation_only': True, 'us_free_data': True})
    assert condition('free_report_validation', {'us_free_report_validation': True})
    assert condition('permission_validation', {'validation_only': True})
    assert condition('gate', {'data_refresh': True})
    assert condition('briefing', {'data_refresh': True})
    assert condition('gate', {}, event='schedule')
    assert condition('briefing', {}, event='schedule')


def test_job_least_privilege_and_only_reference_commands():
    block = job('free_reference_cache')
    assert 'contents: write' in block and 'id-token: write' in block
    assert 'actions: write' not in block
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in block
    assert 'ref: main' in block and 'timeout-minutes: 6' in block
    assert 'python tools/persist_free_daily_references.py\n' in block
    assert 'python tools/persist_free_daily_references.py --verify-cache-only' in block
    assert block.index('--verify-cache-only') < block.index('git add')
    for forbidden in ['briefing.py', 'publisher', 'telegram', 'FINMIND_TOKEN', 'ALPACA_API_',
                      'git add reports', 'git add .', 'git pull', 'git rebase', '--force',
                      'upload-artifact', 'actions/cache', 'continue-on-error']:
        assert forbidden not in block
    assert 'git add -- "$cache_path"' in block
    assert block.count('git push ') == 1
    assert 'git fetch --no-tags origin main' in block
    inputs = TEXT.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0]
    assert len(re.findall(r'^      [a-z_]+:', inputs, re.M)) == 10


def commit_script():
    block = job('free_reference_cache').split('      - name: Commit only', 1)[1]
    return textwrap.dedent(block.split('        run: |\n', 1)[1])


@pytest.mark.parametrize('mode,code,pushes', [('normal', 0, 1), ('stale', 1, 0),
    ('unexpected', 1, 0), ('unchanged', 0, 0), ('push_race', 1, 1)])
def test_scoped_commit_fails_closed_without_rebase_or_retry(tmp_path, mode, code, pushes):
    executable = tmp_path / 'git'
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
with open(os.environ['GIT_CALLS'], 'a') as f: f.write(json.dumps(args)+'\\n')
mode=os.environ['GIT_MODE']
if args[:1]==['rev-parse']: print('new' if mode=='stale' and args[1]=='FETCH_HEAD' else 'same')
if args[:3]==['diff','--name-only','-z']:
 sys.stdout.buffer.write(b'unexpected.json\\0' if mode=='unexpected' else b'reports/us_completed_daily_reference_cache.json\\0')
if args[:2]==['diff','--quiet']: sys.exit(0 if mode=='unchanged' else 1)
if args[:3]==['diff','--cached','--name-only']: print('reports/us_completed_daily_reference_cache.json')
if args[:1]==['push'] and mode=='push_race': sys.exit(1)
''')
    executable.chmod(0o755)
    calls_path = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'GIT_CALLS': str(calls_path), 'GIT_MODE': mode}
    result = subprocess.run(['bash', '-c', commit_script()], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert sum(call[0] == 'push' for call in calls) == pushes
    assert not any(call[0] in {'pull', 'rebase', 'reset'} or '--force' in call for call in calls)
    for call in calls:
        if call[0] == 'add': assert call == ['add', '--', 'reports/us_completed_daily_reference_cache.json']
    if pushes: assert ['push', 'origin', 'HEAD:refs/heads/main'] in calls
