"""Same-batch full-pool research projection, not execution or public-plan release.

Reuses the stock Agent's frozen-evidence reader. No provider/model/ledger calls.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from prediction_engine.models import HORIZONS
from stock_prediction_evidence import load_catalog, build_evidence

MAX_PROJECTION_BYTES = 15 * 1024 * 1024  # leave headroom below stock_agent MAX_REPORT_BYTES

SEMANTICS = {
    'research': '凍結方向與證據不是買賣指令；負方向也是研究結果。',
    'data': '資料合格計畫數不是買點數；缺資料不等於模型沒有研究方向。',
    'execution': 'eligible_actions=0 是公開行動未放行，不是研究機會為零。',
    'native_chatgpt_task': 'not_modified_or_acceptance_verified',
}


def _compact_forecast(f):
    def drivers(items):
        return [{k: item.get(k) for k in ('factor', 'label', 'expected_return_contribution_pct')}
                for item in items if isinstance(item, dict)]
    up, down = drivers(f['upward_drivers']), drivers(f['downward_drivers'])
    return {
        'status': 'frozen_research', 'session_date': f['key']['session_date'],
        'horizon_code': f['key']['horizon_code'], 'horizon_sessions': f['horizon_sessions'],
        'model_version': f['model_version'], 'created_at': f['created_at'],
        'direction': f['direction'], 'expected_return_pct': f['expected_return_pct'],
        'event_target': f['event_target'], 'probability_calibrated': False,
        'frozen_data_quality_pct': f.get('data_quality_pct'),
        'supporting_evidence': down if f['direction'] == 'DOWN' else up if f['direction'] == 'UP' else [],
        'opposing_evidence': up if f['direction'] == 'DOWN' else down if f['direction'] == 'UP' else [],
        'trigger': f['trigger'], 'invalidation': f['invalidation'], 'rule_status': 'not_saved_in_frozen_model',
        'frozen_trade_feasibility': f['trade_feasibility'], 'outcome': f['outcome'],
        'recent_outcomes': [{'session_date': old['key']['session_date'], **{k: old[k] for k in ('model_version', 'expected_return_pct', 'direction', 'outcome')}}
                            for old in f['recent_outcomes']],
        'review_sample_count': len(f['recent_outcomes']),
    }


def integrate_research(payload, reports, *, engine_succeeded):
    """Return a copy with exactly one research object for every analysis identity."""
    rows = payload.get('data')
    if not isinstance(rows, list) or payload.get('analyzed_count') != len(rows):
        raise ValueError('analysis_census_mismatch')
    identities = [(r.get('market'), r.get('symbol')) for r in rows]
    if any(m not in ('TW', 'US') or not isinstance(s, str) or not s for m, s in identities) or len(set(identities)) != len(rows):
        raise ValueError('analysis_identity_invalid_or_duplicate')
    now = datetime.fromisoformat(payload['updated_at'])
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo('Asia/Taipei'))
    catalog = load_catalog(Path(reports))
    same_batch = engine_succeeded is True and catalog['manifest'].get('updated_at') == payload['updated_at']
    if not same_batch:
        catalog = {**catalog, 'records': [], 'issues': catalog['issues'] + ['prediction_engine_failed_or_batch_mismatch']}
    result = deepcopy(payload)
    forecast_count = 0
    # Diagnostics from another prediction system are intentionally not copied into
    # this compact projection. Matched outcomes still use the complete reader.
    for row in result['data']:
        evidence = build_evidence(catalog, row['symbol'], row['market'], now, Path(reports), include_related_diagnostics=False)
        codes = Counter(f['key']['horizon_code'] for f in evidence['forecasts'])
        if any(n > 1 for n in codes.values()):
            evidence['issues'].append('ambiguous_asset_group_horizon')
        forecasts = {f['key']['horizon_code']: _compact_forecast(f) for f in evidence['forecasts'] if codes[f['key']['horizon_code']] == 1}
        forecast_count += len(forecasts)
        horizons = [forecasts.get(code) or {'horizon_code': code, 'horizon_sessions': spec['sessions'],
                     'status': 'missing_or_invalid', 'direction': None, 'expected_return_pct': None,
                     'supporting_evidence': [], 'opposing_evidence': [], 'trigger': None, 'invalidation': None,
                     'outcome': {'status': 'unavailable'}, 'review_sample_count': 0}
                    for code, spec in HORIZONS.items()]
        comparable = defaultdict(list)
        for f in forecasts.values():
            comparable[(f['session_date'], f['horizon_sessions'])].append(f)
        conflicts = [{'session_date': day, 'horizon_sessions': sessions,
                      'models': [{'horizon_code': f['horizon_code'], 'direction': f['direction'], 'model_version': f['model_version']} for f in group]}
                     for (day, sessions), group in comparable.items() if len({f['direction'] for f in group}) > 1]
        row['research'] = {
            'status': 'available' if forecasts else 'unavailable', 'horizons': horizons,
            'data_sufficiency': {k: row.get(k) for k in ('official_session_date', 'market_data_quality',
                'market_data_missing', 'source_daily_ohlcv_complete', 'source_daily_ohlcv_session_date',
                'news_scanned_at', 'news_data_available', 'news_cache_stale')},
            'data_scope': 'existing_report_observations_not_new_freshness_or_execution_certification',
            'issues': evidence['issues'],
            'model_disagreement': {'same_session_horizon_conflicts': conflicts,
                'legacy_outlook': row.get('outlook_direction'), 'legacy_outlook_horizon': row.get('outlook_horizon'),
                'legacy_next_session_date': row.get('next_session_source_session_date'),
                'legacy_next_session_votes': {key: item.get('direction') for key, item in (row.get('next_session_model_votes') or {}).items() if isinstance(item, dict)},
                'scope': 'different_systems_or_horizons_are_not_pooled_votes; missing_disagreement_is_not_consensus'},
            'existing_conditions': {'source_session_date': row.get('official_session_date'),
                'scope': 'existing_current_report_rules_not_frozen_forecast_rules_or_new_orders',
                **{k: row.get(k) for k in ('outlook_change_condition', 'short_term_trigger',
                    'scenario_continuation', 'scenario_no_chase', 'scenario_breakdown_text')}},
            'execution': {'action_release_enabled': False, 'numeric_release_enabled': False,
                'us_numeric_release_enabled': False, 'decision_eligible': False,
                'existing_trade_guard_blocked': row.get('trade_guard_blocked'),
                'existing_trade_guard_reason': row.get('trade_guard_reason'),
                'scope': 'research_reader_cannot_authorize_execution_or_override_public_plan_policy'},
        }
    source_keys = sorted(':'.join(k) for k in identities)
    result['research_integration'] = {
        'version': 'FULL-POOL-RESEARCH-V1',
        'producer': 'deterministic_existing_stock_agent_evidence_reader', 'model_calls': 0, 'provider_calls': 0,
        'status': 'complete_projection' if same_batch else 'research_unavailable',
        'source_batch_at': payload['updated_at'], 'prediction_batch_at': catalog['manifest'].get('updated_at'),
        'identity_rows_accounted': len(rows), 'analysis_rows': len(rows), 'identity_accounting_complete': True,
        'identity_sha256': hashlib.sha256(json.dumps(source_keys, separators=(',', ':')).encode()).hexdigest(),
        'by_market': dict(Counter(m for m, _ in identities)), 'forecast_count': forecast_count,
        'prediction_identities_outside_analysis_count': len({(r.get('market'), r.get('symbol')) for r in catalog['records']} - set(identities)),
        'missing_horizon_count': len(rows)*len(HORIZONS)-forecast_count,
        'sources': catalog['sources'], 'issues': catalog['issues'], 'semantics': SEMANTICS,
        'validation_by_group_and_horizon': catalog['manifest'].get('learning', {}) if same_batch else {},
        'validation_scope': 'published_group_trial_metrics_not_per_stock_win_probability',
        'review_sample_scope': 'published_recent_examples_not_total_validation_sample_size',
        'evidence_basis': 'frozen_model_contributions_not_independent_votes_or_causation',
        'pending_candidates': 'unchanged; not silently promoted into analyzed rows',
    }
    assert [(r['market'], r['symbol']) for r in result['data']] == identities
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_PROJECTION_BYTES:
        raise ValueError('research_projection_size_limit')
    return result


def integrate_research_safely(payload, reports, *, engine_succeeded):
    try:
        return integrate_research(payload, reports, engine_succeeded=engine_succeeded)
    except Exception:
        result = deepcopy(payload)
        result['research_integration'] = {'status': 'integration_failed', 'source_batch_at': payload.get('updated_at'),
                                         'identity_accounting_complete': False, 'semantics': SEMANTICS}
        # Never preserve a carried-forward object as this batch's research.
        for row in result.get('data', []):
            row.pop('research', None)
        return result


def finalize_research_markdown(report, payload, latest_md, original_markdown):
    """Replace only latest.md after the complete projection is published.

    The initial saved report remains the fallback, including its data pointer.
    No delivery, archive duplication, model call or report JSON rewrite occurs.
    """
    from notifier import render_markdown
    temporary = latest_md.with_name(latest_md.name + '.research.tmp')
    try:
        if report['updated_at'] != payload['updated_at']:
            raise ValueError('report_batch_mismatch')
        enriched = deepcopy(report)
        index = {(r['market'], r['symbol']): r.get('research') for r in payload['data']}
        for row in enriched.get('watchlist', []) + enriched.get('top', []):
            research = index.get((row.get('market'), row.get('symbol')))
            if research is not None:
                row['research'] = research
        enriched['research_integration'] = payload['research_integration']
        text = render_markdown(enriched)
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(latest_md)
        return text
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return original_markdown
