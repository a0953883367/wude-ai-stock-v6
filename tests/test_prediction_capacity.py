"""Capacity changes preserve every logical row; all databases here are temporary."""
from datetime import date, timedelta
import json
import shutil

import pytest

from model_unit_learning import open_prediction_store, record_unit_signals
from prediction_engine.engine import _make_predictions, run_prediction_engine
from prediction_engine.evidence_backup import create_verified_backup, logical_snapshot, restore_verified_backup
from prediction_engine.storage import DEFAULT_MAX_BYTES, PredictionStore, configured_max_bytes
from test_prediction_engine import _row
from test_model_unit_learning import _row as unit_row, _decision


def populated(path):
    store = PredictionStore(path)
    first = date(2025, 1, 1)
    for offset in range(150):
        store.record_session('TW', (first + timedelta(days=offset)).isoformat(), 'fixture')
    row = _row('2330'); row['official_session_date'] = first.isoformat()
    store.record_prices([row], first.isoformat())
    store.insert_predictions(_make_predictions([row], market='TW', date=first.isoformat(),
                                               updated_at='2025-01-01 20:00:00', flow_by_symbol={}))
    record_unit_signals(store, [unit_row(first.isoformat())], [_decision()],
                        period='evening', updated_at='2025-01-01 20:00:00', intraday=False)
    with store.connect() as db:
        db.execute("UPDATE predictions SET status='matured'")
        db.execute("UPDATE unit_learning_predictions SET status='matured'")
    return store


def test_maintenance_retains_old_matured_evidence_prices_and_unit_rows(tmp_path):
    store = populated(tmp_path/'engine.sqlite3')
    before = logical_snapshot(store)
    assert before['unit_learning_predictions'] and before['prices']
    assert any(r['evidence_json'] != '{}' for r in before['predictions'])
    result = store.maintain_capacity(keep_matured_sessions=1)
    assert logical_snapshot(store) == before
    assert result['all_history_retained'] is True
    assert result['matured_evidence_compacted'] == result['old_matured_predictions_pruned'] == result['old_matured_unit_rows_pruned'] == 0
    backup = tmp_path/'private'/'backup.json.gz'
    assert create_verified_backup(store, backup)['verified']
    restored = PredictionStore(tmp_path/'restore.sqlite3')
    assert restore_verified_backup(restored, backup)['integrity_check'] == 'ok'
    assert logical_snapshot(restored) == before
    with pytest.raises(ValueError, match='non-empty'):
        restore_verified_backup(store, backup)


def test_raising_limit_reopens_same_database_without_erasing_existing_rows(tmp_path):
    store = populated(tmp_path/'engine.sqlite3')
    before = logical_snapshot(store)
    size = store.path.stat().st_size
    blocked = PredictionStore(store.path, max_bytes=size)
    new = _row('NEW'); new['official_session_date'] = '2025-06-01'
    rows = _make_predictions([new], market='TW', date='2025-06-01',
                             updated_at='2025-06-01 20:00:00', flow_by_symbol={})
    with pytest.raises(RuntimeError, match='hard size limit'):
        blocked.insert_predictions(rows)
    assert logical_snapshot(blocked) == before
    expanded = PredictionStore(store.path, max_bytes=size * 10)
    assert expanded.insert_predictions(rows) == len(rows)
    after = logical_snapshot(expanded)
    for table, old_rows in before.items():
        assert all(row in after[table] for row in old_rows)
    assert len(after['predictions']) == len(before['predictions']) + len(rows)


@pytest.mark.parametrize('override', [None, str(3 * 1024**3)])
def test_all_entry_points_share_default_and_override(tmp_path, monkeypatch, override):
    monkeypatch.delenv('PREDICTION_ENGINE_DB', raising=False)
    if override is None: monkeypatch.delenv('PREDICTION_ENGINE_MAX_BYTES', raising=False)
    else: monkeypatch.setenv('PREDICTION_ENGINE_MAX_BYTES', override)
    expected = DEFAULT_MAX_BYTES if override is None else int(override)
    assert DEFAULT_MAX_BYTES == 2 * 1024**3
    assert configured_max_bytes() == expected
    assert PredictionStore(tmp_path/'direct.sqlite3').max_bytes == expected
    assert open_prediction_store(tmp_path/'unit'/'reports').max_bytes == expected
    reports = tmp_path/'reports'; reports.mkdir()
    contract = run_prediction_engine(reports, [], period='morning', updated_at='2026-11-05 06:00:00',
                                     intraday=False, db_path=tmp_path/'engine.sqlite3')
    assert contract['database']['configured_max_bytes'] == expected
    health = json.loads((reports/'prediction_engine_health.json').read_text())['database']
    assert health['configured_max_bytes'] == expected
    assert health['filesystem_total_bytes'] == shutil.disk_usage(tmp_path).total
    assert health['filesystem_free_bytes'] > 0
    assert health['retention_policy'] == 'preserve_all_history_and_evidence'


@pytest.mark.parametrize('value', ['0', '-1', 'invalid'])
def test_invalid_capacity_fails_before_database_creation(tmp_path, monkeypatch, value):
    monkeypatch.setenv('PREDICTION_ENGINE_MAX_BYTES', value)
    path = tmp_path/'engine.sqlite3'
    with pytest.raises(ValueError): PredictionStore(path)
    assert not path.exists()


def test_runner_inventory_does_not_create_or_read_private_database(tmp_path):
    from tools.prediction_capacity_status import capacity_status
    before = list(tmp_path.iterdir())
    result = capacity_status(tmp_path)
    assert result['cached_database_bytes'] is None
    assert result['cached_backup_bytes'] is None
    assert result['private_content_read'] is False
    assert list(tmp_path.iterdir()) == before
    folder = tmp_path/'.prediction_engine'; folder.mkdir()
    private = folder/'prediction_engine.sqlite3'; private.write_bytes(b'not-a-database')
    assert capacity_status(tmp_path)['cached_database_bytes'] == len(b'not-a-database')
    assert private.read_bytes() == b'not-a-database'
