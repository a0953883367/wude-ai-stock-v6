from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import pytest
from test_tw_prospective_registry import calendar, candidate, official, risk, REGISTERED, NEXT_CLOSE
from tw_prospective_runtime import update_tw_prospective_report, build_current_risk_snapshots, REGISTRY_FILE


def prepared(tmp_path):
    calendar(tmp_path)
    reports=tmp_path/'reports'; reports.mkdir()
    (reports/'official_market_calendar.json').write_bytes((tmp_path/'calendar.json').read_bytes())
    return reports, {'updated_at':'2026-10-09 17:00:00','plans':[candidate()]}


def context(quote, current=None):
    return {'official_records':{quote['symbol']:quote},'risk_snapshots':{quote['symbol']:current} if current else {}}


def test_writer_registers_and_later_actual_close_evaluates_frozen_plan(tmp_path):
    reports, report=prepared(tmp_path)
    update_tw_prospective_report(reports,report,now=REGISTERED,evaluation_context=context(official()))
    assert report['tw_prospective_registry']['summary']['registered_total']==1
    assert report['tw_prospective_registry']['summary']['next_observation_session']=='2026-10-12'
    assert report['plans'][0]['prospective_entries'][0]['frozen_levels']['entry_high']==105
    initial=json.loads((reports/REGISTRY_FILE).read_text())
    later=deepcopy(report);later['plans']=[]
    state=update_tw_prospective_report(reports,later,now=NEXT_CLOSE,
        evaluation_context=context(official('2026-10-12',103,NEXT_CLOSE),risk()))
    assert state['summary']['triggered_close_only']==1
    assert state['records'][0]['frozen']==initial['records'][0]['frozen']
    assert 'raw_record"' not in (reports/REGISTRY_FILE).read_text()
    assert later['tw_prospective_registry']['summary']['registered_total']==1


def test_corrupt_ledger_never_overwritten_or_reported_ready(tmp_path):
    reports,report=prepared(tmp_path);p=reports/REGISTRY_FILE;p.write_text('corrupt')
    with pytest.raises(ValueError):
        update_tw_prospective_report(reports,report,now=REGISTERED,evaluation_context={})
    assert p.read_text()=='corrupt';assert 'tw_prospective_registry' not in report


def test_failed_persistence_does_not_claim_registration(tmp_path,monkeypatch):
    reports,report=prepared(tmp_path)
    monkeypatch.setattr(Path,'replace',lambda *a,**kw: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        update_tw_prospective_report(reports,report,now=REGISTERED,evaluation_context=context(official()))
    assert 'tw_prospective_registry' not in report


def test_empty_runtime_has_no_network(tmp_path):
    reports,report=prepared(tmp_path);report['plans']=[]
    def forbidden(*a,**kw):raise AssertionError('unexpected network')
    update_tw_prospective_report(reports,report,now=REGISTERED,fetch_records=forbidden)
    assert report['tw_prospective_registry']['summary']['registered_total']==0


def risk_batch(reports, report):
    report['updated_at']=NEXT_CLOSE.isoformat()
    row={'market':'TW','symbol':'2330.TW','session_date':'2026-10-12','source_snapshot':{},
         'evidence':[{'source_id':'verified_news','market':'TW','symbol':'2330.TW','provenance':'news_scan',
                      'as_of':NEXT_CLOSE.isoformat(),'affects_decision':True,'direction':'neutral','status':'available'}]}
    index={'updated_at':report['updated_at'],'decision_files':['decision_hub_01.json']}
    (reports/'decision_hub.json').write_text(json.dumps(index))
    (reports/'decision_hub_01.json').write_text(json.dumps({'updated_at':report['updated_at'],'decisions':[row]}))
    return row


def test_real_batch_risk_requires_current_session_and_complete_ca(tmp_path):
    reports, report=prepared(tmp_path);risk_batch(reports,report)
    records={'2330.TW':official('2026-10-12',103,NEXT_CLOSE)}
    actual=build_current_risk_snapshots(reports,report,records,now=NEXT_CLOSE)
    assert actual['2330.TW']['source_validity']=='verified'
    assert actual['2330.TW']['news']['status']=='verified'
    assert actual['2330.TW']['corporate_actions']['status']=='unsupported'
    old=dict(records);old['2330.TW']=official()
    assert build_current_risk_snapshots(reports,report,old,now=NEXT_CLOSE)['2330.TW']['source_validity']=='unverified'


def test_old_batch_cannot_claim_new_risk_observation(tmp_path):
    reports,report=prepared(tmp_path);risk_batch(reports,report)
    (reports/'decision_hub.json').write_text(json.dumps({'updated_at':'2026-10-09 17:00:00','decision_files':['decision_hub_01.json']}))
    assert build_current_risk_snapshots(reports,report,{},now=NEXT_CLOSE)=={}


def test_news_expiry_separate_and_adverse_scan_blocks(tmp_path):
    reports,report=prepared(tmp_path);row=risk_batch(reports,report)
    records={'2330.TW':official('2026-10-12',103,NEXT_CLOSE)}
    actual=build_current_risk_snapshots(reports,report,records,now=NEXT_CLOSE+timedelta(hours=18))
    assert actual['2330.TW']['news']['status']=='expired'
    row['evidence'][0]['direction']='oppose'
    (reports/'decision_hub_01.json').write_text(json.dumps({'updated_at':report['updated_at'],'decisions':[row]}))
    actual=build_current_risk_snapshots(reports,report,records,now=NEXT_CLOSE)
    assert actual['2330.TW']['risk_blocks']==['current_verified_news_risk']


def test_normal_briefing_hook_and_publisher_persist_registry():
    import inspect
    from briefing import _update_trade_plan_shadow_safely
    from tools.publish_shadow_report_batch import REPORT_FILES
    assert 'write_trade_plan_report(reports_dir, update_registry=False)' in inspect.getsource(_update_trade_plan_shadow_safely)
    assert 'reports/tw_prospective_registry.json' in REPORT_FILES
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/stock-briefing.yml').read_text()
    assert 'git add reports' in workflow


def test_briefing_read_only_path_cannot_rewrite_ledger(tmp_path, monkeypatch):
    import trade_plan_shadow
    reports, report=prepared(tmp_path)
    update_tw_prospective_report(reports,report,now=REGISTERED,evaluation_context=context(official()))
    before=(reports/REGISTRY_FILE).read_bytes()
    report.update(status="ready", summary={"total":1}, evidence_ablation={})
    monkeypatch.setattr(trade_plan_shadow,"build_trade_plan_report", lambda *a,**kw: deepcopy(report))
    trade_plan_shadow.write_trade_plan_report(reports,update_registry=False)
    assert (reports/REGISTRY_FILE).read_bytes()==before
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/stock-briefing.yml').read_text()
    assert workflow.index('Safely advance prospective shadow registry') > workflow.index('Report commit could not be published')
    assert 'run: python tools/publish_shadow_report_batch.py --max-attempts 3' in workflow


def test_identity_actions_need_fresh_complete_official_snapshot(tmp_path):
    from tw_prospective_runtime import _identity_action_status
    reports,report=prepared(tmp_path)
    payload={'generated_at':NEXT_CLOSE.isoformat(),'summary':{'unmatched':0},'events':[],
             'source_health':{'twse_registry':{'ok':True},'twse_announcements':{'ok':True}}}
    path=reports/'corporate_actions_shadow.json';path.write_text(json.dumps(payload))
    assert _identity_action_status(reports,'2330.TW','2026-10-12',NEXT_CLOSE)[0]=='clear'
    payload['events']=[{'symbol':'2330.TW','type':'TRADING_HALT'}];path.write_text(json.dumps(payload))
    assert _identity_action_status(reports,'2330.TW','2026-10-12',NEXT_CLOSE)[0]=='blocked'
    payload['events']=[];payload['generated_at']=REGISTERED.isoformat();path.write_text(json.dumps(payload))
    assert _identity_action_status(reports,'2330.TW','2026-10-12',NEXT_CLOSE)[0]=='unsupported'


def test_briefing_does_not_display_corrupt_but_parseable_registry_as_ready(tmp_path, monkeypatch):
    import trade_plan_shadow
    reports, report=prepared(tmp_path)
    update_tw_prospective_report(reports,report,now=REGISTERED,evaluation_context=context(official()))
    path=reports/REGISTRY_FILE
    state=json.loads(path.read_text());state["registry_digest"]="bad";path.write_text(json.dumps(state))
    before=path.read_bytes()
    report.update(status="ready",summary={"total":1},evidence_ablation={})
    monkeypatch.setattr(trade_plan_shadow,"build_trade_plan_report",lambda *a,**kw:deepcopy(report))
    target=trade_plan_shadow.write_trade_plan_report(reports,update_registry=False)
    assert json.loads(target.read_text())["tw_prospective_registry"]["status"]=="unavailable"
    assert path.read_bytes()==before


def test_actual_runtime_adapter_can_observe_later_close_without_moving_levels(tmp_path, monkeypatch):
    import tw_price_action_coverage
    reports, report=prepared(tmp_path)
    update_tw_prospective_report(reports,report,now=REGISTERED,evaluation_context=context(official()))
    frozen=deepcopy(report['plans'][0]['prospective_entries'][0]['frozen_levels'])
    risk_batch(reports,report)
    identity={'generated_at':NEXT_CLOSE.isoformat(),'summary':{'unmatched':0},'events':[],
              'source_health':{'twse_registry':{'ok':True},'twse_announcements':{'ok':True}}}
    (reports/'corporate_actions_shadow.json').write_text(json.dumps(identity))
    calls=[]
    def action_fetch(requests,**kw):
        calls.append(requests)
        return {r['plan_id']:risk()['corporate_actions'] for r in requests}
    monkeypatch.setattr(tw_price_action_coverage,'fetch_tw_price_action_coverage_for_plans',action_fetch)
    fetch=lambda symbols:({'2330.TW':official('2026-10-12',103,NEXT_CLOSE)},[])
    state=update_tw_prospective_report(reports,report,now=NEXT_CLOSE,fetch_records=fetch)
    assert len(calls)==1 and calls[0][0]['original_session']=='2026-10-08'
    assert state['summary']['triggered_close_only']==1
    assert state['records'][0]['frozen']['levels']==frozen


def test_initial_enrollment_does_not_fetch_future_corporate_actions(tmp_path,monkeypatch):
    import tw_price_action_coverage
    reports,report=prepared(tmp_path)
    def forbidden(*a,**kw):raise AssertionError('no later observation exists')
    monkeypatch.setattr(tw_price_action_coverage,'fetch_tw_price_action_coverage_for_plans',forbidden)
    update_tw_prospective_report(reports,report,now=REGISTERED,fetch_records=lambda symbols:({'2330.TW':official()},[]))
    assert report['tw_prospective_registry']['summary']['status_counts']['enrolled_pending']==1


def test_known_price_action_block_not_downgraded_by_missing_identity_report(tmp_path):
    reports,report=prepared(tmp_path);risk_batch(reports,report)
    records={"2330.TW":official("2026-10-12",103,NEXT_CLOSE)}
    known={**risk()["corporate_actions"],"status":"blocked"}
    result=build_current_risk_snapshots(reports,report,records,now=NEXT_CLOSE,corporate_actions={"2330.TW":known})
    assert result["2330.TW"]["corporate_actions"]["status"]=="blocked"
