"""Read-only projection of existing frozen forecasts and their published outcomes.

No model, network, database initialization, settlement, or forecast writes.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import re

from prediction_engine.models import HORIZONS

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 48 * 1024 * 1024
GROUPS = ('TW_STOCK', 'TW_ETF', 'US_STOCK', 'US_ETF')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def direction(value):
    return ('UP' if value > 0 else 'DOWN' if value < 0 else 'FLAT') if finite(value) else 'UNKNOWN'


def _read(path, limit=MAX_FILE_BYTES):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('report_size_limit')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid_report')
    return value, raw


def load_catalog(reports):
    """Use declared full predictions, including every shard, never TOP10 rankings."""
    result = {'records': [], 'sources': [], 'issues': [], 'manifest': {}}
    try:
        manifest, raw = _read(reports / 'prediction_engine.json')
        result['manifest'] = manifest
        result['sources'].append({'path': 'prediction_engine.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                                  'updated_at': manifest.get('updated_at')})
        total = len(raw)
        for group in GROUPS:
            for code in HORIZONS:
                spec = (manifest.get('data_files', {}).get(group) or {}).get(code)
                if spec is None:
                    continue
                paths = [spec] if isinstance(spec, str) else spec.get('parts', []) if isinstance(spec, dict) else []
                records, sources = [], []
                try:
                    if not paths or len(paths) > 8 or len(paths) != len(set(paths)):
                        raise ValueError('invalid_parts')
                    for name in paths:
                        pattern = rf'prediction_engine_data_{group}_{code}(?:_part\d{{2}})?\.json'
                        if not isinstance(name, str) or not re.fullmatch(pattern, name):
                            raise ValueError('invalid_manifest_path')
                        path = reports / name
                        if path.resolve().parent != reports.resolve():
                            raise ValueError('invalid_manifest_path')
                        chunk, raw = _read(path)
                        total += len(raw)
                        if total > MAX_TOTAL_BYTES:
                            result['issues'].append('total_size_limit')
                            return result
                        if (chunk.get('group') != group or chunk.get('horizon_code') != code
                                or chunk.get('updated_at') != manifest.get('updated_at')
                                or chunk.get('contract_version') != manifest.get('contract_version')):
                            raise ValueError('mixed_report_generation')
                        rows = chunk.get('predictions')
                        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                            raise ValueError('invalid_predictions')
                        if any(r.get('asset_group') != group or r.get('horizon_code') != code
                               or r.get('market') != group.split('_')[0] for r in rows):
                            raise ValueError('row_scope_mismatch')
                        records.extend(rows)
                        sources.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                        'updated_at': chunk.get('updated_at')})
                    identities = [r.get('symbol') for r in records]
                    if len(identities) != len(set(identities)):
                        raise ValueError('duplicate_symbol_horizon')
                    result['records'].extend(records)
                    result['sources'].extend(sources)
                except (OSError, ValueError, TypeError):
                    result['issues'].append(f'{group}/{code}: missing_invalid_or_mixed_chunk')
    except (OSError, ValueError, TypeError, AttributeError):
        result['issues'].append('prediction_manifest_unavailable')
    return result


def _valid_identity(row, now):
    try:
        day = datetime.strptime(row['session_date'], '%Y-%m-%d').date()
        created = row.get('created_at')
        if created:
            at = datetime.fromisoformat(created)
            at = at if at.tzinfo else at.replace(tzinfo=ZoneInfo('Asia/Taipei'))
            if at > now or at.date() < day:
                return False
        zone = ZoneInfo('America/New_York' if row.get('market') == 'US' else 'Asia/Taipei')
        return (day <= now.astimezone(zone).date() and row.get('horizon_code') in HORIZONS
                and isinstance(row.get('model_version'), str) and bool(row['model_version'])
                and finite(row.get('expected_return_pct'))
                and row.get('target_side') == HORIZONS[row['horizon_code']]['side'])
    except (KeyError, TypeError, ValueError):
        return False


def _project(row):
    expected = row.get('expected_return_pct')
    evidence = row.get('evidence') or {}
    blocked = row.get('trade_blocked') is True or evidence.get('trade_blocked') is True
    # Old exports coerced missing storage evidence into false. False alone is
    # insufficient to establish that the frozen answer passed its guard.
    known = row.get('trade_block_evidence_recorded') is True or isinstance(evidence.get('trade_blocked'), bool)
    factors = row.get('factor_explanation') or evidence.get('factor_explanation') or {}
    outcome = {'status': 'not_published_or_pending', 'basis': 'frozen_close_to_matured_close',
               'expected_direction_correct': None, 'return_error_pp': None}
    realized = row.get('realized_return_pct')
    end = row.get('outcome_session_date')
    if row.get('status') == 'matured' and finite(realized) and isinstance(end, str) and end > row['session_date']:
        outcome.update(status='matured', session_date=end, realized_return_pct=realized,
                       actual_direction=direction(realized),
                       expected_direction_correct=direction(expected) == direction(realized),
                       return_error_pp=round(realized - expected, 4),
                       legacy_event_target_correct=row.get('direction_correct'))
    key = {k: row.get(k) for k in ('market', 'symbol', 'session_date', 'horizon_code')}
    return {'key': key, 'model_version': row.get('model_version'), 'created_at': row.get('created_at'),
            'label': HORIZONS[row['horizon_code']]['label'], 'horizon_sessions': HORIZONS[row['horizon_code']]['sessions'],
            'expected_return_pct': expected, 'direction': direction(expected),
            'event_target': row.get('target_side'),
            'event_probability_pct': row.get('probability_pct') if finite(row.get('probability_pct')) and 0 <= row['probability_pct'] <= 100 else None,
            'probability_calibrated': False,
            'trade_feasibility': 'blocked' if blocked else 'not_blocked_not_order_authorization' if known else 'unknown_frozen_guard_evidence',
            'buyability_score': row.get('buyability_score'),
            'upward_drivers': factors.get('upward_drivers') or [],
            'downward_drivers': factors.get('downward_drivers') or [],
            'factor_scope': '凍結模型貢獻，不是已證實因果；向上因素不一定支持本筆方向。',
            'trigger': None, 'invalidation': None,
            'rule_status': '此凍結模型未保存觸發／失效規則，不以目前均線補造',
            'outcome': outcome}


def build_evidence(catalog, symbol, market, now, reports):
    selected = [r for r in catalog['records'] if r.get('symbol') == symbol and r.get('market') == market]
    forecasts, issues = [], list(catalog['issues'])
    try:
        published = datetime.fromisoformat(catalog['manifest']['updated_at'])
        published = published if published.tzinfo else published.replace(tzinfo=ZoneInfo('Asia/Taipei'))
        if published > now:
            selected = []
            issues.append('future_prediction_publication')
    except (KeyError, TypeError, ValueError):
        selected = []
        issues.append('prediction_publication_unavailable')
    for row in selected:
        if not _valid_identity(row, now):
            issues.append('invalid_frozen_identity'); continue
        item = _project(row)
        if str(row.get('outcome_session_date') or '') > now.date().isoformat():
            item['outcome'] = {'status': 'future_outcome_rejected'}
        item['recent_outcomes'] = []
        for old in (row.get('recent_outcomes') or [])[:3]:
            if (not isinstance(old, dict) or not _valid_identity(old, now)
                    or any(old.get(k) != row.get(k) for k in ('symbol', 'market', 'asset_group', 'horizon_code'))
                    or old['session_date'] >= row['session_date']
                    or str(old.get('outcome_session_date') or '') > now.date().isoformat()):
                issues.append('mismatched_review_outcome'); continue
            review = _project(old)
            if review['outcome']['status'] == 'matured':
                item['recent_outcomes'].append(review)
        group = row['asset_group']; code = row['horizon_code']
        learning = ((catalog['manifest'].get('learning') or {}).get(group) or {}).get(code) or {}
        item['improvement_trial'] = {
            'scope': '報表產生時同資產組與週期的試驗狀態；不是凍結當時證據，也不是本檔已證實改善',
            'status': learning.get('status'), 'candidate_model_version': learning.get('model_version'),
            'trained_through': learning.get('trained_through'),
            'metrics': learning.get('metrics') or {}, 'control': learning.get('control') or {},
        }
        forecasts.append(item)
    diagnostics = []
    try:
        performance, _ = _read(reports / 'performance.json', 16 * 1024 * 1024)
        learning, _ = _read(reports / 'model_learning.json', 16 * 1024 * 1024)
        for report in (performance, learning):
            at = datetime.fromisoformat(report['updated_at'])
            at = at if at.tzinfo else at.replace(tzinfo=ZoneInfo('Asia/Taipei'))
            if at > now:
                raise ValueError('future_diagnostics')
        for event in (performance.get('error_cases') or {}).get('event_clusters') or []:
            if event.get('symbol') != symbol or event.get('market') != market:
                continue
            if str(event.get('evaluated_end_date') or '') > now.date().isoformat():
                continue
            cause = event.get('primary_cause')
            diagnostics.append({k: event.get(k) for k in ('event_id', 'source_start_date', 'evaluated_end_date',
                'horizons', 'diagnosis', 'learning_action', 'classification_status')})
            diagnostics[-1].update(scope='不同預測系統的歷史錯題，未與本筆凍結模型一對一對應；分類不是因果',
                candidate=((learning.get('shadow_execution_audit') or {}).get('candidates') or {}).get(cause),
                performance_updated_at=performance.get('updated_at'), learning_updated_at=learning.get('updated_at'))
            if len(diagnostics) >= 5:
                break
    except (OSError, ValueError, TypeError, KeyError):
        issues.append('historical_diagnostics_unavailable')
    return {'status': 'frozen_forecasts_available' if forecasts else 'unavailable',
            'forecasts': forecasts, 'sources': catalog['sources'], 'issues': issues,
            'related_historical_diagnostics': diagnostics,
            'investment_scope': {
                'quarter_model_codes': [f['key']['horizon_code'] for f in forecasts
                                        if f['key']['horizon_code'] in ('UP_60D', 'UP_63D')],
                'quarter_note': '約一季只對應既有60／63交易日模型；不把1／5日延伸成季度預測，未證實能穩定增加收入。',
                'long_term_status': 'not_implemented_or_validated',
                'long_term_note': '5–10年研究尚無對應模型或驗證；126交易日也不能當作長期成長結論。',
                'reusable_inputs': ['既有財報／營收／現金流與負債資料（須再核對期間、來源及完整性）',
                                    '既有估值、產業生命週期、公告新聞與公司事件資料'],
                'separate_planning_required': ['季度資金用途、可承受虧損及報酬評估方式尚未定義',
                                               '5–10年競爭優勢、成長持續性、資本配置與稀釋、估值情境及長期驗證須獨立規劃'],
            },
            'note': '方向依凍結預期報酬正負；事件機率未校準。歷史判斷不代表目前可交易；未新增模型呼叫。'}
