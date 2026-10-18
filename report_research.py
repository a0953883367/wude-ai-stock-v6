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


def _judgment_context(reports, batch, now, calendar=None):
    """Read existing judgments only; no hub/plan recomputation or ledger writes."""
    from stock_prediction_evidence import _read
    from market_calendar import OfficialMarketCalendar
    import re
    context = {'hub': {}, 'plans': {}, 'issues': [], 'sources': [], 'timing': {}, 'now': now,
               'calendar': calendar or OfficialMarketCalendar(Path(reports)/'official_market_calendar.json',
                                                              auto_refresh=False, allow_network=False)}
    batch_at = datetime.fromisoformat(batch)
    if batch_at.tzinfo is None: batch_at = batch_at.replace(tzinfo=ZoneInfo('Asia/Taipei'))
    if batch_at > now:
        context['issues'].append('future_analysis_batch')
        return context
    def read(name, limit=4*1024*1024):
        obj, raw = _read(Path(reports)/name, limit)
        context['sources'].append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'updated_at': obj.get('updated_at')})
        return obj
    try:
        manifest = read('decision_hub.json')
        if manifest.get('updated_at') != batch or manifest.get('schema_version') != 6 or manifest.get('model_version') != 'CENTRAL-DECISION-HUB-V6':
            raise ValueError('hub_batch_or_version')
        paths = manifest.get('decision_files')
        if not isinstance(paths, list) or not paths or len(paths)>32 or any(not isinstance(p,str) or not re.fullmatch(r'decision_hub_\d{2}\.json',p) for p in paths) or len(set(paths))!=len(paths):
            raise ValueError('hub_manifest')
        index = {}
        for path in paths:
            if (Path(reports)/path).resolve().parent != Path(reports).resolve():
                raise ValueError('hub_path')
            part = read(path)
            if part.get('updated_at') != batch or part.get('schema_version') != 6 or part.get('model_version') != manifest['model_version']:
                raise ValueError('hub_chunk_batch')
            for row in part['decisions']:
                key = (row['market'], row['symbol'])
                if key in index: raise ValueError('hub_duplicate')
                index[key] = row
        context['hub'] = index
    except (OSError, ValueError, KeyError, TypeError):
        context['issues'].append('same_batch_hub_unavailable')
    try:
        plans = read('trade_plan_shadow.json',16*1024*1024)
        if (plans.get('updated_at') != batch or plans.get('source_decision_hub_updated_at') != batch
                or plans.get('model_version') != 'TRADE-PLAN-SHADOW-V4'):
            raise ValueError('plan_batch_or_version')
        index = {}
        for row in plans['plans']:
            key = (row['market'], row['symbol'])
            if key in index: raise ValueError('plan_duplicate')
            index[key] = row
        context['plans'] = index
    except (OSError, ValueError, KeyError, TypeError):
        context['issues'].append('same_batch_plan_checks_unavailable')
    return context


def _research_judgment(row, research, learning, context):
    """Explain existing recommendations, never invent a score or entry threshold."""
    from shadow_stock_conclusion import _timing, _news_observation_valid
    key = (row['market'], row['symbol']); session = row.get('official_session_date')
    timing_key = (row['market'], session)
    if timing_key not in context['timing']:
        context['timing'][timing_key] = _timing(context['calendar'], row['market'], session, 1, context['now'])
    timing = context['timing'][timing_key]
    fresh = all(timing.get(k) is True for k in ('calendar_verified','completed','fresh'))
    hub = context['hub'].get(key) or {}
    if hub.get('session_date') != session: hub = {}
    plan_row = context['plans'].get(key) or {}
    if plan_row.get('session_date') != session: plan_row = {}
    short_plan = (plan_row.get('plans') or {}).get('short') or {}
    conclusion = short_plan.get('conclusion') or {}
    short = (hub.get('horizons') or {}).get('short') or {}
    code = short.get('recommendation')
    # Source/expiry gates describe the published plan, not current quote execution.
    missing = list(dict.fromkeys(context['issues'] + research.get('issues', [])))
    if not hub: missing.append('缺同批同交易日的既有中樞判斷')
    if not fresh: missing.append('缺可核實的最新已完成交易日；目前只可回顧歷史判斷')
    if not conclusion: missing.append('缺同批既有計畫來源、條件與失效核對')
    else:
        missing += [str(g.get('reason') or g.get('code')) for g in conclusion.get('gates',[]) if g.get('passed') is not True]
        for field, relation in (('evaluated_at','future'),('expires_at','expired')):
            try:
                at = datetime.fromisoformat(conclusion[field].replace('Z','+00:00'))
                if at.tzinfo is None or (at>context['now'] if relation=='future' else at<=context['now']):
                    missing.append('計畫核對時間未生效或已過期')
            except (KeyError,TypeError,ValueError): missing.append('計畫核對／失效時間缺失')
    if (row.get('news_data_available') is not True or row.get('news_cache_stale') is True or
            not _news_observation_valid(row.get('news_scanned_at'),context['now'])):
        missing.append('缺有效時點的新聞核對')
    missing += ['凍結模型未保存可驗證的進場及失效規則', '缺與原始計畫分離的後續合格報價與進場評估']
    labels = {'avoid':'既有短線研究不支持列為新進場候選',
              'can_scale':'既有短線研究列為可進一步評估的候選，尚不能判定現在可買',
              'wait_pullback':'既有短線研究有條件支持，但應等待原計畫確認',
              'watch':'既有短線研究以觀察為主，條件尚未形成',
              'data_insufficient':'既有短線研究資料不足，不能判定是否適合進場'}
    headline = labels.get(code, '缺可重用的短線綜合判斷，不能以方向排名替代')
    if not fresh: headline = '資料時效未通過：'+headline+'（僅歷史參考）'
    # Both sets come from the frozen signed reader; no new factor weighting.
    short_forecasts = [h for h in research['horizons'] if h['horizon_code'] in ('NEXT_1D','UP_5D') and h['status']=='frozen_research']
    if len(short_forecasts)!=2 or any(f['session_date']!=session for f in short_forecasts):
        missing.append('缺與來源交易日一致的完整下一日／五日預判；不同日期因素只作歷史參考')
    if (conclusion.get('validation') or {}).get('formal_adoption_ready') is not True:
        missing.append('既有短線判斷尚無已驗證的投資效能結論')
    support = list(dict.fromkeys(str(x.get('label') or x.get('factor')) for f in short_forecasts for x in f['supporting_evidence']))
    opposition = list(dict.fromkeys(str(x.get('label') or x.get('factor')) for f in short_forecasts for x in f['opposing_evidence']))
    conflicts = []
    if len({f['direction'] for f in short_forecasts})>1:
        conflicts.append('下一日與五日方向不同；期間不同，不合併為一致短線訊號')
    if code in ('can_scale','avoid') and any(f['direction']==('DOWN' if code=='can_scale' else 'UP') for f in short_forecasts):
        conflicts.append('既有中樞建議與至少一個凍結方向不一致，保留衝突、不重算勝負')
    if row.get('news_data_available') is True and not row.get('news_articles'):
        conflicts.append('新聞模型因子可能有正貢獻，但空掃描不是利多或無風險證據')
    conflicts += [(str(c.get('summary') or c.get('reason') or c.get('code') or '中樞既有衝突待核對')
                   + '；既有處理：' + str(c.get('resolution_rule') or '未記錄'))
                  if isinstance(c,dict) else str(c) for c in hub.get('conflicts',[])]
    if short_plan.get('recommendation') and short_plan['recommendation'] != code:
        conflicts.append('中樞與既有計畫的短線建議不同：'+str(code)+'／'+str(short_plan['recommendation'])+'；不自行覆蓋任一判斷')
    if not fresh or not hub:
        entry = '先取得最新同批資料再判斷新進場；不把歷史結論延用為現在建議'
    else: entry = labels.get(code, headline)
    if not fresh or not hub or code=='data_insufficient':
        held = '若已持有，不能因資料缺漏直接推論應賣出；先核對原持有理由與風險限制'
    elif code=='avoid':
        held = '若已持有且原理由是短線轉強，應優先重審該理由；現有研究不支持把續抱視為理所當然，尚不能指定減碼或退出'
    else:
        held = '若已持有，僅在原持有理由仍成立時繼續觀察；新進場候選資格不等於續抱判斷，須另核對持倉風險'
    quarter = [h for h in research['horizons'] if h['horizon_code'] in ('UP_60D','UP_63D') and h['status']=='frozen_research']
    group = row['market']+('_ETF' if 'ETF' in str(row.get('type','')).upper() else '_STOCK')
    validation = {code: (learning.get(group) or {}).get(code,{}).get('status') for code in ('UP_60D','UP_63D')}
    quarter_missing = []
    if len(quarter)!=2: quarter_missing.append('缺完整 UP_60D／UP_63D 模型')
    quarter_current = fresh and len(quarter)==2 and all(f['session_date']==session for f in quarter)
    if not quarter_current: quarter_missing.append('60／63日預判日期與最新完成交易日未全部核對通過')
    directions = {f['direction'] for f in quarter}
    direction_text = {'UP':'偏多','DOWN':'偏空','FLAT':'持平'}.get(next(iter(directions)),'未知') if len(directions)==1 else '分歧'
    # Existing learning records describe challenger trials, not validated
    # investment decisions for the active forecast. Never invent a pass flag.
    quarter_missing += [str(g.get('reason') or g.get('code')) for g in conclusion.get('gates',[])
                        if g.get('code') in ('source','source_date','ohlcv','calendar','completed','freshness','price','snapshot') and g.get('passed') is not True]
    quarter_missing += ['缺與本次使用模型版本對應的三個月樣本外／前向績效驗證結論',
                        '缺三個月已驗證的進場、失效與持有風險規則']
    quarter_text = ('60／63日模型目前僅提供'+direction_text+'研究參考；不足以判定三個月買入或續抱'
                    if quarter_current else '三個月資料不完整或不新鮮，只保留歷史方向，不作目前投資判斷')
    long_missing = ['公司競爭力與護城河的可驗證研究','5～10年成長持續性與情境','帶期別與來源的現金流及資本支出序列',
                    '負債、償債及融資風險','股數變化、稀釋與資本配置','估值假設及敏感度','長期方法與結果驗證']
    if 'ETF' in str(row.get('type','')).upper():
        long_missing = ['ETF策略、成分股穿透、費用、追蹤與商品條款的長期研究']+long_missing
    return {'mode':'synthesis_of_existing_research_not_new_model', 'evaluated_at':context['now'].isoformat(),
            'source_session_date':session, 'freshness':timing, 'headline':headline,
            'short': {'horizon':'1～5交易日','inherited_recommendation':code,
                'reason':short.get('reason'), 'legacy_outlook_reasons':row.get('outlook_reasons') or [],
                'forecast_basis':[{'code':f['horizon_code'],'date':f['session_date'],'direction':f['direction']} for f in short_forecasts],
                'supporting_reasons':support, 'opposing_reasons':opposition, 'important_conflicts':conflicts,
                'factor_scope':'支持／反對各凍結方向，不一定支持中樞建議；中樞理由另列，不合併計票',
                'new_entry':entry, 'if_already_held':held,
                'change_condition':row.get('outlook_change_condition'),
                'condition_status':'既有定性觀察條件；量價／資金轉強未保存可驗證定義，不補造門檻',
                'missing':list(dict.fromkeys(missing)),
                'position_information_missing':['是否持有','持有成本與部位','投資期限及原始持有理由','可承受風險']},
            'quarter':{'horizon':'60／63交易日（約三個月）','conclusion':quarter_text,
                       'direction_reference':direction_text,'forecast_dates_current':quarter_current,
                       'source_fully_verified':False,
                       'learning_status':validation,'investment_judgment_supported':False,'missing':quarter_missing,
                       'supporting_reasons':list(dict.fromkeys(str(x.get('label') or x.get('factor')) for f in quarter for x in f['supporting_evidence'])),
                       'opposing_reasons':list(dict.fromkeys(str(x.get('label') or x.get('factor')) for f in quarter for x in f['opposing_evidence'])),
                       'factor_scope':'僅60／63日模型因素；不同期間不加權合併'},
            'long_term':{'horizon':'5～10年','conclusion':'尚無足夠公司長期研究，不能判斷長期買入或續抱',
                         'investment_judgment_supported':False,'missing':long_missing},
            'execution_authorized':False,'holdings_assumed':False,'probability':None,'target_price':None}

def integrate_research(payload, reports, *, engine_succeeded, judgment_now=None, calendar=None):
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
    judgment_now = judgment_now or now
    if judgment_now.tzinfo is None: raise ValueError("judgment_clock_must_be_aware")
    context = _judgment_context(reports, payload["updated_at"], judgment_now, calendar)
    result = deepcopy(payload)
    forecast_count = 0
    # Diagnostics from another prediction system are intentionally not copied into
    # this compact projection. Matched outcomes still use the complete reader.
    for row in result['data']:
        evidence = build_evidence(catalog, row['symbol'], row['market'], judgment_now, Path(reports), include_related_diagnostics=False)
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
        row['research']['judgment'] = _research_judgment(row, row['research'], catalog['manifest'].get('learning') or {}, context)
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
        'judgment_sources': context['sources'], 'judgment_issues': context['issues'],
        'judgment_rows_accounted': len(result['data']), 'judgment_evaluated_at': judgment_now.isoformat(),
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


def integrate_research_safely(payload, reports, *, engine_succeeded, judgment_now=None, calendar=None):
    try:
        return integrate_research(payload, reports, engine_succeeded=engine_succeeded, judgment_now=judgment_now, calendar=calendar)
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
