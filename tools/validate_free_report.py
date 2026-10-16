"""Check locally generated free-mode report without publishing prices or files."""
from __future__ import annotations
import json
import math
from hashlib import sha256
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_SYMBOLS = ['HNHPF']
FROZEN_UNIVERSE_SHA256 = '2e3f88449fd6f90487f42a9cf9a05662cac2c32f31ee7e81f06d837f06d3f8e3'


def required_symbols(expected):
    """Require the unchanged frozen 188 identities; exclude only HNHPF coverage."""
    if (not isinstance(expected, list) or len(expected) != 188
            or any(not isinstance(symbol, str) for symbol in expected)
            or len(set(expected)) != 188 or 'HNHPF' not in expected):
        return None
    digest = sha256(json.dumps(sorted(expected), separators=(',', ':')).encode()).hexdigest()
    return set(expected) - set(EXCLUDED_SYMBOLS) if digest == FROZEN_UNIVERSE_SHA256 else None


def valid_delayed_reference(row):
    if row.get('us_delayed_data_available') is not True or row.get('us_live_data_available') is not False:
        return False
    price, delay = row.get('us_delayed_price'), row.get('us_delayed_minimum_seconds')
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (price, delay)) or price <= 0 or delay < 960:
        return False
    source, coverage, interval = (row.get(key) for key in (
        'us_delayed_source', 'us_delayed_coverage', 'us_delayed_interval'))
    if source == 'Alpaca SIP historical (>=16min delayed)':
        if coverage != 'consolidated' or interval not in {'1Min', '1Day'}:
            return False
    elif source == 'Yahoo Finance completed daily report cache':
        if coverage != 'provider_defined' or interval != '1Day' or row.get('us_delayed_cached') is not True:
            return False
    else:
        return False
    try:
        asof = datetime.fromisoformat(str(row['us_delayed_asof']).replace('Z', '+00:00'))
        fetched = datetime.fromisoformat(str(row['us_delayed_fetched_at']).replace('Z', '+00:00'))
        return (asof.tzinfo is not None and fetched.tzinfo is not None
                and fetched - asof >= timedelta(seconds=delay))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def summarize(report, expected):
    required = required_symbols(expected)
    if required is None:
        return {'passed': False, 'reason': 'invalid_frozen_universe', 'excluded_symbols': EXCLUDED_SYMBOLS,
                'exclusion_reason': 'user_requested', 'required_count': 187}
    data = report.get('data') if isinstance(report, dict) else None
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        return {'passed': False, 'reason': 'invalid_report_payload'}
    rows = [row for row in data if row.get('market') == 'US']
    if any(not isinstance(row.get('symbol'), str) for row in rows):
        return {'passed': False, 'reason': 'invalid_report_symbol'}
    required_rows = [row for row in rows if row['symbol'] in required]
    symbols = [row.get('symbol') for row in rows]
    valid_price = lambda row: isinstance(row.get('price'), (int, float)) and not isinstance(row.get('price'), bool) and math.isfinite(row['price']) and row['price'] > 0
    result = {
        'mode': 'free_delayed', 'report_updated_at': report.get('updated_at'),
        'excluded_symbols': EXCLUDED_SYMBOLS, 'exclusion_reason': 'user_requested',
        'required_count': len(required),
        'required_missing_symbols': sorted(required - {row['symbol'] for row in required_rows
            if valid_price(row) and valid_delayed_reference(row)}),
        'required_valid_formal_price_count': sum(valid_price(row) for row in required_rows),
        'required_validated_delayed_reference_count': sum(valid_delayed_reference(row) for row in required_rows),
        'requested_count': len(expected), 'reported_count': len(rows),
        'missing_symbols': sorted(set(expected) - set(symbols)),
        'unexpected_symbols': sorted(set(symbols) - set(expected)),
        'duplicate_count': len(symbols) - len(set(symbols)),
        'valid_formal_price_count': sum(valid_price(row) for row in rows),
        'delayed_reference_count': sum(row.get('us_delayed_data_available') is True for row in rows),
        'validated_delayed_reference_count': sum(valid_delayed_reference(row) for row in rows),
        'cached_yahoo_count': sum(row.get('us_delayed_cached') is True for row in rows),
        'existing_yahoo_daily_attested_count': sum(row.get('us_daily_price_available') is True for row in rows),
        'unexpected_live_count': sum(bool(row.get('us_live_data_available')) for row in rows),
        'unexpected_opra_count': sum(bool(row.get('us_option_data_available')) for row in rows),
        'raw_bar_leak_count': sum('us_delayed_bars' in row for row in rows),
        'market_values_exported': False, 'published': False,
        'basic_account_entitlement_verified': False,
    }
    result['passed'] = bool(
        len(rows) == len(expected) and not result['missing_symbols']
        and not result['unexpected_symbols'] and not result['duplicate_count']
        and result['required_valid_formal_price_count'] == len(required)
        and result['required_validated_delayed_reference_count'] == len(required)
        and not result['required_missing_symbols']
        and not result['unexpected_live_count'] and not result['unexpected_opra_count']
        and not result['raw_bar_leak_count'])
    return result


if __name__ == '__main__':
    report = json.loads((ROOT / 'reports/all_analysis.json').read_text())
    expected = json.loads((ROOT / 'tests/fixtures/us_free_probe_universe.json').read_text())['symbols']
    result = summarize(report, expected)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
