"""Offline free-mode recovery evidence; never changes report or trading state."""
import json
from pathlib import Path

from tools.validate_free_report import summarize
from watchlist import WATCHLIST
from us_free_reference_validation import references_current, generation_is_current


def healthy_free_report(report, analysis, *, now):
    """Bind current consumer rows to the complete, attested frozen publication."""
    try:
        expected = json.loads((Path(__file__).parent / 'tests/fixtures/us_free_probe_universe.json').read_text())['symbols']
        if (not generation_is_current(report.get('updated_at'), observed_at=now)
                or not report.get('period')
                or any(report.get(key) != analysis.get(key) for key in ('updated_at', 'period'))
                or not summarize(analysis, expected, observed_at=now)['passed']):
            return False
        rows = report['watchlist']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return False
        consumer = [row for row in rows if row.get('market') == 'US']
        symbols = [row.get('symbol') for row in consumer]
        if (len(symbols) != len(set(symbols))
                or set(symbols) != {row['symbol'] for row in WATCHLIST if row['market'] == 'US'}):
            return False
        full = {row['symbol']: row for row in analysis['data'] if row.get('market') == 'US'}
        # Prices and every reference field must describe the same source rows.
        for row in consumer:
            if 'us_delayed_bars' in row:
                return False
            source = full[row['symbol']]
            keys = {'price', 'us_live_data_available', 'us_option_data_available'} | {
                key for key in row.keys() | source.keys() if key.startswith('us_delayed_')}
            if any(row.get(key) != source.get(key) for key in keys):
                return False
        return references_current(report, consumer, observed_at=now)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
