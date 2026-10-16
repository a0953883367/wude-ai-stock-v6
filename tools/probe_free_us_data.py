"""Read-only diagnostic using existing authorized relay; prints no market rows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from us_market_data import fetch_us_sip_snapshots, _relay_request
from tools.validate_free_report import required_symbols, valid_delayed_reference, EXCLUDED_SYMBOLS


def main(*, diagnostics=False) -> int:
    fixture = json.loads((ROOT / 'tests/fixtures/us_free_probe_universe.json').read_text())
    symbols = fixture['symbols']
    required = required_symbols(symbols)
    if required is None:
        raise ValueError('expected unchanged frozen 188-symbol US universe')
    os.environ['ALPACA_DATA_MODE'] = 'free_delayed'
    diagnostic_counts = {}
    if diagnostics:
        rows = _relay_request('sip', {'symbols': symbols, 'data_mode': 'free_delayed',
                             'include_free_diagnostics': True}, 90,
                             free_diagnostics=diagnostic_counts)
    else:
        rows = fetch_us_sip_snapshots(symbols, timeout=90)
    rows = rows if isinstance(rows, dict) else {}
    accepted = {symbol: row for symbol, row in rows.items()
                if symbol in symbols and isinstance(row, dict) and valid_delayed_reference(row)}
    daily = _relay_request('free_daily_status', {'symbols': symbols}, 90)
    daily = daily if isinstance(daily, dict) else {}
    daily_missing = daily.get('missing_symbols')
    valid_daily_missing = (isinstance(daily_missing, list)
        and all(isinstance(symbol, str) for symbol in daily_missing)
        and len(set(daily_missing)) == len(daily_missing)
        and set(daily_missing) <= set(symbols))
    daily_counts = [daily.get('sip_daily_count'), daily.get('cached_yahoo_count')]
    valid_daily_counts = (valid_daily_missing
        and all(type(value) is int and value >= 0 for value in daily_counts)
        and sum(daily_counts) == 188 - len(daily_missing))
    required_daily_missing = sorted(set(daily_missing) & required) if valid_daily_missing else sorted(required)
    required_missing = sorted(required - set(accepted))
    result = {
        'mode': 'free_delayed', 'source': fixture['source'],
        'excluded_symbols': EXCLUDED_SYMBOLS, 'exclusion_reason': 'user_requested',
        'required_count': len(required), 'required_available_count': len(required & set(accepted)),
        'required_missing_symbols': required_missing,
        'required_coverage_status': 'complete' if not required_missing else 'partial',
        'required_daily_missing_symbols': required_daily_missing,
        'daily_missing_symbols_valid': valid_daily_missing,
        'daily_counts_valid': valid_daily_counts,
        'universe_blob_sha': fixture['source_blob_sha'], 'requested_count': 188,
        'historical_available_count': len(accepted),
        'missing_symbols': sorted(set(symbols) - set(accepted)),
        'minute_count': sum(r.get('us_delayed_interval') == '1Min' for r in accepted.values()),
        'completed_daily_count': sum(r.get('us_delayed_interval') == '1Day' for r in accepted.values()),
        'sip_count': sum(r.get('us_delayed_coverage') == 'consolidated' for r in accepted.values()),
        'cached_yahoo_count': sum(r.get('us_delayed_cached') is True for r in accepted.values()),
        'separate_daily_probe': daily,
        'unexpected_live_count': sum(bool(r.get('us_live_data_available')) for r in rows.values() if isinstance(r, dict)),
        'status': 'complete' if len(accepted) == 188 else 'partial' if accepted else 'blocked',
        'basic_account_entitlement_verified': False,
        'paid_subscription_changed': False, 'market_values_exported': False,
    }
    if diagnostics:
        result['private_free_diagnostics'] = diagnostic_counts
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (not required_missing and not result['unexpected_live_count']
                 and valid_daily_missing and valid_daily_counts and not required_daily_missing
                 and daily.get('requested_count') == 188
                 and daily.get('status') == ('partial' if daily_missing else 'complete')
                 and daily.get('market_values_exported') is False) else 1


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnostics', action='store_true')
    args = parser.parse_args()
    raise SystemExit(main(diagnostics=args.diagnostics))
