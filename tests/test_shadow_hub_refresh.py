import copy
import json
from pathlib import Path
import pytest
from decision_hub import refresh_shadow_inputs


def setup_batch(tmp_path):
    row = {'symbol':'2330','market':'TW','official_session_date':'2026-10-08','price':100,'rsi':50}
    decision = {'symbol':'2330','market':'TW','session_date':'2026-10-08','formal_rank':1,'formal_score':88,'horizons':{'short':{'score':90}}}
    blobs = {'all_analysis.json':{'updated_at':'2026-10-09 12:00:00','data':[row]}, 'decision_hub.json':{'updated_at':'2026-10-09 12:00:00','decision_files':['decision_hub_01.json']},'decision_hub_01.json':{'updated_at':'2026-10-09 12:00:00','decisions':[decision]}}
    for name,data in blobs.items(): (tmp_path/name).write_text(json.dumps(data))
    return blobs


def test_refresh_preserves_frozen_formal_fields_and_source(tmp_path):
    blobs=setup_batch(tmp_path)
    original=(tmp_path/'all_analysis.json').read_bytes()
    index=(tmp_path/'decision_hub.json').read_bytes()
    assert refresh_shadow_inputs(tmp_path)==1
    row=json.loads((tmp_path/'decision_hub_01.json').read_text())['decisions'][0]
    for key,value in blobs['decision_hub_01.json']['decisions'][0].items(): assert row[key]==value
    assert row['input_evidence_categories']
    assert set(row)-set(blobs['decision_hub_01.json']['decisions'][0])=={'source_snapshot','input_evidence_categories','shadow_events'}
    assert (tmp_path/'all_analysis.json').read_bytes()==original
    assert (tmp_path/'decision_hub.json').read_bytes()==index
    once=(tmp_path/'decision_hub_01.json').read_bytes()
    refresh_shadow_inputs(tmp_path)
    assert (tmp_path/'decision_hub_01.json').read_bytes()==once


@pytest.mark.parametrize('mutation', ['timestamp','duplicate','session','manifest','universe'])
def test_bad_batch_never_partially_written(tmp_path,mutation):
    blobs=setup_batch(tmp_path)
    if mutation=='timestamp': blobs['all_analysis.json']['updated_at']='other'
    elif mutation=='duplicate': blobs['all_analysis.json']['data']*=2
    elif mutation=='session': blobs['all_analysis.json']['data'][0]['official_session_date']='2026-10-07'
    elif mutation=='manifest': blobs['decision_hub.json']['decision_files']=['../bad.json']
    elif mutation=='universe': blobs['all_analysis.json']['data'].append({'symbol':'AAPL','market':'US'})
    for name,data in blobs.items(): (tmp_path/name).write_text(json.dumps(data))
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError): refresh_shadow_inputs(tmp_path)
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before


def test_workflow_refreshes_before_plan_and_publishes_chunks():
    workflow=Path('.github/workflows/trade-plan-shadow.yml').read_text()
    assert workflow.index('--refresh-shadow-inputs-only')<workflow.index('python trade_plan_shadow.py')
    assert 'git add reports/trade_plan_shadow.json reports/trade_plan_shadow_health.json reports/trade_plan_validation.json reports/decision_hub_[0-9][0-9].json' in workflow


def test_current_published_batch_refresh(tmp_path):
    import shutil
    root=Path('reports')
    if not (root/'all_analysis.json').exists():
        pytest.skip('reconstructed local snapshot omits published reports; required in full CI')
    index=json.loads((root/'decision_hub.json').read_text())
    for name in ['all_analysis.json','decision_hub.json']+index['decision_files']:
        shutil.copyfile(root/name,tmp_path/name)
    frozen=(tmp_path/'all_analysis.json').read_bytes()
    assert refresh_shadow_inputs(tmp_path)>0
    assert (tmp_path/'all_analysis.json').read_bytes()==frozen
