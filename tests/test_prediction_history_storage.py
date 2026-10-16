import gzip
import json
import subprocess
from pathlib import Path

import pytest

import prediction_history_storage as storage
from prediction_history_storage import HistoryStorageError, read_prediction_history, write_prediction_history
from tools.check_git_blob_sizes import check_staged_blob_sizes


@pytest.fixture
def ledger():
    return {'version': 6, 'snapshots': [{'id': 'TW:2026-10-15', 'session_date': '2026-10-15',
                                      'market': 'TW', 'predictions': [], 'note': '完整歷史'}],
            'metadata': {'unknown_field': [1, None, True]}}


def test_legacy_missing_and_matching_dual(tmp_path, ledger):
    path = tmp_path / 'prediction_history.json'
    assert read_prediction_history(path) == {}
    raw = json.dumps(ledger, ensure_ascii=False, indent=2).encode()
    path.write_bytes(raw)
    assert read_prediction_history(path) == ledger
    path.with_suffix('.json.gz').write_bytes(gzip.compress(raw, mtime=0))
    assert read_prediction_history(path) == ledger


def test_verified_migration_preserves_every_byte_and_field(tmp_path, monkeypatch, ledger):
    path = tmp_path / 'prediction_history.json'
    write_prediction_history(path, ledger)
    raw = path.read_bytes()
    monkeypatch.setattr(storage, 'COMPRESS_AT_BYTES', 1)
    write_prediction_history(path, read_prediction_history(path))
    assert not path.exists()
    compressed = path.with_suffix('.json.gz')
    first = compressed.read_bytes()
    assert gzip.decompress(first) == raw
    assert read_prediction_history(path) == ledger
    write_prediction_history(path, ledger)
    assert compressed.read_bytes() == first
    monkeypatch.setattr(storage, 'COMPRESS_AT_BYTES', 10**9)
    write_prediction_history(path, ledger)
    assert compressed.exists() and not path.exists()


@pytest.mark.parametrize('corruption', [b'bad gzip', gzip.compress(b'{'), gzip.compress(b'[]'), gzip.compress(b'{}')[:-3]])
def test_corruption_never_resets_or_overwrites(tmp_path, ledger, corruption):
    path = tmp_path / 'prediction_history.json'
    compressed = path.with_suffix('.json.gz')
    compressed.write_bytes(corruption)
    with pytest.raises(HistoryStorageError):
        read_prediction_history(path)
    with pytest.raises(HistoryStorageError):
        write_prediction_history(path, ledger)
    assert compressed.read_bytes() == corruption
    assert not path.exists()


def test_conflict_preserves_both(tmp_path, ledger):
    path = tmp_path / 'prediction_history.json'
    path.write_text(json.dumps(ledger))
    compressed = path.with_suffix('.json.gz')
    compressed.write_bytes(gzip.compress(b'{}'))
    before = (path.read_bytes(), compressed.read_bytes())
    with pytest.raises(HistoryStorageError):
        write_prediction_history(path, ledger)
    assert (path.read_bytes(), compressed.read_bytes()) == before


def test_capacity_failure_leaves_history_intact(tmp_path, monkeypatch, ledger):
    path = tmp_path / 'prediction_history.json'
    write_prediction_history(path, ledger)
    before = path.read_bytes()
    monkeypatch.setattr(storage, 'MAX_STORED_BYTES', 1)
    with pytest.raises(HistoryStorageError):
        write_prediction_history(path, ledger)
    assert path.read_bytes() == before
    assert not path.with_suffix('.json.gz').exists()


def test_replace_failure_preserves_original(tmp_path, monkeypatch, ledger):
    path = tmp_path / 'prediction_history.json'
    write_prediction_history(path, ledger)
    before = path.read_bytes()
    monkeypatch.setattr(storage, 'COMPRESS_AT_BYTES', 1)
    def fail(*args):
        raise OSError('simulated disk replacement failure')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        write_prediction_history(path, ledger)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))


def test_all_downstream_consumers_match(tmp_path, monkeypatch, ledger):
    import historical_lab
    import model_learning
    import model_learning_catalog
    import us_direction_agent
    from performance import load_frozen_forecasts
    path = tmp_path / 'prediction_history.json'
    write_prediction_history(path, ledger)
    def results():
        return (model_learning._read(path), model_learning_catalog._read(path),
                us_direction_agent._read(path), historical_lab._forward_archive(tmp_path),
                model_learning_catalog.build_complete_learning_catalog(tmp_path),
                load_frozen_forecasts(tmp_path))
    before = results()
    monkeypatch.setattr(storage, 'COMPRESS_AT_BYTES', 1)
    write_prediction_history(path, ledger)
    assert results() == before


def test_guard_checks_index_not_worktree_and_handles_spaces(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    path = tmp_path / 'history with spaces.json'
    path.write_bytes(b'x' * 100)
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    path.write_bytes(b'small')
    assert check_staged_blob_sizes(tmp_path, 100) == ['history with spaces.json: 100 bytes (limit 100)']
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    assert check_staged_blob_sizes(tmp_path, 100) == []


def test_workflow_guards_before_commit():
    source = Path('.github/workflows/stock-briefing.yml').read_text()
    start = source.index('      - name: Keep reports and recent archive')
    section = source[start:source.index('      - name: Safely advance', start)]
    assert section.index('git add reports') < section.index('python tools/check_git_blob_sizes.py') < section.index('git commit')


def test_malformed_version_cannot_reset(tmp_path):
    path = tmp_path / 'prediction_history.json'
    path.write_text('{"version":"broken","snapshots":[{"id":"keep"}]}')
    before = path.read_bytes()
    from performance import update_performance
    with pytest.raises(HistoryStorageError):
        update_performance(tmp_path, [], [], '2026-10-16 17:00:00', 'evening')
    assert path.read_bytes() == before


def test_real_writer_gzip_update_and_same_session_parity(tmp_path, monkeypatch):
    import runpy
    from performance import update_performance, load_frozen_forecasts, _snapshot_integrity
    make_row = runpy.run_path('tests/test_performance.py')['_row']
    plain = tmp_path / 'plain'
    packed = tmp_path / 'packed'
    first = make_row(100, '2026-08-18')
    second = make_row(101, '2026-08-18')
    stamp = '2026-08-18 20:00:00'
    a = update_performance(plain, [first], [first], stamp, 'evening')
    monkeypatch.setattr(storage, 'COMPRESS_AT_BYTES', 1)
    b = update_performance(packed, [first], [first], stamp, 'evening')
    assert a == b
    assert load_frozen_forecasts(plain) == load_frozen_forecasts(packed)
    first_snapshot = read_prediction_history(packed / 'prediction_history.json')['snapshots'][0]
    update_performance(packed, [second], [second], stamp, 'evening')
    assert read_prediction_history(packed / 'prediction_history.json')['snapshots'][0] == first_snapshot
    assert _snapshot_integrity(first_snapshot) == 'verified'
    assert not (packed / 'prediction_history.json').exists()
