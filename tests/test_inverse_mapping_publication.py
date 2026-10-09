import json
import subprocess
from pathlib import Path


def test_report_rebase_preserves_one_complete_mapping_snapshot(tmp_path: Path):
    _rebase_case(tmp_path / "unsafe", use_atomic=False)
    _rebase_case(tmp_path / "safe", use_atomic=True)


def _rebase_case(tmp_path: Path, use_atomic: bool):
    tmp_path.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.STDOUT, text=True).strip()
    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Test')
    (tmp_path / 'reports').mkdir()
    attributes = Path('.gitattributes').read_text()
    if use_atomic:
        (tmp_path / '.gitattributes').write_text(attributes)
    p = tmp_path / 'reports/inverse_etf_database.json'
    def save(db, message):
        p.write_text(json.dumps(db, indent=2) + '\n')
        git('add', '.'); git('commit', '-m', message)
    rows = [{'symbol': f'BASE{i:02d}'} for i in range(30)]
    base = {'universe_count': 30, 'mappings': rows, 'summary': {'TW': 0, 'US': 30}}
    save(base, 'base')
    git('checkout', '-b', 'report')
    fresh = {'universe_count': 31, 'mappings': rows + [{'symbol': 'LOCAL'}], 'summary': {'TW': 0, 'US': 31}}
    save(fresh, 'fresh report')
    git('checkout', 'main')
    other = {'universe_count': 31, 'mappings': [{'symbol': 'REMOTE'}] + rows, 'summary': {'TW': 0, 'US': 31}}
    save(other, 'other report')
    git('checkout', 'report')
    git('rebase', '-X', 'theirs', 'main')
    result = json.loads(p.read_text())
    if use_atomic:
        assert result == fresh
        assert result['universe_count'] == len(result['mappings']) == sum(result['summary'].values())
    else:
        assert result['universe_count'] == 31
        assert len(result['mappings']) == 32  # Reproduce the real mixed-generation failure.
