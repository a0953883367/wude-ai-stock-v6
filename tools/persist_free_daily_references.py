"""Persist fresh, fully validated completed references only; never formal prices."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.validate_free_report import required_symbols
from us_cached_daily_fallback import (
    SIP_DAILY_SOURCE, _aware_stamp, _sip_close_reference, fetch_cached_us_daily_references,
)

TARGET_NAME = 'us_completed_daily_reference_cache.json'
FIELDS = (
    'us_delayed_data_available', 'us_delayed_source', 'us_delayed_price',
    'us_delayed_asof', 'us_delayed_fetched_at', 'us_delayed_session_date',
    'us_delayed_session_close', 'us_delayed_calendar_verified',
    'us_delayed_calendar_source', 'us_delayed_session_completed',
    'us_delayed_interval', 'us_delayed_coverage', 'us_delayed_minimum_seconds',
    'us_live_data_available',
)


def build_payload(symbols, rows, diagnostics, started, published):
    """Pure validation: no source fetch or persistence; no synthetic price bars."""
    required = required_symbols(symbols)
    if required is None or not isinstance(rows, dict) or not isinstance(diagnostics, dict):
        raise ValueError('invalid_input')
    if (not isinstance(started, datetime) or not isinstance(published, datetime)
            or started.tzinfo is None or published.tzinfo is None or started > published
            or (published - started).total_seconds() > 600):
        raise ValueError('invalid_run_window')
    if set(rows) - set(symbols):
        raise ValueError('unexpected_symbol')
    accepted = {}
    publication = published.isoformat()
    for symbol, row in rows.items():
        if not isinstance(row, dict):
            raise ValueError('invalid_reference')
        # Never let a cache recovery masquerade as a fresh provider observation.
        if row.get('us_delayed_source') != SIP_DAILY_SOURCE or row.get('us_delayed_cached', False) is not False:
            if symbol in required:
                raise ValueError('fresh_required_reference_missing')
            continue
        if row.get('us_option_data_available') is True:
            raise ValueError('unexpected_option_data')
        fetched = _aware_stamp(row.get('us_delayed_fetched_at'))
        # No clock-skew exemption: source fetch must belong to this run.
        if fetched is None or not started <= fetched <= published:
            raise ValueError('source_outside_run')
        clean = {key: row[key] for key in FIELDS if key in row}
        clean.update(symbol=symbol, market='US')
        if row.get('symbol', symbol) != symbol or row.get('market', 'US') != 'US':
            raise ValueError('identity_mismatch')
        if _sip_close_reference(clean, published, published, publication) is None:
            raise ValueError('invalid_completed_reference')
        accepted[symbol] = clean
    if not required <= set(accepted):
        raise ValueError('required_coverage_incomplete')
    sessions = {(r['us_delayed_session_date'], _aware_stamp(r['us_delayed_session_close']))
                for r in accepted.values()}
    if len(sessions) != 1:
        raise ValueError('incoherent_sessions')
    source = diagnostics.get('source_daily')
    if not isinstance(source, dict):
        raise ValueError('source_receipt_missing')
    count = source.get('returned_count')
    expected_status = 'complete' if len(accepted) == 188 else 'partial'
    requests_made = source.get('request_count')
    if (type(requests_made) is not int or requests_made < 1
            or type(count) is not int or count != len(accepted) or type(source.get('requested_count')) is not int or source.get('requested_count') != 188
            or source.get('status') != expected_status
            or source.get('reason') not in (None, 'missing_latest_session')
            or (expected_status == 'partial' and source.get('reason') != 'missing_latest_session')
            or (expected_status == 'complete' and source.get('reason') is not None)):
        raise ValueError('source_receipt_inconsistent')
    data = [accepted.get(symbol, {'symbol': symbol, 'market': 'US',
            'us_delayed_data_available': False, 'us_live_data_available': False,
            'us_delayed_unavailable_reason': 'optional_source_unavailable'}) for symbol in symbols]
    return {
        'schema_version': 1, 'scope': 'completed_daily_reference_only',
        'us_daily_reference_published_at': publication,
        'requested_count': 188, 'required_count': 187,
        'required_available_count': 187, 'fresh_available_count': len(accepted),
        'optional_available_count': int('HNHPF' in accepted),
        'session_date': next(iter(sessions))[0], 'data': data,
    }


def persist_payload(payload, target):
    """Atomic replacement; failures before replace leave the last good file intact."""
    target = Path(target)
    if target.name != TARGET_NAME or target.is_symlink() or target.parent.is_symlink():
        raise ValueError('invalid_target')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.us_daily_reference_', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


SAFE_REASONS = {
    'invalid_input', 'invalid_run_window', 'unexpected_symbol', 'invalid_reference',
    'fresh_required_reference_missing', 'unexpected_option_data', 'source_outside_run',
    'identity_mismatch', 'invalid_completed_reference', 'required_coverage_incomplete',
    'incoherent_sessions', 'source_receipt_missing', 'source_receipt_inconsistent',
    'invalid_target', 'invalid_universe', 'job_free_mode_required', 'oversized_cache',
    'invalid_cache', 'invalid_cache_universe', 'required_cache_incomplete',
    'fallback_not_durable_sip', 'fallback_changed_origin', 'fallback_changed_publication',
    'network_attempted',
}


class SeedFailure(ValueError):
    def __init__(self, reason, phase, diagnostics):
        super().__init__(reason)
        self.reason, self.phase, self.diagnostics = reason, phase, diagnostics


def seed(symbols, target, *, fetcher, clock=lambda: datetime.now(timezone.utc)):
    from free_report_diagnostics import sanitize_diagnostics
    started = clock()
    diagnostics = {}
    phase = 'fetch'
    try:
        # Do not freeze client validation at request start: the backend's fresh
        # fetch legitimately occurs later. Keep start only as the freshness bound.
        rows = fetcher(symbols, timeout=90, free_purpose='completed_daily_report',
                       free_diagnostics=diagnostics)
        phase = 'validate'
        payload = build_payload(symbols, rows, diagnostics, started, clock())
        phase = 'persist'
        persist_payload(payload, target)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in SAFE_REASONS else {
            'fetch': 'source_fetch_failed', 'validate': 'validation_failed', 'persist': 'cache_write_failed'}[phase]
        raise SeedFailure(reason, phase, sanitize_diagnostics(diagnostics)) from None
    return {'status': 'complete', 'requested_count': 188, 'required_count': 187,
            'required_available_count': 187, 'fresh_available_count': payload['fresh_available_count'],
            'optional_available_count': payload['optional_available_count'],
            'market_values_logged': False, 'raw_bars_exported': False, 'cache_written': True,
            'diagnostics': sanitize_diagnostics(diagnostics)}


def verify_cache_only(symbols, target, *, observed_at=None):
    """Prove durable contents and the real outage fallback without network/writes."""
    from types import SimpleNamespace
    from unittest.mock import patch
    import config
    import requests
    import us_market_data
    from us_cached_daily_fallback import MAX_BYTES, SIP_CACHE_SOURCE
    target = Path(target)
    if target.name != TARGET_NAME or target.is_symlink() or target.parent.is_symlink():
        raise ValueError('invalid_target')
    required = required_symbols(symbols)
    now = observed_at or datetime.now(timezone.utc)
    with Path(target).open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('oversized_cache')
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError('invalid_cache')
    published_text = payload.get('us_daily_reference_published_at')
    published = _aware_stamp(published_text)
    rows = payload.get('data')
    if required is None or published is None or published > now or not isinstance(rows, list):
        raise ValueError('invalid_cache')
    identities = [row.get('symbol') for row in rows if isinstance(row, dict)]
    if any(not isinstance(symbol, str) for symbol in identities) or len(identities) != 188 or len(set(identities)) != 188 or set(identities) != set(symbols):
        raise ValueError('invalid_cache_universe')
    original = {row['symbol']: row for row in rows}
    valid = {row['symbol'] for row in rows if _sip_close_reference(row, published, now, published_text) is not None}
    if not required <= valid:
        raise ValueError('required_cache_incomplete')
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('network_forbidden')
    with patch.dict(os.environ, {'ALPACA_DATA_MODE': 'free_delayed'}), \
         patch.object(config, 'SETTINGS', SimpleNamespace(reports_dir=Path(target).parent)), \
         patch.object(us_market_data, '_credentials', return_value=None), \
         patch.object(us_market_data, '_relay_request', return_value={}), \
         patch.object(requests, 'get', side_effect=forbidden), \
         patch.object(requests, 'post', side_effect=forbidden), \
         patch.object(requests.sessions.Session, 'request', side_effect=forbidden):
        recovered = us_market_data.fetch_us_sip_snapshots(
            symbols, observed_at=now, free_purpose='completed_daily_report', free_diagnostics={})
    if attempts:
        raise ValueError('network_attempted')
    for symbol in required:
        row = recovered.get(symbol, {})
        if row.get('us_delayed_source') != SIP_CACHE_SOURCE or row.get('us_delayed_cached') is not True:
            raise ValueError('fallback_not_durable_sip')
        for key in ('us_delayed_asof', 'us_delayed_fetched_at', 'us_delayed_session_date', 'us_delayed_session_close'):
            if row.get(key) != original[symbol].get(key):
                raise ValueError('fallback_changed_origin')
        expected_publication = original[symbol].get('us_delayed_origin_published_at', published_text)
        if row.get('us_delayed_origin_published_at') != expected_publication:
            raise ValueError('fallback_changed_publication')
    return {'status': 'complete', 'required_count': 187, 'required_available_count': 187,
            'provider_outage_recovered_count': 187, 'network_requests': 0,
            'cache_written': False, 'market_values_logged': False, 'raw_bars_exported': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', '--verify-cache-only', dest='verify', action='store_true', help='Read only; make no provider requests')
    args = parser.parse_args(argv)
    try:
        symbols = json.loads((ROOT / 'tests/fixtures/us_free_probe_universe.json').read_text())['symbols']
        required = required_symbols(symbols)
        if required is None:
            raise ValueError('invalid_universe')
        target = ROOT / 'reports' / TARGET_NAME
        if args.verify:
            result = verify_cache_only(symbols, target)
        else:
            if os.getenv('ALPACA_DATA_MODE', '').strip().lower() != 'free_delayed':
                raise ValueError('job_free_mode_required')
            from us_market_data import fetch_us_sip_snapshots
            result = seed(symbols, target, fetcher=fetch_us_sip_snapshots)
        print(json.dumps(result, separators=(',', ':')))
        return 0 if result['status'] == 'complete' else 1
    except Exception as exc:
        # Only fixed allowlisted reasons; never arbitrary exception/provider text.
        reason = exc.reason if isinstance(exc, SeedFailure) else (
            str(exc) if isinstance(exc, ValueError) and str(exc) in SAFE_REASONS else 'cache_operation_failed')
        print(json.dumps({'status': 'blocked', 'phase': exc.phase if isinstance(exc, SeedFailure) else 'verify' if args.verify else 'setup',
                          'reason': reason, 'cache_written': False, 'required_count': 187,
                          'diagnostics': exc.diagnostics if isinstance(exc, SeedFailure) else {},
                          'market_values_logged': False, 'raw_bars_exported': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
