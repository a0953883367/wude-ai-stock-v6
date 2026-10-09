import json
from datetime import datetime
from zoneinfo import ZoneInfo

from performance import _snapshot_hash
from us_direction_agent import inspect_us_direction
from agent_stock_recovery import inspect, reserve, execute_reserved, save

ZONE = ZoneInfo('Asia/Taipei')


def setup(root, *, calendar=True, source='2026-10-08'):
    save(root / 'all_analysis.json', {'data': [
        {'market': 'US', 'symbol': 'NVDA', 'type': '個股', 'official_session_date': source, 'market_contract_valid': True},
        {'market': 'US', 'symbol': 'SMH', 'type': 'ETF', 'official_session_date': source, 'market_contract_valid': True}]})
    save(root / 'latest.json', {'updated_at': '2026-10-09 16:58:00', 'period': 'noon',
        'data_status': {'us_sip_count': 187, 'us_opra_count': 29}})
    if calendar:
        sessions = ['2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-12']
        save(root / 'official_market_calendar.json', {'version': 1, 'markets': {
            'TW': {'years': {}}, 'US': {'years': {'2026': {'status': 'verified_alpaca', 'sessions': sessions,
            'session_details': {s: {'open': '09:30', 'close': '16:00'} for s in sessions}}}}}})


def receipt(root, source='2026-10-08', *, tampered=False):
    snapshot = {'id': f'US:{source}', 'market': 'US', 'session_date': source,
                'audit_schema_version': 4, 'captured_at': '2026-10-09 12:00:00', 'period': 'noon',
                'predictions': [{'symbol': 'NVDA', 'cohort': 'US_STOCK', 'validation_eligible': True},
                                {'symbol': 'SMH', 'cohort': 'US_ETF', 'validation_eligible': True}]}
    snapshot['integrity_sha256'] = _snapshot_hash(snapshot)
    if tampered:
        snapshot['captured_at'] = '2026-10-09 13:00:00'
    save(root / 'prediction_history.json', {'version': 6, 'snapshots': [snapshot]})


def test_direction_agent_accepts_only_actual_verified_current_snapshot(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 17, tzinfo=ZONE)
    receipt(tmp_path, '2026-10-02')
    assert inspect_us_direction(tmp_path, now)['verified_recovered'] is False
    receipt(tmp_path, tampered=True)
    assert inspect_us_direction(tmp_path, now)['verified_recovered'] is False
    receipt(tmp_path)
    assert inspect_us_direction(tmp_path, now)['verified_recovered'] is True


def test_direction_agent_waits_after_open_and_stops_failed_calendar(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 23, tzinfo=ZONE)
    status = inspect_us_direction(tmp_path, now)
    assert status['status'] == 'waiting_next_close'
    assert status['needs_silent_refresh'] is False
    (tmp_path / 'official_market_calendar.json').unlink()
    save(tmp_path / 'us_direction_progress.json', {'calendar_refresh_attempts': [{'status': 'failed_stop_no_retry'}]})
    assert inspect_us_direction(tmp_path, now)['status'] == 'blocked_manual'
    assert inspect_us_direction(tmp_path, now)['needs_silent_refresh'] is False


class Executor:
    def __init__(self, active=False):
        self.sent = []
        self.active = active
    def runs(self):
        return [{'status': 'in_progress'}] if self.active else []
    def dispatch(self, period, *, data_refresh=False):
        self.sent.append((period, data_refresh))


def test_fresh_report_can_silently_recover_direction_without_sending(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 17, tzinfo=ZONE)
    state = inspect(tmp_path, now, runs=[], web={'ok': True})
    assert state['next_actions'] == [{'incident_key': '2026-10-09:noon', 'period': 'noon', 'kind': 'data_refresh'}]
    assert state['us_direction_verification']['verified_recovered'] is False
    reserve(state, now, 'guard-1')
    save(tmp_path / 'agent_recovery.json', state)
    executor = Executor()
    result = execute_reserved(tmp_path, now, executor, expected_run_id='guard-1')
    assert executor.sent == [('noon', True)]
    assert result['us_direction_verification']['verified_recovered'] is False
    assert not (tmp_path / 'report_delivery_status.json').exists()
    assert not (tmp_path / 'prediction_history.json').exists()


def test_reserved_direction_repair_rechecks_receipt_active_run_and_clock(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 17, tzinfo=ZONE)
    state = inspect(tmp_path, now, runs=[], web={'ok': True})
    reserve(state, now, 'guard-1')
    save(tmp_path / 'agent_recovery.json', state)
    receipt(tmp_path)
    executor = Executor()
    result = execute_reserved(tmp_path, now, executor, expected_run_id='guard-1')
    assert executor.sent == []
    assert result['data_incidents']['2026-10-09:noon']['attempts'][0]['state'] == 'skipped_already_fresh'


def test_direction_budget_is_not_reset_by_fresh_prices(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 17, tzinfo=ZONE)
    save(tmp_path / 'agent_recovery.json', {'data_incidents': {'2026-10-09:noon': {
        'period': 'noon', 'attempts': [{'at': '2026-10-09T13:00:00+08:00'}, {'at': '2026-10-09T14:00:00+08:00'}]}}})
    state = inspect(tmp_path, now, runs=[], web={'ok': True})
    assert state['data_recovery_status'] == 'exhausted'
    assert state['next_actions'] == []
    assert len(state['data_incidents']['2026-10-09:noon']['attempts']) == 2


def test_reserved_direction_refresh_avoids_active_run(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 17, tzinfo=ZONE)
    state = inspect(tmp_path, now, runs=[], web={'ok': True})
    reserve(state, now, 'guard-1')
    save(tmp_path / 'agent_recovery.json', state)
    executor = Executor(active=True)
    result = execute_reserved(tmp_path, now, executor, expected_run_id='guard-1')
    assert executor.sent == []
    assert result['data_incidents']['2026-10-09:noon']['attempts'][0]['state'] == 'skipped_active_run'


def test_accepted_refresh_is_not_recovery_and_later_receipt_is_verified(tmp_path):
    setup(tmp_path)
    now = datetime(2026, 10, 9, 17, tzinfo=ZONE)
    save(tmp_path / 'agent_recovery.json', {'data_incidents': {'2026-10-09:noon': {
        'period': 'noon', 'attempts': [{'at': now.isoformat(), 'state': 'dispatch_accepted'}]}}})
    pending = inspect(tmp_path, now, runs=[], web={'ok': True})
    assert pending['us_direction_verification']['verified_recovered'] is False
    receipt(tmp_path)
    recovered = inspect(tmp_path, now, runs=[], web={'ok': True})
    assert recovered['us_direction_verification']['verified_recovered'] is True
    assert len(recovered['data_incidents']['2026-10-09:noon']['attempts']) == 1


def test_agent_runtime_exposes_direction_receipt_and_budget(tmp_path):
    from agent_runtime import build_runtime_report
    import shutil
    shutil.copytree("agent_workspaces", tmp_path / "agent_workspaces")
    save(tmp_path / 'agent_recovery.json', {'us_direction_verification': {'status': 'waiting_next_close', 'verified_recovered': False},
         'data_incidents': {'2026-10-09:evening': {'status': 'exhausted', 'attempts': [1, 2]}}})
    runtime = build_runtime_report(tmp_path, root=tmp_path)
    assert runtime['stock_maintenance']['us_direction_verification']['verified_recovered'] is False
    assert runtime['stock_maintenance']['data_incidents']['2026-10-09:evening']['status'] == 'exhausted'


def test_agent_rejects_hashed_but_late_forecast(tmp_path):
    setup(tmp_path)
    receipt(tmp_path)
    history = json.loads((tmp_path / 'prediction_history.json').read_text())
    snapshot = history['snapshots'][0]
    snapshot['captured_at'] = '2026-10-09 22:00:00'
    snapshot['integrity_sha256'] = _snapshot_hash(snapshot)
    save(tmp_path / 'prediction_history.json', history)
    result = inspect_us_direction(tmp_path, datetime(2026, 10, 9, 23, tzinfo=ZONE))
    assert result['verified_recovered'] is False
    assert result['status'] == 'blocked_manual'
