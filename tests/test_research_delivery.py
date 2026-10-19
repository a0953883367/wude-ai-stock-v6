from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

import research_delivery as delivery

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reports(tmp_path):
    source = ROOT/'reports'
    analysis = json.loads((source/'all_analysis.json').read_bytes())
    names = ['all_analysis.json'] + [s['path'] for s in analysis['research_integration']['sources']]
    for name in names: shutil.copyfile(source/name, tmp_path/name)
    return tmp_path


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def test_real_complete_pool_small_files_and_exact_projection(reports):
    before = {p.name: p.read_bytes() for p in reports.iterdir()}
    output = delivery.build_delivery(reports)
    assert before == {p.name: p.read_bytes() for p in reports.iterdir()}
    index = json.loads(output[delivery.INDEX])
    source = read(reports/'all_analysis.json')
    assert index['status'] == 'complete'
    assert index['identity_count'] == len(source['data']) == 380
    assert index['horizon_count'] == 3800
    assert {s['path'] for s in index['prediction_files']} == {
        s['path'] for s in source['research_integration']['sources']}
    assert all(len(raw) <= delivery.MAX_BYTES for raw in output.values())
    actual = {}
    for spec in index['judgment_files']:
        raw = output[spec['path']]
        assert len(raw) == spec['bytes'] <= delivery.JUDGMENT_BYTES
        assert hashlib.sha256(raw).hexdigest() == spec['sha256']
        part = json.loads(raw)
        assert part['updated_at'] == index['updated_at']
        for row in part['data']: actual[row['market'], row['symbol']] = row
    for row in source['data']:
        assert actual[row['market'], row['symbol']]['research']['judgment'] == row['research']['judgment']
        assert set(actual[row['market'], row['symbol']]) == {'market', 'symbol', 'name', 'research'}
    specs = {s['file_id']: s for s in index['prediction_files']}
    for entry in index['identities']:
        assert len(entry['prediction_files']) == 10
        assert entry['judgment_file'] in output
        for horizon, file_id in entry['prediction_files'].items():
            name = specs[file_id]['path']
            raw = before[name]; part = json.loads(raw)
            assert hashlib.sha256(raw).hexdigest() == specs[file_id]['sha256']
            assert part['updated_at'] == index['updated_at']
            assert sum(r['market'] == entry['market'] and r['symbol'] == entry['symbol']
                       and r['horizon_code'] == horizon for r in part['predictions']) == 1
    assert delivery.publish_delivery_safely(reports)
    assert (reports/delivery.INDEX).read_bytes() == output[delivery.INDEX]
    assert delivery.publish_delivery_safely(reports)
    assert (reports/delivery.INDEX).read_bytes() == output[delivery.INDEX]
    assert all((reports/name).read_bytes() == raw for name, raw in before.items())


@pytest.mark.parametrize('fault', ['empty', 'missing', 'hash', 'batch', 'duplicate', 'horizon', 'judgment', 'traversal', 'forecast_mismatch'])
def test_failures_replace_old_complete_index_without_modifying_analysis(reports, fault):
    assert delivery.publish_delivery_safely(reports)
    analysis = read(reports/'all_analysis.json')
    manifest = read(reports/'prediction_engine.json')
    name = next(s['path'] for s in analysis['research_integration']['sources'] if s['path'] != 'prediction_engine.json')
    if fault == 'empty': (reports/name).write_bytes(b'')
    if fault == 'missing': (reports/name).unlink()
    if fault == 'hash': (reports/name).write_bytes((reports/name).read_bytes()+b' ')
    if fault == 'batch':
        manifest['updated_at'] = '2000-01-01 00:00:00'; save(reports/'prediction_engine.json', manifest)
    if fault == 'traversal':
        manifest['data_files']['US_STOCK']['NEXT_1D'] = '../private.json'; save(reports/'prediction_engine.json', manifest)
    if fault == 'duplicate': analysis['data'][1] = deepcopy(analysis['data'][0])
    if fault == 'horizon': analysis['data'][0]['research']['horizons'].pop()
    if fault == 'judgment': analysis['data'][0]['research'].pop('judgment')
    if fault == 'forecast_mismatch': analysis['data'][0]['research']['horizons'][0]['expected_return_pct'] = 999
    save(reports/'all_analysis.json', analysis)
    before = (reports/'all_analysis.json').read_bytes()
    assert not delivery.publish_delivery_safely(reports)
    assert read(reports/delivery.INDEX)['status'] == 'unavailable'
    assert (reports/'all_analysis.json').read_bytes() == before


def test_interrupted_publish_and_resume(reports, monkeypatch):
    original = delivery._atomic
    def interrupt(path, raw):
        if path.name == 'research_judgment_02.json': raise OSError('interrupted write')
        original(path, raw)
    monkeypatch.setattr(delivery, '_atomic', interrupt)
    assert not delivery.publish_delivery_safely(reports)
    assert read(reports/delivery.INDEX)['status'] == 'unavailable'
    monkeypatch.setattr(delivery, '_atomic', original)
    assert delivery.publish_delivery_safely(reports)
    assert read(reports/delivery.INDEX)['status'] == 'complete'


def test_private_and_execution_fields_never_projected(reports):
    analysis = read(reports/'all_analysis.json')
    row = analysis['data'][0]
    row['private_holding'] = {'secret': 'PRIVATE_SENTINEL'}
    row['buy_price'] = 123456789
    row['research']['judgment']['private_holding'] = 'PRIVATE_SENTINEL'
    row['research']['judgment']['short']['order_price'] = 123456789
    save(reports/'all_analysis.json', analysis)
    raw = b''.join(delivery.build_delivery(reports).values())
    assert b'PRIVATE_SENTINEL' not in raw and b'123456789' not in raw
    row['research']['judgment']['target_price'] = 123456789
    save(reports/'all_analysis.json', analysis)
    assert not delivery.publish_delivery_safely(reports)


def test_publication_hook_after_analysis_before_rendering():
    code = (ROOT/'briefing.py').read_text()
    assert code.index('all_analysis_tmp.replace(all_analysis_path)') < code.index('publish_delivery_safely(SETTINGS.reports_dir)') < code.index('markdown = finalize_research_markdown')


def test_index_write_failure_does_not_abort_core_report(reports, monkeypatch):
    assert delivery.publish_delivery_safely(reports)
    before = (reports/'all_analysis.json').read_bytes()
    def fail(*args): raise OSError('disk unavailable')
    monkeypatch.setattr(delivery, '_atomic', fail)
    assert not delivery.publish_delivery_safely(reports)
    assert not (reports/delivery.INDEX).exists()
    assert (reports/'all_analysis.json').read_bytes() == before


def test_unexpected_nested_private_object_is_not_serialized(reports):
    analysis = read(reports/'all_analysis.json')
    analysis['data'][0]['research']['judgment']['short']['reason'] = {'private': 'secret'}
    save(reports/'all_analysis.json', analysis)
    assert not delivery.publish_delivery_safely(reports)
