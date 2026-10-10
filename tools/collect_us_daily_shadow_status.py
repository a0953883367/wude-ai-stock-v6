"""Ask the existing private Railway relay for operational daily-source counts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from us_market_data import _relay_request

from datetime import date, datetime

REASONS = {
    'official_calendar_unavailable', 'official_calendar_unverified', 'official_calendar_invalid',
    'official_calendar_insufficient', 'invalid_ohlcv', 'invalid_bar', 'invalid_bar_session',
    'request_time_budget', 'provider_network_failure', 'provider_authentication_denied',
    'provider_entitlement_denied', 'provider_rate_limited', 'provider_http_failure',
    'invalid_provider_json', 'invalid_provider_payload', 'unexpected_provider_symbol',
    'duplicate_provider_session', 'invalid_pagination', 'pagination_budget_exhausted',
    'insufficient_indicator_history', 'insufficient_volume_history', 'existing_credentials_unavailable',
    'probe_incomplete', 'unexpected_content_encoding', 'response_byte_budget',
}


def sanitize_status(result, requested_count):
    """Validate exact operational fields; no arbitrary provider strings/values."""
    blocked = {'schema_version': 1, 'status': 'blocked', 'reason': 'invalid_or_unavailable_relay_status',
               'requested_count': requested_count, 'decision_eligible': False, 'affects_formal': False,
               'durable_raw_retention': False, 'market_values_exported': False,
               'prospective_evaluation_started': False, 'bar_finality_verified': False}
    if not isinstance(result, dict) or result.get('version') != 'US-SIP-DAILY-IN-MEMORY-V1':
        return blocked
    constants = {'version': 'US-SIP-DAILY-IN-MEMORY-V1', 'source': 'Alpaca SIP historical daily bars',
                 'feed': 'sip', 'interval': '1Day', 'adjustment': 'raw'}
    if any(result.get(k) != v for k, v in constants.items()):
        return blocked
    if not isinstance(result.get('status'), str) or result.get('status') not in {'blocked', 'probe_verified', 'computed_in_memory', 'partial_in_memory'}:
        return blocked
    if any(result.get(key) is not False for key in ('decision_eligible', 'affects_formal', 'durable_raw_retention',
                                                   'market_values_exported', 'prospective_evaluation_started', 'bar_finality_verified')):
        return blocked
    clean = {**blocked, **constants, 'status': result['status']}
    clean.pop('reason')
    bounds = {'requested_count': (requested_count, requested_count), 'history_complete_count': (0, requested_count),
              'indicator_complete_count': (0, requested_count), 'request_count': (0, 24),
              'calendar_session_count': (4, 400), 'probe_session_count': (4, 4),
              'provider_http_status': (100, 599), 'x_ratelimit_limit': (0, 999999999),
              'x_ratelimit_remaining': (0, 999999999)}
    for key, (low, high) in bounds.items():
        if key not in result:
            if key in {'requested_count', 'history_complete_count', 'indicator_complete_count', 'request_count'}:
                return blocked
            continue
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            return blocked
        clean[key] = value
    if clean['indicator_complete_count'] > clean['history_complete_count']:
        return blocked
    if not isinstance(result.get('probe_status'), str) or result.get('probe_status') not in {'not_run', 'blocked', 'passed'}:
        return blocked
    clean['probe_status'] = result['probe_status']
    if clean['status'] != 'blocked' and (clean['probe_status'] != 'passed' or clean.get('probe_session_count') != 4):
        return blocked
    if clean['status'] == 'computed_in_memory' and clean['indicator_complete_count'] != requested_count:
        return blocked
    if clean['status'] == 'probe_verified' and (clean['indicator_complete_count'] or clean['history_complete_count']):
        return blocked
    if 'reason' in result:
        if not isinstance(result['reason'], str) or result['reason'] not in REASONS:
            return blocked
        clean['reason'] = result['reason']
    if 'blocked_reasons' in result:
        reasons = result['blocked_reasons']
        allowed = {'incomplete_session_coverage', 'raw_price_discontinuity_requires_review',
                   'insufficient_indicator_history', 'insufficient_volume_history', 'invalid_indicator'}
        if (not isinstance(reasons, dict) or set(reasons) - allowed
                or any(type(value) is not int or not 0 <= value <= requested_count for value in reasons.values())
                or sum(reasons.values()) != requested_count - clean['indicator_complete_count']):
            return blocked
        clean['blocked_reasons'] = dict(reasons)
    for key in ('observed_at', 'session_date'):
        if key not in result:
            continue
        try:
            if key == 'session_date':
                parsed = date.fromisoformat(result[key])
            else:
                parsed = datetime.fromisoformat(result[key])
                if parsed.tzinfo is None:
                    return blocked
            clean[key] = parsed.isoformat()
        except (TypeError, ValueError):
            return blocked
    if clean['status'] != 'blocked' and (not all(key in clean for key in ('observed_at', 'session_date', 'calendar_session_count'))
                                             or clean['request_count'] < 1):
        return blocked
    if clean['status'] == 'probe_verified':
        clean['next_stage'] = 'bounded_history_collection_not_started'
    elif clean['status'] in {'computed_in_memory', 'partial_in_memory'}:
        clean['next_stage'] = 'private_retention_rights_and_prospective_observation_required'
    return clean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reports-dir', type=Path, default=Path('reports'))
    parser.add_argument('--collect-history', action='store_true')
    args = parser.parse_args()
    payload = json.loads((args.reports_dir / 'all_analysis.json').read_text())
    rows = payload.get('data') or []
    symbols = sorted({str(row.get('symbol') or '').upper() for row in rows
                      if row.get('market') == 'US' and 'ETF' not in str(row.get('type') or '').upper()})
    if not args.collect_history:
        symbols = ['AAPL']
    result = _relay_request('daily_shadow_status', {'symbols': symbols, 'collect_history': args.collect_history}, 95) if symbols else {}
    clean = sanitize_status(result, len(symbols))
    target = args.reports_dir / 'us_daily_shadow_status.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(target)
    print('US daily shadow:', clean.get('status'), 'indicators:', clean.get('indicator_complete_count', 0),
          '/', len(symbols), 'reason:', clean.get('reason') or clean.get('blocked_reasons') or 'none')


if __name__ == '__main__':
    main()
