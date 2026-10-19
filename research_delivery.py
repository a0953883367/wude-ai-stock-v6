"""Small public delivery index over already published research; no analysis runs."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from prediction_engine.models import HORIZONS
from stock_prediction_evidence import _read, load_catalog

MAX_BYTES = 300_000
JUDGMENT_BYTES = 200_000
INDEX = 'research_delivery.json'
VERSION = 'PUBLIC-RESEARCH-DELIVERY-V1'


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


# Explicit projection: new private/price fields cannot silently become public.
JUDGMENT_FIELDS = {
    'freshness': 'calendar_verified completed fresh expires_at valid_through_session',
    'short': 'horizon inherited_recommendation reason legacy_outlook_reasons forecast_basis supporting_reasons opposing_reasons important_conflicts factor_scope new_entry if_already_held change_condition condition_status missing position_information_missing',
    'quarter': 'horizon conclusion direction_reference forecast_dates_current source_fully_verified learning_status investment_judgment_supported missing supporting_reasons opposing_reasons factor_scope',
    'long_term': 'horizon conclusion investment_judgment_supported missing',
}


def _judgment(value):
    if not isinstance(value, dict) or any(k not in value for k in JUDGMENT_FIELDS):
        raise ValueError('judgment_missing')
    if (value.get('execution_authorized') is not False or value.get('holdings_assumed') is not False
            or value.get('probability') is not None or value.get('target_price') is not None):
        raise ValueError('judgment_release_contract')
    result = {k: value[k] for k in ('mode', 'evaluated_at', 'source_session_date', 'headline',
                                  'execution_authorized', 'holdings_assumed', 'probability', 'target_price')}
    for section, fields in JUDGMENT_FIELDS.items():
        source = value[section]
        if not isinstance(source, dict):
            raise ValueError('judgment_section_invalid')
        result[section] = {k: source[k] for k in fields.split() if k in source}
    # Nested objects also have an explicit schema; lists elsewhere contain text.
    result['short']['forecast_basis'] = [
        {k: item[k] for k in ('code', 'date', 'direction') if k in item}
        for item in value['short'].get('forecast_basis', [])]
    result['quarter']['learning_status'] = {
        k: v for k, v in value['quarter'].get('learning_status', {}).items()
        if k in ('UP_60D', 'UP_63D') and (v is None or isinstance(v, str))}
    def check(node):
        if isinstance(node, dict):
            for v in node.values(): check(v)
        elif isinstance(node, list):
            if any(not isinstance(v, str) for v in node):
                raise ValueError('judgment_nontext_list')
        elif node is not None and type(node) not in (str, bool):
            raise ValueError('judgment_unexpected_value')
    basis = result['short'].pop('forecast_basis')
    learning = result['quarter'].pop('learning_status')
    for section in JUDGMENT_FIELDS:
        if any(isinstance(v, dict) for v in result[section].values()):
            raise ValueError('judgment_unexpected_nested_object')
    check(result)
    for item in basis: check(item)
    result['short']['forecast_basis'] = basis
    result['quarter']['learning_status'] = learning
    return result


def build_delivery(reports):
    """Validate the complete source census, then return bytes without writes."""
    reports = Path(reports)
    analysis, analysis_raw = _read(reports/'all_analysis.json', 16*1024*1024)
    rows = analysis['data']; batch = analysis['updated_at']
    identities = [(r['market'], r['symbol']) for r in rows]
    if (not rows or analysis.get('analyzed_count') != len(rows)
            or len(set(identities)) != len(rows)
            or any(m not in ('TW', 'US') or not isinstance(s, str) or not s for m, s in identities)):
        raise ValueError('analysis_census_invalid')
    meta = analysis.get('research_integration') or {}
    catalog = load_catalog(reports)
    if (meta.get('status') != 'complete_projection' or catalog['issues']
            or catalog['manifest'].get('updated_at') != batch):
        raise ValueError('prediction_batch_or_integrity')
    expected_hashes = {s['path']: s['sha256'] for s in meta.get('sources', [])}
    files = []; locations = {}
    for source in catalog['sources']:
        name = source['path']
        obj, raw = _read(reports/name)
        if (len(raw) > MAX_BYTES or _sha(raw) != source['sha256']
                or expected_hashes.get(name) != _sha(raw) or obj.get('updated_at') != batch):
            raise ValueError('prediction_hash_batch_or_size')
        file_id = len(files)
        files.append({'file_id': file_id, 'path': name, 'sha256': _sha(raw), 'bytes': len(raw), 'updated_at': batch})
        if name == 'prediction_engine.json': continue
        for record in obj['predictions']:
            key = (record['market'], record['symbol'], record['horizon_code'])
            if key in locations: raise ValueError('duplicate_prediction_identity')
            locations[key] = (file_id, record)
    wanted = {(m, s, h) for m, s in identities for h in HORIZONS}
    if set(locations) != wanted:
        raise ValueError('incomplete_or_extra_prediction_census')
    projected = []; mapping = []
    for row in rows:
        m, s = row['market'], row['symbol']
        research = row['research']
        horizons = research['horizons']
        if len(horizons) != len(HORIZONS) or {h['horizon_code'] for h in horizons} != set(HORIZONS):
            raise ValueError('research_horizon_census')
        refs = {}
        for horizon in horizons:
            code = horizon['horizon_code']; file_id, record = locations[m, s, code]
            if (horizon.get('status') != 'frozen_research' or any(
                    horizon.get(k) != record.get(k) for k in
                    ('session_date', 'model_version', 'created_at', 'expected_return_pct'))):
                raise ValueError('research_prediction_mismatch')
            refs[code] = file_id
        projected.append({'market': m, 'symbol': s, 'name': row.get('name'),
                          'research': {'judgment': _judgment(research.get('judgment'))}})
        mapping.append({'market': m, 'symbol': s, 'prediction_files': refs})
    output = {}; parts = []; chunk = []
    def encoded(items):
        return _bytes({'schema_version': VERSION, 'updated_at': batch, 'data': items})
    def flush():
        name = f'research_judgment_{len(parts)+1:02}.json'; raw = encoded(chunk)
        output[name] = raw
        parts.append({'path': name, 'sha256': _sha(raw), 'bytes': len(raw),
                      'updated_at': batch, 'identity_count': len(chunk)})
        for entry in mapping[sum(p['identity_count'] for p in parts[:-1]):sum(p['identity_count'] for p in parts)]:
            entry['judgment_file'] = name
    for row in projected:
        if len(encoded([row])) > JUDGMENT_BYTES: raise ValueError('judgment_row_oversize')
        if chunk and len(encoded(chunk+[row])) > JUDGMENT_BYTES:
            flush(); chunk = []
        chunk.append(row)
    if chunk: flush()
    index = {'schema_version': VERSION, 'status': 'complete', 'updated_at': batch,
             'identity_count': len(rows), 'horizon_count': len(wanted),
             'identity_sha256': _sha(_bytes(sorted(f'{m}:{s}' for m, s in identities))),
             'source': {'path': 'all_analysis.json', 'sha256': _sha(analysis_raw), 'bytes': len(analysis_raw)},
             'judgment_files': parts, 'prediction_files': files, 'identities': mapping,
             'reader_contract': {
                 'pin_repository_commit': True, 'verify_every_file_hash_and_batch': True,
                 'read_predictions_not_rankings': True, 'require_exact_identity_and_horizon_census': True,
                 'empty_truncated_or_missing_content_is_incomplete': True,
                 'fallback_subset_is_not_complete_research': True,
                 'direction_from_signed_expected_return_not_target_side': True,
                 'probabilities_are_not_calibrated': True,
                 'execution_authorized': False,
                 'scope': 'existing_public_research_not_new_investment_validation'}}
    output[INDEX] = _bytes(index)
    if len(output[INDEX]) > MAX_BYTES: raise ValueError('index_oversize')
    return output


def _atomic(path, raw):
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(raw)
    temporary.replace(path)


def publish_delivery_safely(reports):
    """Publish index last; a failed generation must not advertise old success."""
    reports = Path(reports)
    try:
        output = build_delivery(reports)
        for name, raw in output.items():
            if name != INDEX: _atomic(reports/name, raw)
        _atomic(reports/INDEX, output[INDEX])
        return True
    except (OSError, ValueError, KeyError, TypeError):
        logging.warning('Public research delivery incomplete; core analysis unchanged')
        try:
            analysis, _ = _read(reports/'all_analysis.json', 16*1024*1024)
            batch = analysis.get('updated_at')
        except (OSError, ValueError): batch = None
        try:
            _atomic(reports/INDEX, _bytes({'schema_version': VERSION, 'status': 'unavailable',
                                         'updated_at': batch, 'reason': 'delivery_integrity_check_failed',
                                         'identities': [], 'judgment_files': [], 'prediction_files': []}))
        except OSError:
            # Preserve the core report even if this optional publication fails.
            # Readers must always check the requested batch, never trust old success.
            try: (reports/INDEX).unlink(missing_ok=True)
            except OSError: logging.error('Cannot invalidate research delivery index; reader batch check required')
        return False
