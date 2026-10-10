"""Ask the existing private Railway relay for operational daily-source counts."""
from __future__ import annotations
import argparse
import json
import re
from hashlib import sha256
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


def sanitize_status(result, requested_count, expected_manifest=None, expected_categories=None):
    """Validate exact operational fields; no arbitrary provider strings/values."""
    blocked = {'schema_version': 1, 'status': 'blocked', 'reason': 'invalid_or_unavailable_relay_status',
               'requested_count': requested_count, 'decision_eligible': False, 'affects_formal': False,
               'durable_raw_retention': False, 'market_values_exported': False,
               'prospective_evaluation_started': False, 'bar_finality_verified': False,
               'corporate_actions_independently_verified': False}
    if not isinstance(result, dict) or result.get('version') != 'US-SIP-DAILY-IN-MEMORY-V2':
        return blocked
    constants = {'version': 'US-SIP-DAILY-IN-MEMORY-V2', 'source': 'Alpaca SIP historical daily bars',
                 'feed': 'sip', 'interval': '1Day', 'adjustment': 'split'}
    if any(result.get(k) != v for k, v in constants.items()):
        return blocked
    if not isinstance(result.get('status'), str) or result.get('status') not in {'blocked', 'probe_verified', 'computed_in_memory', 'partial_in_memory'}:
        return blocked
    if any(result.get(key) is not False for key in ('decision_eligible', 'affects_formal', 'durable_raw_retention',
                                                   'market_values_exported', 'prospective_evaluation_started', 'bar_finality_verified',
                                                   'corporate_actions_independently_verified')):
        return blocked
    clean = {**blocked, **constants, 'status': result['status']}
    clean.pop('reason')
    bounds = {'requested_count': (requested_count, requested_count),
              'stock_count': (0, requested_count), 'etf_count': (0, requested_count), 'unclassified_count': (0, requested_count), 'history_complete_count': (0, requested_count),
              'indicator_complete_count': (0, requested_count),
              'weekly_indicator_complete_count': (0, requested_count), 'daily_momentum_complete_count': (0, requested_count), 'research_plan_complete_count': (0, requested_count), 'request_count': (0, 24),
              'elapsed_ms': (0, 3600000), 'calendar_session_count': (4, 400), 'probe_session_count': (4, 4),
              'provider_http_status': (100, 599), 'x_ratelimit_limit': (0, 999999999),
              'x_ratelimit_remaining': (0, 999999999)}
    for key, (low, high) in bounds.items():
        if key not in result:
            if key in {'requested_count', 'stock_count', 'etf_count', 'unclassified_count', 'history_complete_count', 'indicator_complete_count', 'request_count',
                       'weekly_indicator_complete_count', 'daily_momentum_complete_count', 'research_plan_complete_count'}:
                return blocked
            continue
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            return blocked
        clean[key] = value
    if clean['stock_count'] + clean['etf_count'] + clean['unclassified_count'] != requested_count:
        return blocked
    if expected_categories is not None:
        for category, key in (('stock', 'stock_count'), ('etf', 'etf_count'), ('unknown', 'unclassified_count')):
            if clean[key] != sum(value == category for value in expected_categories.values()):
                return blocked
    digest = result.get('universe_manifest_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or (expected_manifest is not None and digest != expected_manifest):
        return blocked
    clean['universe_manifest_sha256'] = digest
    if any(clean.get(k, 0) > clean['indicator_complete_count'] for k in ('weekly_indicator_complete_count', 'daily_momentum_complete_count', 'research_plan_complete_count')):
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
        allowed = {'unexpected_bar_session', 'invalid_ohlcv', 'insufficient_contiguous_indicator_history',
                   'adjusted_price_discontinuity_requires_review', 'insufficient_volume_history', 'invalid_indicator'}
        if (not isinstance(reasons, dict) or set(reasons) - allowed
                or any(type(value) is not int or not 0 <= value <= requested_count for value in reasons.values())
                or sum(reasons.values()) != requested_count - clean['indicator_complete_count']):
            return blocked
        clean['blocked_reasons'] = dict(reasons)
    if 'coverage_notes' in result:
        notes = result['coverage_notes']
        if (not isinstance(notes, dict) or set(notes) - {'requested_window_incomplete',
                'insufficient_completed_weekly_history', 'research_geometry_unavailable'}
                or any(type(v) is not int or not 0 <= v <= requested_count for v in notes.values())):
            return blocked
        clean['coverage_notes'] = dict(notes)
    for key, expected in {'projection_version': 'US-PRIVATE-SIP-PROJECTION-V1',
                          'corporate_action_basis': 'provider_split_adjusted_as_observed_now'}.items():
        if key in result or clean['status'] in {'computed_in_memory', 'partial_in_memory'}:
            if result.get(key) != expected:
                return blocked
            clean[key] = expected
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
    if 'quality_diagnostics' in result:
        diagnostics = result['quality_diagnostics']
        allowed_labels = {'source_continuity_review', 'history_window_insufficient'}
        if (not isinstance(diagnostics, list) or (diagnostics and expected_categories is None)
                or len(diagnostics) > requested_count - clean['indicator_complete_count']):
            return blocked
        seen = set()
        validated = []
        for item in diagnostics:
            if not isinstance(item, dict) or set(item) != {'symbol', 'reason', 'sessions'}:
                return blocked
            symbol, reason, sessions = item['symbol'], item['reason'], item['sessions']
            if (not isinstance(symbol, str) or symbol not in expected_categories or symbol in seen
                    or not isinstance(reason, str) or reason not in allowed_labels
                    or not isinstance(sessions, list) or not 1 <= len(sessions) <= 16):
                return blocked
            try:
                dates = [date.fromisoformat(day) for day in sessions if isinstance(day, str)]
                latest = date.fromisoformat(clean['session_date'])
                if (len(dates) != len(sessions) or dates != sorted(set(dates))
                        or any(day.isoformat() != raw or not 0 <= (latest - day).days <= 400
                               for day, raw in zip(dates, sessions))):
                    return blocked
            except (ValueError, TypeError, KeyError):
                return blocked
            seen.add(symbol)
            validated.append({'symbol': symbol, 'reason': reason, 'sessions': sessions[:]})
        label_sources = {'source_continuity_review': 'adjusted_price_discontinuity_requires_review',
                         'history_window_insufficient': 'insufficient_contiguous_indicator_history'}
        if any(sum(item['reason'] == label for item in validated) != clean.get('blocked_reasons', {}).get(source, 0)
               for label, source in label_sources.items()):
            return blocked
        clean['quality_diagnostics'] = validated
    if clean['status'] == 'probe_verified':
        clean['next_stage'] = 'bounded_history_collection_not_started'
    elif clean['status'] in {'computed_in_memory', 'partial_in_memory'}:
        clean['next_stage'] = 'private_retention_rights_and_prospective_observation_required'
    return clean



def build_manifest(rows, *, probe_only=False):
    if probe_only:
        categories = {'AAPL': 'stock'}
    else:
        categories = {}
        for row in rows:
            if not isinstance(row, dict) or row.get('market') != 'US':
                continue
            symbol = str(row.get('symbol') or '').upper()
            if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', symbol):
                raise ValueError('invalid US universe symbol')
            category = 'etf' if 'ETF' in str(row.get('type') or '').upper() else 'stock'
            if symbol in categories and categories[symbol] != category:
                raise ValueError('conflicting US universe category')
            categories[symbol] = category
    symbols = sorted(categories)
    if not 1 <= len(symbols) <= 200:
        raise ValueError('US universe must contain 1 to 200 instruments')
    digest = sha256(json.dumps([[s, categories[s]] for s in symbols], separators=(',', ':')).encode()).hexdigest()
    return symbols, categories, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reports-dir', type=Path, default=Path('reports'))
    parser.add_argument('--collect-history', action='store_true')
    args = parser.parse_args()
    payload = json.loads((args.reports_dir / 'all_analysis.json').read_text())
    rows = payload.get('data') or []
    symbols, categories, manifest_hash = build_manifest(rows, probe_only=not args.collect_history)
    result = _relay_request('daily_shadow_status', {'symbols': symbols, 'collect_history': args.collect_history,
                                                   'instrument_types': categories}, 95) if symbols else {}
    clean = sanitize_status(result, len(symbols), expected_manifest=manifest_hash, expected_categories=categories)
    clean['scope'] = 'all_us_instruments_including_etfs' if args.collect_history else 'aapl_four_session_entitlement_probe'
    target = args.reports_dir / 'us_daily_shadow_status.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(target)
    print('US daily shadow:', clean.get('status'), 'indicators:', clean.get('indicator_complete_count', 0),
          '/', len(symbols), 'reason:', clean.get('reason') or clean.get('blocked_reasons') or 'none')


if __name__ == '__main__':
    main()
