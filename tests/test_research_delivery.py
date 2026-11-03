from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

import research_delivery as delivery

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reports(tmp_path):
    # Deliberately synthetic complete pool: live reports can correctly be
    # unavailable, and must never be edited merely to make unit tests pass.
    batch = '2026-11-03 06:00:00'
    manifest = {'updated_at': batch, 'contract_version': 'test', 'data_files': {}}
    rows = []
    for market in ('TW', 'US'):
        symbols = (['8046.TW', '4971.TWO'] + [f'T{i:03}.TW' for i in range(188)]
                   if market == 'TW' else ['LEU'] + [f'U{i:03}' for i in range(189)])
        group = f'{market}_STOCK'
        manifest['data_files'][group] = {}
        for code in delivery.HORIZONS:
            name = f'prediction_engine_data_{group}_{code}.json'
            manifest['data_files'][group][code] = name
            records = [{'market': market, 'symbol': symbol, 'asset_group': group,
                        'horizon_code': code, 'session_date': '2026-11-02',
                        'model_version': 'synthetic-test', 'created_at': batch,
                        'expected_return_pct': 1.0} for symbol in symbols]
            save(tmp_path/name, {'updated_at': batch, 'contract_version': 'test',
                                'group': group, 'horizon_code': code, 'predictions': records})
        for symbol in symbols:
            judgment = {key: {} for key in delivery.JUDGMENT_FIELDS}
            judgment.update(mode='test', evaluated_at=batch, source_session_date='2026-11-02',
                            headline='Synthetic fixture', execution_authorized=False,
                            holdings_assumed=False, probability=None, target_price=None)
            # Exercise real multi-part output and interrupted publication.
            judgment['short']['forecast_basis'] = []
            judgment['quarter']['learning_status'] = {}
            judgment['short']['reason'] = 'Synthetic evidence. ' * 100
            rows.append({'market': market, 'symbol': symbol, 'research': {
                'judgment': judgment,
                'horizons': [{'horizon_code': code, 'status': 'frozen_research',
                              'session_date': '2026-11-02', 'model_version': 'synthetic-test',
                              'created_at': batch, 'expected_return_pct': 1.0}
                             for code in delivery.HORIZONS]}})
    save(tmp_path/'prediction_engine.json', manifest)
    sources = [{'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in tmp_path.iterdir()]
    save(tmp_path/'all_analysis.json', {'updated_at': batch, 'analyzed_count': len(rows), 'data': rows,
         'research_integration': {'status': 'complete_projection', 'sources': sources}})
    return tmp_path


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def test_complete_fixture_pool_small_files_and_exact_projection(reports):
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


@pytest.mark.parametrize('invalid', [None, [], 'invalid', 42])
def test_invalid_learning_status_cannot_abort_core_report(reports, invalid):
    assert delivery.publish_delivery_safely(reports)
    analysis = read(reports/'all_analysis.json')
    analysis['data'][0]['research']['judgment']['quarter']['learning_status'] = invalid
    save(reports/'all_analysis.json', analysis)
    before = (reports/'all_analysis.json').read_bytes()
    with pytest.raises(ValueError, match='judgment_learning_status_invalid'):
        delivery.build_delivery(reports)
    assert delivery.publish_delivery_safely(reports) is False
    assert read(reports/delivery.INDEX)['status'] == 'unavailable'
    assert (reports/'all_analysis.json').read_bytes() == before


@pytest.mark.parametrize('symbol', ['8046.TW', '4971.TWO'])
def test_missing_analysis_history_diagnoses_extra_frozen_predictions(reports, caplog, symbol):
    assert delivery.publish_delivery_safely(reports)
    analysis = read(reports/'all_analysis.json')
    analysis['data'] = [r for r in analysis['data'] if r['symbol'] != symbol]
    analysis['analyzed_count'] = len(analysis['data'])
    assert analysis['analyzed_count'] == 379
    save(reports/'all_analysis.json', analysis)
    before = {p.name: p.read_bytes() for p in reports.iterdir() if p.name != delivery.INDEX}
    with pytest.raises(ValueError, match='incomplete_or_extra_prediction_census'):
        delivery.build_delivery(reports)
    assert not delivery.publish_delivery_safely(reports)
    assert 'expected=3790 actual=3800 missing=0 extra=10' in caplog.text
    assert f"extra_symbols=['TW:{symbol}']" in caplog.text
    assert 'reason=incomplete_or_extra_prediction_census' in caplog.text
    index = read(reports/delivery.INDEX)
    assert index['status'] == 'unavailable'
    assert index['reason'] == 'delivery_integrity_check_failed'
    assert index['identities'] == index['prediction_files'] == index['judgment_files'] == []
    assert all((reports/name).read_bytes() == raw for name, raw in before.items())


@pytest.mark.parametrize('error', [OSError, ValueError, KeyError, TypeError])
def test_external_exception_details_are_not_logged(reports, monkeypatch, caplog, error):
    assert delivery.publish_delivery_safely(reports)
    def fail(*args):
        raise error('PRIVATE_SENTINEL /private/path token=example')
    monkeypatch.setattr(delivery, 'build_delivery', fail)
    assert not delivery.publish_delivery_safely(reports)
    assert f'reason={error.__name__}' in caplog.text
    assert 'PRIVATE_SENTINEL' not in caplog.text
    assert '/private/path' not in caplog.text
    assert read(reports/delivery.INDEX)['status'] == 'unavailable'


def test_census_log_samples_are_bounded_and_reject_multiline_values():
    keys = {('TW', f'{i:04}.TW', 'NEXT_1D') for i in range(20)}
    keys.add(('TW', 'BAD\nPRIVATE_SENTINEL', 'NEXT_1D'))
    samples = delivery._census_symbols(keys)
    assert len(samples) == 10
    assert 'PRIVATE_SENTINEL' not in str(samples)
    assert samples == sorted(samples)


def test_missing_frozen_leu_horizons_still_blocks_complete_analysis(reports, caplog):
    assert delivery.publish_delivery_safely(reports)
    analysis = read(reports/'all_analysis.json')
    manifest = read(reports/'prediction_engine.json')
    for name in manifest['data_files']['US_STOCK'].values():
        chunk = read(reports/name)
        chunk['predictions'] = [r for r in chunk['predictions'] if r['symbol'] != 'LEU']
        save(reports/name, chunk)
    for source in analysis['research_integration']['sources']:
        source['sha256'] = hashlib.sha256((reports/source['path']).read_bytes()).hexdigest()
    save(reports/'all_analysis.json', analysis)
    before = {p.name: p.read_bytes() for p in reports.iterdir() if p.name != delivery.INDEX}
    assert not delivery.publish_delivery_safely(reports)
    assert 'expected=3800 actual=3790 missing=10 extra=0' in caplog.text
    assert "missing_symbols=['US:LEU']" in caplog.text
    assert read(reports/delivery.INDEX)['status'] == 'unavailable'
    assert all((reports/name).read_bytes() == raw for name, raw in before.items())
