import copy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from tools.persist_free_daily_references import build_payload, seed, verify_cache_only, TARGET_NAME
from us_cached_daily_fallback import fetch_cached_us_daily_references

SYMBOLS = json.loads((Path(__file__).parent / 'fixtures/us_free_probe_universe.json').read_text())['symbols']
START = datetime.fromisoformat('2026-10-16T16:00:00+00:00')
PUBLISHED = START + timedelta(seconds=30)


def inputs():
    rows = {s: dict(us_delayed_data_available=True, us_delayed_source='Alpaca SIP completed daily bars',
        us_delayed_price=100, us_delayed_asof='2026-10-15T16:00:00-04:00',
        us_delayed_fetched_at=START.isoformat(), us_delayed_session_date='2026-10-15',
        us_delayed_session_close='2026-10-15T16:00:00-04:00', us_delayed_calendar_verified=True,
        us_delayed_calendar_source='Alpaca Market Calendar', us_delayed_session_completed=True,
        us_delayed_interval='1Day', us_delayed_coverage='consolidated', us_delayed_minimum_seconds=960,
        us_live_data_available=False) for s in SYMBOLS if s != 'HNHPF'}
    return rows, {'source_daily': {'status': 'partial', 'reason': 'missing_latest_session',
        'requested_count': 188, 'returned_count': 187, 'request_count': 1}}


def fake_fetch(rows, diagnostics):
    def fetch(symbols, **kwargs):
        assert symbols == SYMBOLS
        assert kwargs['free_purpose'] == 'completed_daily_report'
        kwargs['free_diagnostics'].update(diagnostics)
        return rows
    return fetch


def test_seed187_and_real_outage_recovery_without_network(tmp_path):
    rows, diagnostics = inputs()
    target = tmp_path / TARGET_NAME
    moments = iter([START, PUBLISHED])
    result = seed(SYMBOLS, target, fetcher=fake_fetch(rows, diagnostics), clock=lambda: next(moments))
    assert result['required_available_count'] == 187
    payload = json.loads(target.read_text())
    assert len(payload['data']) == 188
    assert payload['optional_available_count'] == 0
    assert all('us_delayed_bars' not in row and 'price' not in row for row in payload['data'])
    before = target.read_bytes()
    proof = verify_cache_only(SYMBOLS, target, observed_at=PUBLISHED)
    assert proof['provider_outage_recovered_count'] == 187
    assert proof['network_requests'] == 0
    assert target.read_bytes() == before
    # Dedicated source participates in normal bounded cache lookup.
    cached = fetch_cached_us_daily_references(SYMBOLS, PUBLISHED, tmp_path / 'all_analysis.json')
    assert len(cached) == 187
    assert all(r['us_delayed_cache_source_kind'] == 'durable_completed_daily' for r in cached.values())
    assert not fetch_cached_us_daily_references(SYMBOLS, PUBLISHED + timedelta(days=7), tmp_path / 'all_analysis.json')
    with pytest.raises(ValueError, match='required_cache_incomplete'):
        verify_cache_only(SYMBOLS, target, observed_at=PUBLISHED + timedelta(days=7))


@pytest.mark.parametrize('change', ['partial', 'cached', 'stale', 'future', 'wrong_delay', 'different_session',
    'no_calendar', 'blocked_receipt', 'false_count', 'missing_receipt', 'bad_reason', 'extra_symbol'])
def test_failure_never_overwrites_good_cache(tmp_path, change):
    rows, diagnostics = inputs()
    key = next(iter(rows))
    if change == 'partial': rows.pop(key)
    elif change == 'cached': rows[key]['us_delayed_source'] = 'Alpaca SIP completed daily report cache'
    elif change == 'stale': rows[key]['us_delayed_fetched_at'] = (START - timedelta(seconds=1)).isoformat()
    elif change == 'future': rows[key]['us_delayed_fetched_at'] = (PUBLISHED + timedelta(seconds=1)).isoformat()
    elif change == 'wrong_delay': rows[key]['us_delayed_minimum_seconds'] = 0
    elif change == 'different_session':
        rows[key]['us_delayed_session_date'] = '2026-10-14'
        rows[key]['us_delayed_session_close'] = rows[key]['us_delayed_asof'] = '2026-10-14T16:00:00-04:00'
    elif change == 'no_calendar': rows[key]['us_delayed_calendar_verified'] = False
    elif change == 'blocked_receipt': diagnostics['source_daily']['status'] = 'blocked'
    elif change == 'false_count': diagnostics['source_daily']['returned_count'] = 188
    elif change == 'missing_receipt': diagnostics.clear()
    elif change == 'bad_reason': diagnostics['source_daily']['reason'] = 'provider_http_failure'
    elif change == 'extra_symbol': rows['UNREQUESTED'] = dict(rows[key])
    target = tmp_path / TARGET_NAME
    target.write_text('last-good-cache')
    moments = iter([START, PUBLISHED])
    with pytest.raises(ValueError):
        seed(SYMBOLS, target, fetcher=fake_fetch(rows, diagnostics), clock=lambda: next(moments))
    assert target.read_text() == 'last-good-cache'
    assert sorted(p.name for p in tmp_path.iterdir()) == [TARGET_NAME]


def test_optional_fresh_and_minimal_allowlist():
    rows, diagnostics = inputs()
    rows['HNHPF'] = dict(next(iter(rows.values())))
    rows['HNHPF']['us_delayed_bars'] = [{'secret_raw': 123}]
    rows['HNHPF']['price'] = 900
    diagnostics['source_daily'].update(status='complete', returned_count=188)
    diagnostics['source_daily'].pop('reason')
    payload = build_payload(SYMBOLS, rows, diagnostics, START, PUBLISHED)
    assert payload['fresh_available_count'] == 188
    assert all('us_delayed_bars' not in r and 'price' not in r for r in payload['data'])


def test_bad_universe_and_unbounded_window():
    rows, diagnostics = inputs()
    for symbols, end in [(SYMBOLS[:-1], PUBLISHED), (SYMBOLS, START + timedelta(minutes=11))]:
        with pytest.raises(ValueError):
            build_payload(symbols, rows, diagnostics, START, end)


def test_verifier_refuses_duplicate_or_missing_publication(tmp_path):
    rows, diagnostics = inputs()
    original = build_payload(SYMBOLS, rows, diagnostics, START, PUBLISHED)
    target = tmp_path / TARGET_NAME
    for kind in ('duplicate', 'publication'):
        payload = copy.deepcopy(original)
        if kind == 'duplicate': payload['data'][-1] = payload['data'][0]
        else: payload.pop('us_daily_reference_published_at')
        target.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            verify_cache_only(SYMBOLS, target, observed_at=PUBLISHED)


def test_actual_relay_client_accepts_fetch_after_job_start(tmp_path, monkeypatch):
    import us_market_data
    rows, diagnostics = inputs()
    for row in rows.values():
        row['us_delayed_fetched_at'] = (START + timedelta(seconds=1)).isoformat()
    def relay(kind, payload, timeout, **kwargs):
        kwargs['free_diagnostics'].update(diagnostics)
        return rows
    monkeypatch.setenv('ALPACA_DATA_MODE', 'free_delayed')
    monkeypatch.setattr(us_market_data, '_credentials', lambda: None)
    monkeypatch.setattr(us_market_data, '_relay_request', relay)
    moments = iter([START, START + timedelta(seconds=2)])
    result = seed(SYMBOLS, tmp_path / TARGET_NAME, fetcher=us_market_data.fetch_us_sip_snapshots,
                  clock=lambda: next(moments))
    assert result['required_available_count'] == 187


def test_failure_receipt_preserves_safe_diagnostic_reason(tmp_path):
    from tools.persist_free_daily_references import SeedFailure
    rows, diagnostics = inputs()
    diagnostics['source_daily']['request_count'] = 0
    moments = iter([START, PUBLISHED])
    with pytest.raises(SeedFailure) as caught:
        seed(SYMBOLS, tmp_path / TARGET_NAME, fetcher=fake_fetch(rows, diagnostics), clock=lambda: next(moments))
    assert caught.value.reason == 'source_receipt_inconsistent'
    assert caught.value.phase == 'validate'
    assert caught.value.diagnostics['source_daily']['request_count'] == 0


@pytest.mark.parametrize('value', [True, 188.0, '188', None])
def test_source_requested_count_requires_exact_int(value):
    rows, diagnostics = inputs()
    diagnostics['source_daily']['requested_count'] = value
    with pytest.raises(ValueError, match='source_receipt_inconsistent'):
        build_payload(SYMBOLS, rows, diagnostics, START, PUBLISHED)


def test_verifier_guards_target_and_malformed_payload(tmp_path):
    target = tmp_path / TARGET_NAME
    other = tmp_path / 'other.json'
    other.write_text('{}')
    with pytest.raises(ValueError, match='invalid_target'):
        verify_cache_only(SYMBOLS, other, observed_at=PUBLISHED)
    target.symlink_to(other)
    with pytest.raises(ValueError, match='invalid_target'):
        verify_cache_only(SYMBOLS, target, observed_at=PUBLISHED)
    target.unlink()
    for payload in ([], {'us_daily_reference_published_at': PUBLISHED.isoformat(), 'data': [{'symbol': []}]}):
        target.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match='invalid_cache'):
            verify_cache_only(SYMBOLS, target, observed_at=PUBLISHED)


@pytest.mark.parametrize('flag', [True, 1, 0, 'true', 'false', None])
def test_malformed_cached_flag_cannot_seed_fresh(flag):
    rows, diagnostics = inputs()
    rows[next(iter(rows))]['us_delayed_cached'] = flag
    with pytest.raises(ValueError, match='fresh_required_reference_missing'):
        build_payload(SYMBOLS, rows, diagnostics, START, PUBLISHED)
