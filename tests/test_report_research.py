from copy import deepcopy
import json
from pathlib import Path

import pytest
from tests.test_stock_prediction_evidence import reports, write
from report_research import integrate_research, integrate_research_safely, finalize_research_markdown


def payload(manifest):
    return {'updated_at': manifest['updated_at'], 'analyzed_count': 3, 'candidate_count': 4,
            'pending_candidates': [{'market': 'US', 'symbol': 'PENDING'}],
            'data': [{'symbol': s, 'market': 'US', 'name': s, 'score': 55, 'overall_rank': n,
                      'official_session_date': '2026-10-16', 'outlook_change_condition': 'existing rule'}
                     for n, s in enumerate(['IREN', 'MSFT', 'OUTSIDE-WATCHLIST'])]}


def test_full_pool_signed_directions_and_separate_limits(reports):
    folder, manifest = reports
    source = payload(manifest); original = deepcopy(source)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    result = integrate_research(source, folder, engine_succeeded=True)
    assert source == original and before == {p.name:p.read_bytes() for p in folder.iterdir()}
    assert [r['symbol'] for r in result['data']] == ['IREN','MSFT','OUTSIDE-WATCHLIST']
    meta = result['research_integration']
    assert meta['identity_accounting_complete'] and meta['identity_rows_accounted'] == 3
    assert meta['forecast_count'] == 2 and meta['missing_horizon_count'] == 28
    for old, row in zip(original['data'], result['data']):
        assert {k:v for k,v in row.items() if k != 'research'} == old
        assert len(row['research']['horizons']) == 10
        assert row['research']['execution']['decision_eligible'] is False
    research = result['data'][0]['research']; f = research['horizons'][0]
    assert f['direction'] == 'DOWN' and f['expected_return_pct'] == -.35 and f['event_target'] == 'UP'
    assert f['supporting_evidence'][0]['label'] == '趨勢'
    assert f['opposing_evidence'][0]['label'] == '新聞'
    assert f['trigger'] is None and f['invalidation'] is None
    assert f['review_sample_count'] == 0 and f['outcome']['status'] == 'not_published_or_pending'
    assert research['existing_conditions']['outlook_change_condition'] == 'existing rule'
    assert integrate_research(source,folder,engine_succeeded=True) == result


@pytest.mark.parametrize('failure',['engine','batch','chunk'])
def test_failed_or_prior_batch_is_not_presented_as_current(reports, failure):
    folder, manifest = reports; source = payload(manifest)
    if failure == 'batch': source['updated_at'] = '2026-10-19 06:30:12'
    if failure == 'chunk':
        (folder/manifest['data_files']['US_STOCK']['NEXT_1D']['parts'][1]).unlink()
    r = integrate_research(source,folder,engine_succeeded=failure!='engine')
    assert r['research_integration']['forecast_count'] == 0
    assert all(h['status']=='missing_or_invalid' for row in r['data'] for h in row['research']['horizons'])
    assert r['research_integration']['identity_rows_accounted'] == 3


@pytest.mark.parametrize('failure',['duplicate','count','size'])
def test_failure_preserves_core_and_removes_carried_research(reports, monkeypatch, failure):
    import report_research
    folder, manifest = reports; source = payload(manifest)
    source['data'][0]['research']={'status':'old_success'}
    if failure=='duplicate':source['data'][1]=deepcopy(source['data'][0])
    if failure=='count':source['analyzed_count']=99
    if failure=='size':monkeypatch.setattr(report_research,'MAX_PROJECTION_BYTES',1)
    r=integrate_research_safely(source,folder,engine_succeeded=True)
    assert r['research_integration']['status']=='integration_failed'
    assert len(r['data'])==len(source['data'])
    assert all('research' not in row for row in r['data'])


def test_markdown_is_same_batch_atomic_and_never_sends(reports, monkeypatch):
    import notifier
    folder, manifest=reports; p=integrate_research(payload(manifest),folder,engine_succeeded=True)
    report={'updated_at':p['updated_at'],'watchlist':[{'market':'US','symbol':'IREN'}]}
    latest=folder/'latest.md'; latest.write_text('original')
    seen=[]
    def render(r):
        seen.append(r)
        return '\n'.join(notifier._research_lines(r['watchlist'][0]['research']))
    monkeypatch.setattr(notifier,'render_markdown',render)
    monkeypatch.setattr(notifier,'send_telegram',lambda *_:pytest.fail('no delivery'))
    text=finalize_research_markdown(report,p,latest,'original')
    assert 'DOWN -0.35%' in text and '2026-10-16 凍結' in text
    assert '支持方向：趨勢' in text and '反對方向：新聞' in text
    assert latest.read_text()==text and 'research' not in report['watchlist'][0]
    latest.write_text('original')
    monkeypatch.setattr(notifier,'render_markdown',lambda *_:(_ for _ in ()).throw(RuntimeError()))
    assert finalize_research_markdown(report,p,latest,'original')=='original'
    assert latest.read_text()=='original'
    p['updated_at']='different'
    assert finalize_research_markdown(report,p,latest,'original')=='original'


def test_pipeline_order_preserves_early_report_fallback():
    source=Path('briefing.py').read_text()
    start=source.index('def main('); source=source[start:]
    assert source.index('latest_json, latest_md = save_report(')<source.index('prediction_engine_succeeded =')
    assert source.index('prediction_engine_succeeded =')<source.index('all_analysis_payload = integrate_research_safely(')
    assert source.index('all_analysis_tmp.replace(all_analysis_path)')<source.index('markdown = finalize_research_markdown(')


def test_atomic_write_failure_keeps_saved_report(reports, monkeypatch):
    import notifier
    folder, manifest=reports; p=integrate_research(payload(manifest),folder,engine_succeeded=True)
    report={'updated_at':p['updated_at'],'watchlist':[]};latest=folder/'latest.md';latest.write_text('original')
    monkeypatch.setattr(notifier,'render_markdown',lambda *_:'new')
    original=Path.write_text
    def fail(path,*args,**kwargs):
        if path.name.endswith('.research.tmp'):raise OSError('write failed')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail)
    assert finalize_research_markdown(report,p,latest,'original')=='original'
    assert latest.read_text()=='original'


def test_comparable_model_disagreement_and_matched_outcome_survive(reports):
    from tests.test_stock_prediction_evidence import frozen
    folder, manifest=reports
    for code, expected, side in [('UP_21D',2,'UP'),('DOWN_21D',-2,'DOWN')]:
        filename=f'prediction_engine_data_US_STOCK_{code}.json'
        manifest['data_files']['US_STOCK'][code]=filename
        r=frozen(code=code);r.update(expected_return_pct=expected,target_side=side)
        old=frozen(code=code,day='2026-09-01');old.update(expected_return_pct=expected,target_side=side,status='matured',realized_return_pct=1,outcome_session_date='2026-10-01')
        r['recent_outcomes']=[old]
        write(folder/filename,{'group':'US_STOCK','horizon_code':code,'updated_at':manifest['updated_at'],'contract_version':'test','predictions':[r]})
    write(folder/'prediction_engine.json',manifest)
    result=integrate_research(payload(manifest),folder,engine_succeeded=True)['data'][0]['research']
    conflict=result['model_disagreement']['same_session_horizon_conflicts'][0]
    assert conflict['horizon_sessions']==21 and len(conflict['models'])==2
    f=next(f for f in result['horizons'] if f['horizon_code']=='DOWN_21D')
    assert f['review_sample_count']==1
    assert f['recent_outcomes'][0]['outcome']['return_error_pp']==3
    assert f['recent_outcomes'][0]['outcome']['expected_direction_correct'] is False
