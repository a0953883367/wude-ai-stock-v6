from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from report_research import _judgment_context, _research_judgment, integrate_research
from tests.test_stock_agent import Calendar

NOW = datetime(2026,10,18,14,55,52,tzinfo=timezone.utc)
BATCH = '2026-10-18 22:46:02'


def fixture(code='avoid'):
    row={'market':'US','symbol':'TEST','name':'Test','type':'個股','official_session_date':'2026-10-16',
         'news_data_available':True,'news_cache_stale':False,'news_scanned_at':NOW.isoformat(),
         'news_articles':[], 'outlook_reasons':['既有趨勢理由'],'outlook_change_condition':'站回既有均線並核對量價'}
    forecasts=[{'horizon_code':h,'status':'frozen_research','session_date':'2026-10-16','direction':'DOWN',
                'supporting_evidence':[{'label':'趨勢'}],'opposing_evidence':[{'label':'新聞'}]}
                for h in ('NEXT_1D','UP_5D','UP_60D','UP_63D')]
    research={'horizons':forecasts}
    context={'hub':{('US','TEST'):{'session_date':'2026-10-16','horizons':{'short':{'recommendation':code,'reason':'原中樞理由'}}}},
             'plans':{},'issues':[],'timing':{},'calendar':Calendar(),'now':NOW}
    return row,research,context


def test_reuses_judgment_separates_entry_held_and_does_not_authorize():
    row,research,context=fixture(); original=deepcopy((row,research))
    j=_research_judgment(row,research,{},context)
    assert (row,research)==original
    assert j['short']['inherited_recommendation']=='avoid'
    assert j['short']['reason']=='原中樞理由'
    assert '新進場候選' in j['short']['new_entry']
    assert '若已持有且原理由' in j['short']['if_already_held']
    assert j['short']['change_condition']==row['outlook_change_condition']
    assert '不補造門檻' in j['short']['condition_status']
    assert '是否持有' in j['short']['position_information_missing']
    assert not j['holdings_assumed'] and not j['execution_authorized']
    assert j['probability'] is None and j['target_price'] is None


def test_candidate_is_not_blocked_just_because_execution_is_disabled():
    row,research,context=fixture('can_scale')
    context['hub'][('US','TEST')]['conflicts']=[{'reason':'既有分歧','resolution_rule':'保留原處理，不加權'}]
    j=_research_judgment(row,research,{},context)
    assert '可進一步評估' in j['short']['new_entry']
    assert '現在可買' in j['short']['new_entry']
    assert any('不一致' in t for t in j['short']['important_conflicts'])
    assert any('空掃描不是利多' in t for t in j['short']['important_conflicts'])
    assert any('保留原處理，不加權' in t for t in j['short']['important_conflicts'])


def test_quarter_never_extends_short_forecasts_or_promotes_trial_status():
    row,research,context=fixture()
    learning={'US_STOCK':{h:{'status':'stable_champion','metrics':{'sample_count':9999}} for h in ('UP_60D','UP_63D')}}
    j=_research_judgment(row,research,learning,context)
    assert '偏空' in j['quarter']['conclusion']
    assert not j['quarter']['investment_judgment_supported']
    assert not j['quarter']['source_fully_verified']
    research['horizons']=research['horizons'][:2]
    j=_research_judgment(row,research,learning,context)
    assert not j['quarter']['forecast_dates_current']
    assert '缺完整 UP_60D／UP_63D 模型' in j['quarter']['missing']


def test_stale_calendar_or_mismatched_forecast_dates_are_not_current_judgments():
    row,research,context=fixture()
    context['now']=datetime(2026,10,20,23,tzinfo=timezone.utc)
    j=_research_judgment(row,research,{},context)
    assert '歷史參考' in j['headline'] and '最新同批資料' in j['short']['new_entry']
    assert not j['quarter']['forecast_dates_current']
    row,research,context=fixture()
    research['horizons'][-1]['session_date']='2026-10-15'
    assert not _research_judgment(row,research,{},context)['quarter']['forecast_dates_current']


def test_long_term_has_specific_missing_research_even_if_126d_exists():
    row,research,context=fixture()
    research['horizons'].append({'horizon_code':'UP_126D','status':'frozen_research','direction':'UP'})
    long=_research_judgment(row,research,{},context)['long_term']
    assert not long['investment_judgment_supported']
    for word in ('競爭力','成長','現金流','負債','稀釋','估值'):
        assert any(word in text for text in long['missing'])


@pytest.mark.parametrize('problem',['old','duplicate','escape'])
def test_hub_loader_rejects_mixed_batch_duplicates_and_paths(tmp_path,problem):
    manifest={'schema_version':6,'updated_at':BATCH,'model_version':'CENTRAL-DECISION-HUB-V6','decision_files':['decision_hub_01.json']}
    part={'schema_version':6,'updated_at':BATCH,'model_version':manifest['model_version'],'decisions':[{'market':'US','symbol':'TEST'}]}
    if problem=='old':part['updated_at']='2026-10-17 22:46:02'
    if problem=='duplicate':part['decisions']*=2
    if problem=='escape':manifest['decision_files']=['../decision_hub_01.json']
    (tmp_path/'decision_hub.json').write_text(json.dumps(manifest));(tmp_path/'decision_hub_01.json').write_text(json.dumps(part))
    context=_judgment_context(tmp_path,BATCH,NOW,Calendar())
    assert not context['hub'] and 'same_batch_hub_unavailable' in context['issues']


def test_missing_hub_does_not_invent_recommendation():
    row,research,context=fixture();context['hub']={}
    j=_research_judgment(row,research,{},context)
    assert j['short']['inherited_recommendation'] is None
    assert '缺可重用' in j['headline']


def test_published_full_pool_coverage_and_formal_fields_unchanged():
    reports=Path('reports');source=json.loads((reports/'all_analysis.json').read_text())
    original=deepcopy(source)
    # Replay the checked-in batch, not a fixed date that scheduled report
    # updates can overtake while a PR is being tested. This is not live data.
    checked_at=datetime.fromisoformat(source['updated_at'])
    if checked_at.tzinfo is None: checked_at=checked_at.replace(tzinfo=ZoneInfo('Asia/Taipei'))
    plan=json.loads((reports/'trade_plan_shadow.json').read_text())
    evaluated=datetime.fromisoformat(plan['evaluated_at'].replace('Z','+00:00'))
    checked_at=max(checked_at,evaluated)
    result=integrate_research(source,reports,engine_succeeded=True,judgment_now=checked_at)
    assert source==original
    assert len(result['data'])==len(source['data'])==result['research_integration']['judgment_rows_accounted']
    assert {(r['market'],r['symbol']) for r in result['data']}=={(r['market'],r['symbol']) for r in source['data']}
    hub_manifest=json.loads((reports/'decision_hub.json').read_text())
    expected={}
    if hub_manifest['updated_at']==source['updated_at']:
        for name in hub_manifest['decision_files']:
            part=json.loads((reports/name).read_text())
            if part['updated_at']!=source['updated_at']:
                expected={}
                break
            expected.update({(d['market'],d['symbol']):d['horizons']['short']['recommendation'] for d in part['decisions']})
    for old,new in zip(source['data'],result['data']):
        assert {k:v for k,v in old.items() if k!='research'}=={k:v for k,v in new.items() if k!='research'}
        j=new['research']['judgment']
        assert all(k in j for k in ('short','quarter','long_term'))
        assert j['short']['inherited_recommendation']==expected.get((new['market'],new['symbol']))
        assert not j['execution_authorized'] and not j['holdings_assumed']


def test_conclusion_precedes_price_rank_and_evidence_in_render():
    from notifier import _stock_block
    row,research,context=fixture();research['judgment']=_research_judgment(row,research,{},context)
    row['research']=research
    # The existing detail renderer needs compact forecast display fields.
    for h in research['horizons']:
        h.update(horizon_sessions=1,expected_return_pct=-1)
    text=_stock_block(row)
    assert text.startswith('Test TEST｜綜合研究判斷（非即時）')
    assert text.index('新進場：')<text.index('研究方向與證據（歷史凍結')
    assert '若已持有（未假設實際持倉）' in text


def test_future_batch_is_not_a_reusable_current_judgment(tmp_path):
    context=_judgment_context(tmp_path,'2026-10-20 22:46:02',NOW,Calendar())
    assert not context['hub'] and context['issues']==['future_analysis_batch']


def test_expired_plan_and_missing_condition_do_not_create_valid_entry_rule():
    row,research,context=fixture('can_scale');row.pop('outlook_change_condition')
    context['plans'][('US','TEST')]={'session_date':'2026-10-16','plans':{'short':{'conclusion':{
        'evaluated_at':'2026-10-16T22:00:00+00:00','expires_at':'2026-10-17T22:00:00+00:00','gates':[]}}}}
    j=_research_judgment(row,research,{},context)
    assert '計畫核對時間未生效或已過期' in j['short']['missing']
    assert j['short']['change_condition'] is None
    assert j['execution_authorized'] is False
