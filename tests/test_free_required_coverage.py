import copy
import json
from pathlib import Path

import pytest
from tools import probe_free_us_data as probe
from tools.validate_free_report import summarize, required_symbols

SYMBOLS = json.loads((Path(__file__).parent / 'fixtures/us_free_probe_universe.json').read_text())['symbols']
REQUIRED = [s for s in SYMBOLS if s != 'HNHPF']


def row(symbol):
    return {'market': 'US', 'symbol': symbol, 'price': 11,
        'us_delayed_data_available': True, 'us_live_data_available': False,
        'us_delayed_price': 11, 'us_delayed_minimum_seconds': 960,
        'us_delayed_source': 'Alpaca SIP historical (>=16min delayed)',
        'us_delayed_coverage': 'consolidated', 'us_delayed_interval': '1Min',
        'us_delayed_asof': '2026-10-16T15:13:00Z',
        'us_delayed_fetched_at': '2026-10-16T15:30:00Z'}


def report():
    return {'data': [row(s) if s != 'HNHPF' else {
        'market': 'US', 'symbol': 'HNHPF', 'price': None,
        'us_delayed_data_available': False, 'us_live_data_available': False} for s in SYMBOLS]}


def test_report_hnhpf_unavailable_allowed_but_all_188_identities_retained():
    result = summarize(report(), SYMBOLS)
    assert result['passed'] is True
    assert result['requested_count'] == result['reported_count'] == 188
    assert result['required_count'] == result['required_valid_formal_price_count'] == 187
    assert result['required_validated_delayed_reference_count'] == 187
    assert result['required_missing_symbols'] == []
    assert result['excluded_symbols'] == ['HNHPF'] and result['exclusion_reason'] == 'user_requested'
    assert result['validated_delayed_reference_count'] == 187


def test_report_missing_hnhpf_identity_still_fails():
    value = report()
    value['data'] = [r for r in value['data'] if r['symbol'] != 'HNHPF']
    assert not summarize(value, SYMBOLS)['passed']


@pytest.mark.parametrize('field,value', [('price', None), ('us_delayed_data_available', False),
                                        ('us_delayed_source', 'wrong')])
def test_other_symbol_unavailable_or_mislabelled_fails(field, value):
    value_report = report()
    target = next(r for r in value_report['data'] if r['symbol'] == REQUIRED[0])
    target[field] = value
    result = summarize(value_report, SYMBOLS)
    assert not result['passed'] and result['required_missing_symbols'] == [REQUIRED[0]]


@pytest.mark.parametrize('field,value', [('us_live_data_available', True),
    ('us_option_data_available', True), ('us_delayed_bars', [])])
def test_excluded_symbol_still_obeys_no_live_opra_raw_bars(field, value):
    value_report = report()
    next(r for r in value_report['data'] if r['symbol'] == 'HNHPF')[field] = value
    assert not summarize(value_report, SYMBOLS)['passed']


def test_wrong_or_duplicate_frozen_universe_fails():
    for wrong in [REQUIRED, SYMBOLS[:-1] + ['OTHER'], SYMBOLS[:-1] + [SYMBOLS[0]], ['HNHPF'], None, {'symbols': SYMBOLS}]:
        assert required_symbols(wrong) is None
        assert not summarize(report(), wrong)['passed']
    value = report()
    value['data'][-1] = copy.deepcopy(value['data'][0])
    assert not summarize(value, SYMBOLS)['passed']


def probe_case(monkeypatch, capsys, *, missing=('HNHPF',), available=None):
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    calls = []
    monkeypatch.setattr(probe, 'fetch_us_sip_snapshots',
        lambda symbols, **kw: calls.append(list(symbols)) or {s: row(s) for s in (REQUIRED if available is None else available)})
    daily = {'requested_count': 188, 'status': 'partial' if missing else 'complete',
             'missing_symbols': missing, 'market_values_exported': False,
             'sip_daily_count': 188 - len(missing) if isinstance(missing, list) else 0,
             'cached_yahoo_count': 0}
    monkeypatch.setattr(probe, '_relay_request', lambda kind, payload, timeout: calls.append(payload['symbols']) or daily)
    code = probe.main()
    result = json.loads(capsys.readouterr().out)
    assert calls == [SYMBOLS, SYMBOLS]
    return code, result


def test_probe_requests_188_and_passes_required187(monkeypatch, capsys):
    code, result = probe_case(monkeypatch, capsys, missing=['HNHPF'])
    assert code == 0
    assert result['required_count'] == result['required_available_count'] == 187
    assert result['requested_count'] == 188 and result['historical_available_count'] == 187
    assert result['missing_symbols'] == ['HNHPF'] and result['status'] == 'partial'
    assert result['required_missing_symbols'] == result['required_daily_missing_symbols'] == []
    assert result['excluded_symbols'] == ['HNHPF'] and result['exclusion_reason'] == 'user_requested'


def test_probe_other_missing_in_either_source_fails(monkeypatch, capsys):
    assert probe_case(monkeypatch, capsys, missing=['HNHPF', REQUIRED[0]])[0] == 1
    assert probe_case(monkeypatch, capsys, missing=['HNHPF'], available=REQUIRED[1:])[0] == 1


@pytest.mark.parametrize('malicious', [None, 'HNHPF', {'HNHPF': True}, [None], [{}], [['HNHPF']],
                                       ['HNHPF', 'HNHPF'], ['NOT_IN_UNIVERSE'], True, 0])
def test_probe_invalid_daily_missing_types_fail_closed(monkeypatch, capsys, malicious):
    code, result = probe_case(monkeypatch, capsys, missing=malicious)
    assert code == 1 and result['daily_missing_symbols_valid'] is False
    assert len(result['required_daily_missing_symbols']) == 187


def test_probe_wrong_188_universe_fails_before_requests(tmp_path, monkeypatch):
    folder = tmp_path / 'tests/fixtures'
    folder.mkdir(parents=True)
    (folder / 'us_free_probe_universe.json').write_text(json.dumps({'symbols': SYMBOLS[:-1] + ['OTHER']}))
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setattr(probe, 'fetch_us_sip_snapshots', lambda *a, **kw: pytest.fail('No provider requests'))
    with pytest.raises(ValueError, match='unchanged frozen'):
        probe.main()


@pytest.mark.parametrize('sip,cached', [(0, 0), (True, 186), (187, False),
    (-1, 188), (None, 187), ('187', 0), (188, 0), (187, 1)])
def test_probe_inconsistent_daily_counts_fail_closed(monkeypatch, capsys, sip, cached):
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(probe, 'fetch_us_sip_snapshots', lambda *a, **kw: {s: row(s) for s in REQUIRED})
    monkeypatch.setattr(probe, '_relay_request', lambda *a, **kw: {
        'requested_count': 188, 'status': 'partial', 'missing_symbols': ['HNHPF'],
        'market_values_exported': False, 'sip_daily_count': sip, 'cached_yahoo_count': cached})
    assert probe.main() == 1
    assert json.loads(capsys.readouterr().out)['daily_counts_valid'] is False


def test_empty_daily_missing_with_zero_counts_cannot_pass(monkeypatch, capsys):
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(probe, 'fetch_us_sip_snapshots', lambda *a, **kw: {s: row(s) for s in REQUIRED})
    monkeypatch.setattr(probe, '_relay_request', lambda *a, **kw: {
        'requested_count': 188, 'status': 'complete', 'missing_symbols': [],
        'market_values_exported': False, 'sip_daily_count': 0, 'cached_yahoo_count': 0})
    assert probe.main() == 1
    assert json.loads(capsys.readouterr().out)['daily_counts_valid'] is False
