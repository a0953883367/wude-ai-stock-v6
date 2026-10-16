"""Read-only diagnostic using existing authorized relay; prints no market rows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from us_market_data import fetch_us_sip_snapshots


def main() -> int:
    fixture = json.loads((ROOT / 'tests/fixtures/us_free_probe_universe.json').read_text())
    symbols = fixture['symbols']
    if len(symbols) != 188 or len(set(symbols)) != 188:
        raise ValueError('expected frozen 188-symbol US universe')
    os.environ['ALPACA_DATA_MODE'] = 'free_delayed'
    rows = fetch_us_sip_snapshots(symbols, timeout=90)
    accepted = {symbol: row for symbol, row in rows.items()
                if symbol in symbols and row.get('us_delayed_data_available') is True
                and row.get('us_live_data_available') is False
                and row.get('us_delayed_minimum_seconds', 0) >= 960
                and row.get('us_delayed_coverage') == 'consolidated'}
    result = {
        'mode': 'free_delayed', 'source': fixture['source'],
        'universe_blob_sha': fixture['source_blob_sha'], 'requested_count': 188,
        'historical_available_count': len(accepted),
        'missing_symbols': sorted(set(symbols) - set(accepted)),
        'minute_count': sum(r.get('us_delayed_interval') == '1Min' for r in accepted.values()),
        'completed_daily_count': sum(r.get('us_delayed_interval') == '1Day' for r in accepted.values()),
        'unexpected_live_count': sum(bool(r.get('us_live_data_available')) for r in rows.values()),
        'status': 'complete' if len(accepted) == 188 else 'partial' if accepted else 'blocked',
        'basic_account_entitlement_verified': False,
        'paid_subscription_changed': False, 'market_values_exported': False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if accepted and not result['unexpected_live_count'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
