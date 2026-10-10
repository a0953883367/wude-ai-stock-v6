"""Stock-only notification policy and source-only isolation regressions."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from briefing_watchdog import (delivery_is_current, generation_is_current,
                              load_daily_delivery, recovery_decision, scheduled_gate_decision)
from report_delivery import deliver_verified_report, record_delivery

TZ = ZoneInfo('Asia/Taipei')
AT = datetime(2026, 10, 3, 5, 45, tzinfo=TZ)
TARGET = AT.replace(hour=6, minute=0)


def generate(path, at=AT, period='morning', valid=True):
    report = {'period': period, 'updated_at': at.isoformat(), 'run_mode': 'scheduled_report',
              'data_status': {'us_sip_count': 1 if valid else 0, 'us_opra_count': 1}}
    (path / 'latest.json').write_text(json.dumps(report))
    (path / 'latest.md').write_text('stock report')
    return record_delivery(path, period=period, report_updated_at=at.isoformat(),
                           state='suppressed', delivered=False, expected_delivery=False,
                           detail='Telegram disabled', checked_at=at)


def test_silent_generation_deduplicates_without_claiming_delivery(tmp_path):
    generate(tmp_path)
    evidence = load_daily_delivery(tmp_path / 'report_delivery_status.json', AT)
    assert generation_is_current(evidence, period='morning', target=TARGET)
    assert not delivery_is_current(evidence, period='morning', target=TARGET)
    assert not scheduled_gate_decision({}, now=AT, schedule='55 21 * * *', delivery=evidence)[0]
    assert not recovery_decision({}, now=TARGET + timedelta(minutes=15), period='morning', delivery=evidence)[0]
    receipt = json.loads((tmp_path / 'delivery_receipts/2026-10-03-morning.json').read_text())
    assert receipt['last_success'] == {}
    assert receipt['last_generation']['delivered'] is False


@pytest.mark.parametrize('invalid', ['incomplete', 'stale', 'future', 'not_fixed'])
def test_invalid_suppressed_generation_does_not_suppress_recovery(tmp_path, invalid):
    at = AT if invalid != 'stale' else AT - timedelta(days=1)
    if invalid == 'future':
        at = AT + timedelta(days=1)
    generate(tmp_path, at=at, valid=invalid != 'incomplete')
    if invalid == 'not_fixed':
        report = json.loads((tmp_path / 'latest.json').read_text())
        report['run_mode'] = 'intraday'
        (tmp_path / 'latest.json').write_text(json.dumps(report))
        (tmp_path / 'delivery_receipts/2026-10-03-morning.json').unlink()
        record_delivery(tmp_path, period='morning', report_updated_at=at.isoformat(),
                        state='suppressed', delivered=False, expected_delivery=False,
                        detail='silent', checked_at=at)
    evidence = load_daily_delivery(tmp_path / 'report_delivery_status.json', AT)
    assert not generation_is_current(evidence, period='morning', target=TARGET)
    assert recovery_decision({}, now=TARGET + timedelta(minutes=15), period='morning', delivery=evidence)[0]


def test_later_period_preserves_silent_generation_and_no_success(tmp_path):
    generate(tmp_path)
    generate(tmp_path, at=AT.replace(hour=11), period='noon')
    evidence = load_daily_delivery(tmp_path / 'report_delivery_status.json', AT.replace(hour=12))
    assert generation_is_current(evidence, period='morning', target=TARGET)
    assert not delivery_is_current(evidence, period='morning', target=TARGET)


def test_even_injected_sender_and_health_are_never_contacted(tmp_path, monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-sentinel-never-used')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'test-sentinel-never-used')
    generate(tmp_path)
    fail = lambda *a, **kw: pytest.fail('stock delivery must be network silent')
    assert not deliver_verified_report(tmp_path, period='morning', now=AT, sender=fail, health_get=fail)


def test_source_only_job_is_bounded_and_has_no_production_side_effects():
    text = Path('.github/workflows/stock-briefing.yml').read_text()
    job = text.split('  source_validation:\n')[1].split('  gate:\n')[0]
    assert 'inputs.source_validation_only == true' in job
    assert 'inputs.validation_only != true' in job
    assert 'contents: read' in job and 'id-token: write' in job
    assert 'persist-credentials: false' in job
    assert 'timeout-minutes: 6' in job
    assert job.count('python tools/probe_fubon_daily_history.py') == 1
    assert 'COLLECT_US_DAILY_HISTORY: ${{ inputs.us_daily_history == true }}' in job
    assert 'python tools/collect_us_daily_shadow_status.py "${args[@]}"' in job
    assert 'cat reports/us_daily_shadow_status.json' in job
    for forbidden in ('secrets.', 'git push', 'git commit', 'briefing.py', 'report_delivery.py',
                      'publish_', 'upload-artifact', 'cache/save', 'contents: write', 'actions: write'):
        assert forbidden not in job
    assert "  gate:\n    if: ${{ inputs.source_validation_only != true }}" in text
    assert "inputs.source_validation_only != true && needs.gate.outputs.should_run == 'true'" in text
    assert 'TELEGRAM_BOT_TOKEN' not in text and 'TELEGRAM_CHAT_ID' not in text
    assert 'python report_delivery.py' not in text
    assert 'send_telegram' not in Path('briefing.py').read_text()
    assert 'def send_telegram(' in Path('notifier.py').read_text()  # Other alerts untouched.


def test_agent_rechecks_validated_silent_generation_before_dispatch(tmp_path, monkeypatch):
    from agent_stock_recovery import inspect, reserve, execute_reserved, save
    monkeypatch.setattr('agent_stock_recovery.inspect_us_direction', lambda *a, **kw: {'needs_silent_refresh': False, 'status': 'verified_current'})
    now = TARGET + timedelta(minutes=15)
    state = inspect(tmp_path, now, runs=[], web={'ok': True})
    assert state['next_actions'] == [{'incident_key': '2026-10-03:morning', 'period': 'morning'}]
    reserve(state, now, 'suppression-test')
    save(tmp_path / 'agent_recovery.json', state)
    generate(tmp_path)

    class Executor:
        def runs(self):
            return []
        def dispatch(self, *args, **kwargs):
            pytest.fail('validated silent generation must prevent repeat dispatch')

    result = execute_reserved(tmp_path, now, Executor(), expected_run_id='suppression-test')
    assert result['incidents']['2026-10-03:morning']['status'] == 'skipped_generated_suppressed'
    after = inspect(tmp_path, now, runs=[], web={'ok': True})
    assert after['incidents']['2026-10-03:morning']['status'] == 'generated_suppressed'
    assert after['next_actions'] == []
