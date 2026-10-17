"""Offline free-mode recovery evidence; never changes report or trading state."""
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.validate_free_report import summarize
from watchlist import WATCHLIST


def healthy_free_report(report, analysis, *, now):
    """Bind current consumer rows to the complete, attested frozen publication."""
    try:
        expected = json.loads((Path(__file__).parent / 'tests/fixtures/us_free_probe_universe.json').read_text())['symbols']
        if (not report.get('updated_at') or not report.get('period')
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
        ny = ZoneInfo('America/New_York')
        for payload, references in ((analysis, list(full.values())), (report, consumer)):
            publication = datetime.fromisoformat(payload['us_daily_reference_published_at'].replace('Z', '+00:00'))
            if publication.tzinfo is None or publication > now:
                return False
            for row in references:
                if row['symbol'] == 'HNHPF' and row.get('us_delayed_data_available') is not True:
                    continue
                asof = datetime.fromisoformat(row['us_delayed_asof'].replace('Z', '+00:00'))
                fetched = datetime.fromisoformat(row['us_delayed_fetched_at'].replace('Z', '+00:00'))
                session = asof.astimezone(ny).date()
                if (asof.tzinfo is None or fetched.tzinfo is None
                        or not asof <= fetched <= publication <= now
                        or not 1 <= (now.astimezone(ny).date() - session).days <= 7
                        or fetched < datetime.combine(session + timedelta(days=1), time(), ny) + timedelta(
                            seconds=960 if row['us_delayed_source'] == 'Yahoo Finance completed daily report cache' else 959)):
                    return False
                if row.get('us_delayed_cached') is True and row.get('us_delayed_origin_published_at'):
                    read = datetime.fromisoformat(row['us_delayed_cache_read_at'].replace('Z', '+00:00'))
                    if read.tzinfo is None or read > publication:
                        return False
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
