from copy import deepcopy
import json
from hashlib import sha256
import pytest
from gap_entry_shadow import update, empty_state


def rows(date='2026-10-02', next_date='2026-10-05', openings=None, closes=None):
    result=[]
    for i in range(10):
        proof={'status':'verified','date':date,'open':(openings or {}).get(i,100),
            'close':(closes or {}).get(i,100),'source':'TWSE OpenAPI',
            'calendar_status':{'available':True,'is_session':True},'session_complete':True,
            'next_session_date':next_date,'captured_at':date+' 20:00:00'}
        proof['sha256']=sha256(json.dumps(proof,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        result.append({'symbol':f'S{i}','shadow_price_evidence':proof})
    return result


def source():
    return {'pending':{'signal_session_date':'2026-10-02','snapshot_id':'original-frozen',
        'created_at':'2026-10-02 20:00:00','picks':[{'symbol':f'S{i}','rank':i+1,
        'ranking_score':90-i,'allocation_twd':100000} for i in range(10)]}}


def frozen():
    return update(None,source(),rows(),'2026-10-04 20:00:00')


def test_prospective_freeze_no_input_mutation_or_historical_backfill():
    model=source();bars=rows();before=deepcopy((model,bars))
    state=update(None,model,bars,'2026-10-04 20:00:00')
    assert (model,bars)==before
    assert state['completed_sessions']==0 and state['summary']['baseline_net_profit_twd'] is None
    assert state['pending']['execution_session_date']=='2026-10-05'
    assert state['pending']['picks'][0]['ranking_score']==90
    assert update(None,model,bars,'2026-10-05 09:00:00')['pending'] is None


def test_same_prices_same_cost_rule_cash_not_reallocated_and_no_duplicate_day():
    state=frozen();receipt=deepcopy(state['pending'])
    bars=rows('2026-10-05','2026-10-06',openings={0:103,1:102},closes={0:102,1:103})
    result=update(state,source(),bars,'2026-10-05 20:00:00')
    day=result['days'][0];a,b=day['positions'][:2]
    assert a['baseline_executed'] and not a['gap_cash_executed']
    assert a['gap_cash_cost_twd']==0 and a['gap_cash_net_profit_twd']==0
    assert b['gap_pct']==2 and b['gap_cash_executed']
    assert b['gap_cash_cost_twd']==b['baseline_cost_twd']==685
    assert day['gap_cash_twd']==100000 and day['baseline_cash_twd']==0
    assert day['frozen_receipt']==receipt
    assert update(result,source(),bars,'2026-10-05 20:30:00')['days']==result['days']
    assert state['days']==[]


def test_missing_exact_date_evidence_never_rolls_and_failure_is_retained_once():
    state=frozen()
    bad=rows('2026-10-06','2026-10-07')
    result=update(state,source(),bad,'2026-10-06 20:00:00')
    assert result['days']==[] and len(result['invalid_days'])==1
    assert result['invalid_days'][0]['pending']==state['pending']
    again=update(result,source(),bad,'2026-10-06 20:30:00')
    assert again['invalid_days']==result['invalid_days']


def test_nine_official_prices_allow_comparison_but_eight_do_not():
    state=frozen();bars=rows('2026-10-05','2026-10-06')
    result=update(state,source(),bars[:9],'2026-10-05 20:00:00')
    day=result['days'][0]
    assert day['baseline_cash_twd']==day['gap_cash_twd']==100000
    assert day['positions'][9]['cash_reason']=='official_price_missing'
    assert update(state,source(),bars[:8],'2026-10-05 20:00:00')['days']==[]


def test_filtered_group_can_hold_all_cash_without_relaxing_data_minimum():
    bars=rows('2026-10-05','2026-10-06',openings={i:103 for i in range(10)})
    day=update(frozen(),source(),bars,'2026-10-05 20:00:00')['days'][0]
    assert day['gap_cash_twd']==1000000 and day['gap_cash_net_profit_twd']==0
    assert day['available_prices']==10


@pytest.mark.parametrize('mutation', ['created_missing','created_after_open','price_missing','source_tampered'])
def test_freeze_fails_closed(mutation):
    model=source();bars=rows()
    if mutation=='created_missing':model['pending'].pop('created_at')
    elif mutation=='created_after_open':model['pending']['created_at']='2026-10-05 10:00:00'
    elif mutation=='price_missing':bars.pop()
    else:bars[0]['shadow_price_evidence']['close']=999
    assert update(None,model,bars,'2026-10-04 20:00:00')['pending'] is None


def test_corrupted_receipt_stops_and_intraday_never_settles():
    state=frozen();state['pending']['picks'][0]['signal_close']=999
    assert update(state,source(),rows(),'2026-10-05 20:00:00')['status']=='receipt_mismatch_manual_review'
    assert update(frozen(),source(),rows('2026-10-05'),'2026-10-05 12:00:00',intraday=True)['days']==[]


def test_twenty_and_sixty_session_reviews_never_promote_or_change_policy():
    state=empty_state('2026-10-04 20:00:00')
    for n,status in [(20,'preliminary_review_only'),(60,'manual_review_only')]:
        state['days']=[{'baseline_net_profit_twd':-100,'gap_cash_net_profit_twd':0,
            'incremental_net_profit_twd':100,'skipped_gap_positions':1} for _ in range(n)]
        result=update(state,source(),rows(),'2026-10-04 20:00:00',intraday=True)
        assert result['status']==status and result['promotion_allowed'] is False
    state['policy']['gap_cutoff_pct']=3
    assert update(state,source(),rows(),'2026-10-04 20:00:00')['status']=='policy_mismatch_manual_review'


def test_existing_weight_producer_connects_freeze_settlement_and_repeat_safely(tmp_path, monkeypatch):
    import weight_price_evidence
    from weight_experiment import update_weight_experiment
    def prepared(date, next_date, openings=None, closes=None):
        result=rows(date,next_date,openings,closes)
        for i,r in enumerate(result):
            r.update({'market':'TW','type':'個股','name':r['symbol'],
                'official_session_date':date,'tw_official_session_date':date,
                'tw_official_price_available':True,'shadow_price_contract_required':True,
                'short_term_rank_tier':1,'short_term_score':90-i,
                'short_term_base_ranking_score':90-i})
        return result
    signal=prepared('2026-10-02','2026-10-05')
    monkeypatch.setattr(weight_price_evidence,'prepare_from_cache',lambda reports,rows,prices,at:deepcopy(rows))
    path=update_weight_experiment(tmp_path,signal,period='evening',updated_at='2026-10-04 20:00:00')
    state=json.loads(path.read_text());assert state['entry_gap_shadow']['pending']
    assert state['entry_gap_shadow']['completed_sessions']==0
    execution=prepared('2026-10-05','2026-10-06',openings={0:103},closes={0:102})
    update_weight_experiment(tmp_path,execution,period='evening',updated_at='2026-10-05 20:00:00')
    state=json.loads(path.read_text());before=deepcopy(state['entry_gap_shadow']['days'])
    assert len(before)==1 and before[0]['skipped_gap_positions']==1
    update_weight_experiment(tmp_path,execution,period='evening',updated_at='2026-10-05 20:15:00')
    assert json.loads(path.read_text())['entry_gap_shadow']['days']==before
