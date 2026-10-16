"""Shared no-network validation for isolated free display references."""
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

def valid_delayed_reference(row):
    if not isinstance(row, dict):
        return False
    if row.get('us_delayed_data_available') is not True or row.get('us_live_data_available') is not False:
        return False
    price, delay = row.get('us_delayed_price'), row.get('us_delayed_minimum_seconds')
    try:
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (price, delay)) or price <= 0 or delay < 960:
            return False
    except OverflowError:
        return False
    source, coverage, interval = (row.get(key) for key in (
        'us_delayed_source', 'us_delayed_coverage', 'us_delayed_interval'))
    if not all(isinstance(value, str) for value in (source, coverage, interval)):
        return False
    if source == 'Alpaca SIP historical (>=16min delayed)':
        if coverage != 'consolidated' or interval not in {'1Min', '1Day'}:
            return False
    elif source in {'Alpaca SIP completed daily bars', 'Alpaca SIP completed daily report cache'}:
        if coverage != 'consolidated' or interval != '1Day':
            return False
    elif source == 'Yahoo Finance completed daily report cache':
        if coverage != 'provider_defined' or interval != '1Day' or row.get('us_delayed_cached') is not True:
            return False
    else:
        return False
    try:
        asof = datetime.fromisoformat(str(row['us_delayed_asof']).replace('Z', '+00:00'))
        fetched = datetime.fromisoformat(str(row['us_delayed_fetched_at']).replace('Z', '+00:00'))
        if source in {'Alpaca SIP completed daily bars', 'Alpaca SIP completed daily report cache'}:
            close = datetime.fromisoformat(str(row['us_delayed_session_close']).replace('Z', '+00:00'))
            session_day = date.fromisoformat(row['us_delayed_session_date'])
            ny = ZoneInfo('America/New_York')
            if (row.get('us_delayed_calendar_verified') is not True
                    or row.get('us_delayed_session_completed') is not True
                    or row.get('us_delayed_calendar_source') != 'Alpaca Market Calendar'
                    or close.tzinfo is None or fetched.tzinfo is None or close != asof
                    or close.astimezone(ny).date() != session_day
                    or not time(9, 30) < close.astimezone(ny).time() <= time(16, 0)
                    or fetched.astimezone(ny).date() <= session_day
                    or fetched < datetime.combine(session_day, time(23, 59, 59), ny) + timedelta(seconds=delay)
                    or fetched - close < timedelta(seconds=delay)):
                return False
        if source == 'Alpaca SIP completed daily report cache':
            original = datetime.fromisoformat(str(row['us_delayed_origin_published_at']).replace('Z', '+00:00'))
            read_at = datetime.fromisoformat(str(row['us_delayed_cache_read_at']).replace('Z', '+00:00'))
            if (row.get('us_delayed_cached') is not True
                    or row.get('us_delayed_origin_source') != 'Alpaca SIP completed daily bars'
                    or original.tzinfo is None or read_at.tzinfo is None
                    or not fetched <= original <= read_at
                    or read_at.astimezone(ny).date() < session_day
                    or (read_at.astimezone(ny).date() - session_day).days > 7):
                return False
        return (asof.tzinfo is not None and fetched.tzinfo is not None
                and fetched - asof >= timedelta(seconds=delay))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False



def valid_report_reference(row):
    return (isinstance(row, dict) and isinstance(row.get('us_delayed_source'), str)
            and row.get('us_delayed_interval') == '1Day'
            and row.get('us_delayed_source') in {'Alpaca SIP completed daily bars',
                'Alpaca SIP completed daily report cache', 'Yahoo Finance completed daily report cache'}
            and valid_delayed_reference(row))
