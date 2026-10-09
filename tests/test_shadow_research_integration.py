from datetime import datetime, timezone
import json
from pathlib import Path

from briefing import _preserve_alpaca_daily_samples_safely
from decision_hub import _shadow_event_summary
from trade_plan_shadow import build_trade_plan_report, write_trade_plan_report
from test_us_daily_shadow_sample import calendar, snapshot, at
from us_daily_shadow_sample import build_us_daily_shadow_sample


def test_snapshot_archive_stays_private_and_is_idempotent(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    cal = calendar(tmp_path)
    (reports / 'official_market_calendar.json').write_bytes((tmp_path / 'calendar.json').read_bytes())
    observed = at('2026-10-09T16:00:00Z')
    samples = {'AAPL': build_us_daily_shadow_sample('AAPL', snapshot(), feed='sip', calendar=cal, observed_at=observed)}
    before = json.dumps(samples, sort_keys=True)
    assert _preserve_alpaca_daily_samples_safely(reports, samples, now=observed)
    assert _preserve_alpaca_daily_samples_safely(reports, samples, now=observed)
    paths = list((tmp_path / '.alpaca_daily_shadow').glob('*.json'))
    assert len(paths) == 1
    saved = json.loads(paths[0].read_text())
    assert saved['samples']['AAPL']['selected']['provider_field'] == 'prevDailyBar'
    assert saved['decision_eligible'] is saved['history_ready'] is False
    assert saved['private_only'] is True
    assert sorted(p.name for p in reports.iterdir()) == ['official_market_calendar.json']
    assert json.dumps(samples, sort_keys=True) == before


def test_private_capture_never_retroactively_closes_forming_sample(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    cal = calendar(tmp_path)
    (reports / 'official_market_calendar.json').write_bytes((tmp_path / 'calendar.json').read_bytes())
    captured = at('2026-10-09T16:00:00Z')
    samples = {'AAPL': build_us_daily_shadow_sample('AAPL', snapshot(), feed='sip', calendar=cal, observed_at=captured)}
    assert _preserve_alpaca_daily_samples_safely(reports, samples, now=at('2026-10-09T21:00:00Z'))
    path = next((tmp_path / '.alpaca_daily_shadow').glob('*.json'))
    saved = json.loads(path.read_text())['samples']['AAPL']
    assert saved['observed_at'] == captured.isoformat()
    assert saved['selected']['session_date'] == '2026-10-08'
    assert saved['candidates'][0]['session_closed'] is False


def test_empty_or_future_sample_is_not_reported_as_preserved(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    assert not _preserve_alpaca_daily_samples_safely(reports, {})
    cal = calendar(tmp_path)
    samples = {'AAPL': build_us_daily_shadow_sample('AAPL', snapshot(), feed='sip', calendar=cal,
                                                   observed_at=at('2026-10-09T22:00:00Z'))}
    assert not _preserve_alpaca_daily_samples_safely(reports, samples, now=at('2026-10-09T21:00:00Z'))
    assert not (tmp_path / '.alpaca_daily_shadow').exists()


def test_events_integration_honors_report_clock_and_does_not_publish_payload():
    row = {'symbol': 'AAPL', 'news_scanned_at': '2026-10-09T10:00:00Z',
           'news_articles': [{'title': 'Example', 'publisher': 'Example', 'published_at': '2026-10-09', 'url': 'https://example.com/news'}]}
    summary = _shadow_event_summary(row, '2026-10-09 18:00:00')
    assert summary['cutoff'].startswith('2026-10-09T10:00:00')
    assert summary['counts']['input'] == 1
    assert summary['counts']['eligible_events'] == 0
    assert summary['affects_scores'] is False
    assert 'available_revision_history' not in summary
    assert 'payload' not in json.dumps(summary)
    assert _shadow_event_summary(row, 'broken')['status'] == 'invalid_cutoff'


def test_ablation_is_in_actual_report_without_private_state(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    report = build_trade_plan_report(reports, now=datetime(2026,10,9,tzinfo=timezone.utc))
    audit = report['evidence_ablation']
    assert audit['status'] == 'insufficient'
    assert audit['matched_completed_pairs'] == 0
    assert audit['accuracy_improvement'] is None
    assert 'state' not in audit
    target = write_trade_plan_report(reports)
    published = json.loads(target.read_text())
    assert 'state' not in published['evidence_ablation']
    assert (tmp_path / '.prediction_engine' / 'shadow_evidence_ablation_state.json').is_file()
    assert not list(reports.glob('*ablation*'))


def test_corrupt_private_state_preserved_fail_closed(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    private = tmp_path / '.prediction_engine'
    private.mkdir()
    state = private / 'shadow_evidence_ablation_state.json'
    state.write_text('broken state kept for diagnosis')
    target = write_trade_plan_report(reports)
    assert state.read_text() == 'broken state kept for diagnosis'
    audit = json.loads(target.read_text())['evidence_ablation']
    assert audit['status'] == 'insufficient'
    assert 'invalid_private_ablation_input' in audit['blocked_reasons']


def test_private_raw_sample_storage_is_outside_actions_cache_and_docker_context():
    gitignore = Path('.gitignore').read_text().splitlines()
    dockerignore = Path('.dockerignore').read_text().splitlines()
    workflow = Path('.github/workflows/stock-briefing.yml').read_text()
    assert '.alpaca_daily_shadow/' in gitignore
    assert '.alpaca_daily_shadow/' in dockerignore
    assert '.prediction_engine/' in dockerignore
    assert '.alpaca_daily_shadow' not in workflow
    assert 'path: .prediction_engine' in workflow
