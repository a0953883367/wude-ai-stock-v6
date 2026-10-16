"""Report-only completed-session references; independent of private minute quotes."""
from datetime import date, datetime, time, timezone
import math

from us_daily_shadow_history import NY, _symbols
from us_free_daily_fallback import DELAY, fetch_free_completed_daily

SOURCE = 'Alpaca SIP completed daily bars'


def fetch_free_report_daily(symbols, calendar=None, *, session=None, observed_at=None, telemetry=None):
    requested = _symbols(sorted(set(symbols)))
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError('timezone-aware observation required')
    if calendar is None:
        from config import SETTINGS
        from market_calendar import OfficialMarketCalendar
        calendar = OfficialMarketCalendar(SETTINGS.reports_dir / 'official_market_calendar.json',
                                          auto_refresh=False, allow_network=False)
    daily = fetch_free_completed_daily(requested, calendar, now=observed, session=session)
    output = {}
    calendar_years = {}
    attestation_failure = None
    for symbol, row in daily['rows'].items():
        # Reader verifies the session and OHLCV; independently enforce the public reference contract.
        try:
            day = date.fromisoformat(row['session_date'])
        except (KeyError, TypeError, ValueError):
            attestation_failure = 'invalid_bar_session'
            continue
        if day.year not in calendar_years:
            try:
                calendar_years[day.year] = calendar.relay_us_year(day.year)
            except (AttributeError, ValueError, RuntimeError, TypeError):
                calendar_years[day.year] = {}
                attestation_failure = 'official_calendar_unavailable'
        evidence = calendar_years[day.year]
        if not isinstance(evidence, dict):
            attestation_failure = 'official_calendar_invalid'
            continue
        session_details = evidence.get('session_details') or {}
        if not isinstance(session_details, dict):
            attestation_failure = 'official_calendar_invalid'
            continue
        details = session_details.get(day.isoformat()) or {}
        if (evidence.get('year') != day.year or evidence.get('status') != 'verified_alpaca'
                or evidence.get('sources') != ['Alpaca Market Calendar']
                or not isinstance(evidence.get('sessions'), list)
                or day.isoformat() not in evidence['sessions']):
            attestation_failure = attestation_failure or 'official_calendar_invalid'
            continue
        try:
            opening = time.fromisoformat(details['open'])
            closing = time.fromisoformat(details['close'])
            if opening >= closing or not time(9, 30) < closing <= time(16):
                attestation_failure = 'official_calendar_invalid'
                continue
            stamp = datetime.combine(day, closing, NY)
        except (KeyError, TypeError, ValueError):
            attestation_failure = 'official_calendar_invalid'
            continue
        price = row.get('close')
        eligible_at = datetime.combine(day, time(23, 59, 59), NY) + DELAY
        if (symbol not in requested or type(price) not in (int, float)
                or not math.isfinite(price) or price <= 0 or observed < eligible_at
                or observed - stamp < DELAY):
            attestation_failure = 'invalid_bar'
            continue
        output[symbol] = {
            'us_delayed_data_available': True, 'us_delayed_source': SOURCE,
            'us_delayed_price': price, 'us_delayed_asof': stamp.isoformat(),
            'us_delayed_fetched_at': daily['observed_at'],
            'us_delayed_session_date': day.isoformat(),
            'us_delayed_session_close': stamp.isoformat(),
            'us_delayed_calendar_source': 'Alpaca Market Calendar',
            'us_delayed_calendar_verified': True, 'us_delayed_session_completed': True,
            'us_delayed_minimum_seconds': int(DELAY.total_seconds()),
            'us_delayed_interval': '1Day', 'us_delayed_coverage': 'consolidated',
            'us_live_data_available': False,
        }
    sip_count = len(output)
    missing = sorted(set(requested) - set(output))
    if missing:
        from us_cached_daily_fallback import fetch_cached_us_daily, fetch_cached_us_daily_references
        output.update(fetch_cached_us_daily_references(missing, observed_at=observed))
        missing = sorted(set(requested) - set(output))
        if missing:
            output.update(fetch_cached_us_daily(missing, observed_at=observed))
    if telemetry is not None:
        from free_report_diagnostics import sanitize_diagnostics
        source = {'status': daily['status'], 'requested_count': len(requested),
                  'returned_count': sip_count, 'request_count': daily['request_count']}
        if daily['status'] == 'partial':
            source['reason'] = 'missing_latest_session'
        if attestation_failure:
            source.update(status='blocked', reason=attestation_failure)
        for key in ('reason', 'http_status'):
            if key in daily:
                source[key] = daily[key]
        for old, new in (('network_error_category', 'network_category'), ('network_error_phase', 'phase')):
            if old in daily:
                source[new] = daily[old]
        coverage = 'complete' if len(output) == len(requested) else 'partial' if output else 'unavailable'
        telemetry.update(sanitize_diagnostics({
            'source_daily': source, 'requested_count': len(requested), 'returned_count': len(output),
            'daily_available_count': sip_count, 'client_cache_count': len(output) - sip_count,
            'coverage_status': coverage,
            'status': ('recovered' if output else 'blocked') if source['status'] == 'blocked'
                      else 'complete' if coverage == 'complete' else 'partial',
        }))
    return output
