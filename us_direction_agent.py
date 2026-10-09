"""Read-only direction-ledger diagnosis for the existing bounded Agent."""
from pathlib import Path
from datetime import datetime
import json

from market_calendar import OfficialMarketCalendar
from performance import _snapshot_integrity, _us_forecast_window
from completed_us_session import completed_us_session


def _read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def inspect_us_direction(reports: Path, now: datetime) -> dict:
    rows = [r for r in _read(reports / 'all_analysis.json').get('data', [])
            if isinstance(r, dict) and r.get('market') == 'US']
    result = {'status': 'not_observable', 'reason': 'official_us_rows_missing',
              'needs_silent_refresh': False, 'verified_recovered': False,
              'last_source_session_date': '', 'latest_official_session_date': '',
              'historical_backfill_allowed': False}
    if not rows:
        return result
    dates = {str(r.get('official_session_date') or '') for r in rows}
    if len(dates) != 1 or '' in dates:
        return {**result, 'status': 'blocked_evidence', 'reason': 'mixed_or_missing_us_sessions'}
    source = next(iter(dates))
    result['latest_official_session_date'] = source
    history = _read(reports / 'prediction_history.json')
    snapshots = history.get('snapshots', []) if history.get('version') == 6 else []
    verified = [s for s in snapshots if isinstance(s, dict) and s.get('market') == 'US'
                and s.get('audit_schema_version') == 4 and _snapshot_integrity(s) == 'verified'
                and any(r.get('validation_eligible') is True for r in s.get('predictions', []))]
    last = max((str(s.get('session_date') or '') for s in verified), default='')
    result['last_source_session_date'] = last
    current = next((s for s in verified if s.get('session_date') == source), None)
    required = {'US_ETF' if 'ETF' in str(r.get('type', '')).upper() else 'US_STOCK' for r in rows}
    covered = {r.get('cohort') for r in (current or {}).get('predictions', [])
               if r.get('validation_eligible') is True}
    calendar = OfficialMarketCalendar(reports / 'official_market_calendar.json', auto_refresh=False, allow_network=False)
    capture_window = _us_forecast_window(calendar, source, str((current or {}).get('captured_at') or '')) if current else {}
    if current and required <= covered and capture_window.get('status') == 'ready':
        return {**result, 'status': 'verified_current', 'reason': 'immutable_eligible_snapshot_matches_official_session',
                'verified_recovered': True, 'snapshot_id': current.get('id'),
                'captured_at': current.get('captured_at')}
    progress = _read(reports / 'us_direction_progress.json')
    if current and (not required <= covered or capture_window.get('reason') != 'official_calendar_unavailable'):
        return {**result, 'status': 'blocked_manual', 'reason': 'immutable_receipt_cohort_or_capture_evidence_invalid'}
    if any(r.get('market_contract_valid') is not True for r in rows):
        return {**result, 'status': 'blocked_evidence', 'reason': 'official_session_contract_incomplete'}
    window = _us_forecast_window(calendar, source, now.isoformat())
    reason = window.get('reason')
    if window.get('status') == 'ready':
        return {**result, 'status': 'ready_silent_refresh', 'reason': 'direction_receipt_missing_inside_forecast_window',
                'needs_silent_refresh': True}
    if reason == 'official_calendar_unavailable' and progress.get('calendar_credentials_available') is False:
        return {**result, 'status': 'blocked_manual', 'reason': 'github_alpaca_calendar_credentials_missing',
                'required_secrets': ['ALPACA_API_KEY_ID', 'ALPACA_API_SECRET_KEY']}
    if reason == 'official_calendar_unavailable' and any(item.get('status') == 'failed_stop_no_retry' for item in progress.get('calendar_refresh_attempts', [])):
        return {**result, 'status': 'blocked_manual', 'reason': 'calendar_refresh_failed_stopped'}
    if reason == 'official_calendar_unavailable' and completed_us_session(rows, now.isoformat()):
        # The normal workflow has the calendar credentials. It alone may fetch
        # once and enforce the actual next-open gate; this observer never guesses it.
        return {**result, 'status': 'ready_silent_refresh', 'reason': 'calendar_bootstrap_required',
                'needs_silent_refresh': True}
    return {**result, 'status': 'waiting_next_close' if reason == 'forecast_window_missed_no_backfill' else 'blocked_evidence',
            'reason': reason, 'next_session_date': window.get('next_session_date')}
