"""Explicit, opt-in count-only free report receipts; never raw logs or market rows."""
from __future__ import annotations
import json
import math
from pathlib import Path

_ENUMS = {
    'status': {'partial', 'running', 'complete', 'recovered', 'blocked', 'unavailable'},
    'stage': {'minute', 'missing_daily', 'complete'},
    'phase': {'oidc_configuration', 'oidc_acquisition', 'oidc_http', 'oidc_response',
        'relay_post', 'relay_http', 'relay_response', 'complete', 'before_request',
        'connect_or_headers', 'http', 'body', 'json', 'payload', 'symbols', 'bar_list',
        'bar_shape', 'bar_timestamp', 'bar_ohlcv', 'bar_window', 'duplicate_bar', 'pagination', 'output'},
    'reason': {'missing_latest_session', 'configuration_unavailable', 'empty_oidc_token', 'empty_data',
        'provider_http_failure', 'provider_network_failure', 'invalid_json', 'invalid_value',
        'invalid_payload', 'invalid_bar', 'out_of_range_bar', 'unexpected_symbol',
        'invalid_bars', 'duplicate_bar', 'invalid_pagination', 'pagination_budget',
        'request_time_budget', 'response_byte_budget', 'existing_credentials_unavailable', 'completed_sessions_unavailable',
        'official_calendar_unavailable', 'official_calendar_unverified', 'official_calendar_invalid',
        'official_calendar_insufficient', 'invalid_provider_json', 'invalid_provider_payload',
        'invalid_bar_session', 'invalid_ohlcv', 'unexpected_provider_symbol',
        'duplicate_provider_session', 'pagination_budget_exhausted', 'unexpected_content_encoding',
        'provider_authentication_denied', 'provider_entitlement_denied', 'provider_rate_limited'},
    'network_category': {'connect_timeout', 'read_timeout', 'tls_error', 'proxy_error',
        'chunked_encoding_error', 'content_decoding_error', 'timeout', 'connection_error', 'request_error'},
    'coverage_status': {'complete', 'partial', 'unavailable'},
    'interval': {'1Min', '1Day'},
}
_NUMBERS = {'schema_version', 'request_timeout_seconds', 'identity_request_count',
    'post_request_count', 'requested_count', 'returned_count', 'request_count',
    'completed_page_count', 'validated_bar_count', 'validated_symbol_count',
    'minimum_delay_seconds', 'total_budget_seconds', 'minute_window_minutes',
    'page_limit', 'response_byte_limit', 'response_bytes', 'elapsed_ms',
    'minute_available_count', 'missing_for_daily_count', 'daily_available_count',
    'client_cache_count', 'client_returned_count', 'client_rejected_count'}


def sanitize_diagnostics(value, depth=0):
    if not isinstance(value, dict) or depth > 2:
        return {}
    result = {}
    for key, item in value.items():
        if key in _ENUMS and isinstance(item, str) and item in _ENUMS[key]:
            result[key] = item
        elif key in _NUMBERS and type(item) in (int, float):
            try:
                if math.isfinite(item) and 0 <= item <= 10**12:
                    result[key] = item
            except OverflowError:
                pass
        elif key == 'http_status' and (item is None or type(item) is int and 100 <= item <= 599):
            result[key] = item
        elif key in {'relay', 'source_daily'} and depth == 0:
            result[key] = sanitize_diagnostics(item, depth + 1)
        elif key == 'failures' and isinstance(item, list) and depth == 0:
            result[key] = [sanitize_diagnostics(entry, depth + 1) for entry in item[:3] if isinstance(entry, dict)]
    return result


def write_receipt(path, diagnostics, *, market_stage_completed):
    if not path:
        return False
    receipt = {'receipt_version': 1, 'mode': 'free_delayed',
               'market_stage_completed': market_stage_completed is True,
               'diagnostics': sanitize_diagnostics(diagnostics)}
    try:
        Path(path).write_text(json.dumps(receipt, ensure_ascii=False), encoding='utf-8')
        return True
    except (OSError, TypeError, ValueError):
        return False


def print_receipt(path):
    try:
        with Path(path).open('rb') as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        receipt = json.loads(raw)
        if not isinstance(receipt, dict) or receipt.get('receipt_version') != 1 or receipt.get('mode') != 'free_delayed':
            raise ValueError()
        safe = {'receipt_version': 1, 'mode': 'free_delayed',
                'market_stage_completed': receipt.get('market_stage_completed') is True,
                'diagnostics': sanitize_diagnostics(receipt.get('diagnostics'))}
    except (OSError, TypeError, ValueError, RecursionError):
        safe = {'receipt_status': 'unavailable', 'reason': 'receipt_missing'}
    print(json.dumps(safe, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    import sys
    print_receipt(sys.argv[1] if len(sys.argv) == 2 else '')
