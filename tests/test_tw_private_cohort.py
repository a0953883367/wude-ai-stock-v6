"""Synthetic offline fixtures, never represented as live provider evidence."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import pytest
import tw_private_cohort as cohort
from tw_daily_shadow_attestation import parse_official_price_rows

NOW = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)
STAMP = '2026-10-10T08:00:00+00:00'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sessions():
    # Deliberately synthetic fixture calendar; not evidence of exchange sessions.
    first = datetime(2026, 6, 22)
    return [(first+timedelta(days=i)).date().isoformat() for i in range(109) if (first+timedelta(days=i)).weekday()<5]


def fixture():
    dates = sessions()
    assert 60 <= len(dates) <= 120
    items=[{'symbol':'2330.TW','market':'TW','venue':'TWSE','type':'個股'},
           {'symbol':'00886.TWO','market':'TW','venue':'TPEX_MAINBOARD','type':'ETF'},
           {'symbol':'7415.TWO','market':'TW','venue':'TPEX_EMERGING','type':'個股'}]
    formal={'updated_at':'2026-10-10 16:00:00','data':[
        {**x,'official_session_date':dates[-1],'price':9999,'score':88,'rank':i+1,
         'short_term_entry_low':8000,'intraday_available':True,'attack_volume':77}
        for i,x in enumerate(items)]}
    histories={}
    official={}
    for item in items[:2]:
        bars=[]
        for i,day in enumerate(dates):
            close=100+i%7+(i//7)*.5
            bars.append({'date':day,'open':close-.5,'high':close+1,'low':close-1,'close':close,'volume':10000+i*100})
        histories[item['symbol']]={'status':'validated_in_memory','symbol':item['symbol'],'market':'TW',
            'venue':item['venue'],'source':cohort.SOURCE,'source_session_date':dates[-1],
            'requested_at':'2026-10-10T07:59:00+00:00','observed_at':STAMP,
            'price_basis':'raw_unadjusted','volume_unit':'shares',
            'adjustment_evidence':'explicit_request_and_provider_contract',
            'request':{'timeframe':'D','adjusted':'false','from':dates[0],'to':dates[-1]},
            'bars':bars,'response_sha256':digest(bars)}
        last=bars[-1]; rawdate=str(int(last['date'][:4])-1911)+last['date'][5:7]+last['date'][8:10]
        if item['venue']=='TWSE':
            source='TWSE OpenAPI';raw={'Code':'2330','Date':rawdate,
                **dict(zip(['OpeningPrice','HighestPrice','LowestPrice','ClosingPrice','TradeVolume'],[last[x] for x in cohort.BAR_FIELDS]))}
        else:
            source='TPEx OpenAPI';raw={'SecuritiesCompanyCode':'00886','Date':rawdate,
                **dict(zip(['Open','High','Low','Close','TradingShares'],[last[x] for x in cohort.BAR_FIELDS]))}
        official[item['symbol']]=parse_official_price_rows(source,[raw],fetched_at=STAMP)[item['symbol']]
    calendar={'available':True,'status':'verified','source_statuses':['verified_twse_tpex'],'sessions':dates}
    return formal,items,histories,calendar,official


def run(parts=None, **kwargs):
    formal,manifest,histories,calendar,official=parts or fixture()
    return cohort.build_private_cohort(formal,manifest,histories,calendar_evidence=calendar,
        official_records=official,now=NOW,**kwargs)


def coverage(symbol='2330.TW', events=None):
    dates=sessions()
    return {'status':'verified_complete','symbol':symbol,'coverage_from':dates[0],
        'through_session':dates[-1],'observed_at':STAMP,'source':'Fubon Neo corporate actions',
        'coverage':[{'method':method,'status':'verified_complete','coverage_from':dates[0],
                     'through_session':dates[-1],'observed_at':STAMP,'response_sha256':'a'*64}
                    for method in ['corporate_actions.dividends','corporate_actions.capital_changes']],
        'events':events or []}


def event(**changes):
    return {'symbol':'2330.TW','effective_date':sessions()[20],
        'source_method':'corporate_actions.dividends','classification_status':'verified',
        'classification_version':'TW-RAW-ACTION-TYPES-V1','raw_record_sha256':'b'*64,
        'kind':'cash_dividend','dividend_type':'息','cash_dividend':2.5,'stock_dividend_shares':0,**changes}


def action(data):
    return cohort.action_policy(data,symbol='2330.TW',first=sessions()[0],last=sessions()[-1],now=NOW)


def test_rebuilds_prices_from_actual_bars_not_old_quote_or_plan():
    parts=fixture();before=copy.deepcopy(parts)
    result=run(parts)
    assert parts==before
    assert result['formal_sha256_before']==result['formal_sha256_after']==digest(parts[0])
    assert result['network_calls']==0 and result['scoring_executed'] is False
    assert result['all_supported_histories_validated'] is True
    row=next(x for x in result['rows'] if x['symbol']=='2330.TW')
    bars=parts[2]['2330.TW']['bars']; features=row['features']
    assert features['price']==bars[-1]['close']!=9999
    assert features['ma60']==sum(x['close'] for x in bars[-60:])/60
    assert features['attack_volume'] is None and features['volume_pace'] is None
    assert features['intraday_available'] is False
    assert 'score' not in features and 'short_term_entry_low' not in features
    assert row['plans'] is None and row['decision_eligible'] is False
    assert 'official_crosscheck_mismatch' not in row['reasons']
    etf=next(x for x in result['rows'] if x['symbol']=='00886.TWO')
    assert etf['asset_type']=='ETF'
    esb=next(x for x in result['rows'] if x['symbol']=='7415.TWO')
    assert esb['features'] is None and esb['reasons']==['unsupported_venue']


@pytest.mark.parametrize('change,reason',[
    ({'source':'Yahoo'},'history_identity'),({'venue':'TPEX_MAINBOARD'},'history_identity'),
    ({'market':'US'},'history_identity'),({'symbol':'6290.TWO'},'history_identity'),
    ({'price_basis':'adjusted'},'history_provenance'),({'volume_unit':'lots'},'history_provenance'),
    ({'response_sha256':'0'*64},'history_digest'),({'observed_at':'2030-01-01T00:00:00Z'},'history_provenance'),
    ({'requested_at':'2030-01-01T00:00:00Z'},'history_provenance'),
    ({'observed_at':'2026-10-10T08:00:00'},'history_provenance'),
])
def test_history_provenance_fail_closed(change,reason):
    parts=fixture();parts[2]['2330.TW'].update(change)
    row=next(x for x in run(parts)['rows'] if x['symbol']=='2330.TW')
    assert row['features'] is None and row['reasons']==[reason]


@pytest.mark.parametrize('mutation',['missing','duplicate','wrong_date','nan','negative_volume','fraction_volume','bool','impossible','extra_field'])
def test_frame_corruption_never_recomputes(mutation):
    parts=fixture();h=parts[2]['2330.TW'];bars=h['bars']
    if mutation=='missing': bars.pop(4)
    elif mutation=='duplicate': bars[4]=copy.deepcopy(bars[3])
    elif mutation=='wrong_date': bars[4]['date']='2026-01-01'
    elif mutation=='nan': bars[4]['close']=float('nan')
    elif mutation=='negative_volume': bars[4]['volume']=-1
    elif mutation=='fraction_volume': bars[4]['volume']=1.1
    elif mutation=='bool': bars[4]['volume']=True
    elif mutation=='impossible': bars[4]['high']=1
    else: bars[4]['vendor_change']=1
    row=next(x for x in run(parts)['rows'] if x['symbol']=='2330.TW')
    assert row['features'] is None and row['decision_eligible'] is False


def test_partial_cohort_not_reinterpreted_as_whole_universe():
    parts=fixture();parts[2].pop('00886.TWO')
    result=run(parts)
    assert not result['all_supported_histories_validated']
    assert len(result['rows'])==3 and not result['scoring_executed']
    assert cohort.safe_status(result)['daily_features_rebuilt_count']==1


@pytest.mark.parametrize('what',['omit','duplicate','extra','taxonomy','future_batch','session','calendar','future_bar'])
def test_manifest_calendar_and_batch_bindings(what):
    parts=fixture()
    if what=='omit': parts[1].pop()
    elif what=='duplicate': parts[1][1]=copy.deepcopy(parts[1][0])
    elif what=='extra': parts[2]['AAPL']={}
    elif what=='taxonomy': parts[1][1]['type']='個股'
    elif what=='future_batch': parts[0]['updated_at']='2030-01-01 12:00:00'
    elif what=='session': parts[0]['data'][0]['official_session_date']='2026-01-01'
    elif what=='calendar': parts[3]['source_statuses']=['verified_conservative_union']
    else: parts[3]['sessions'][-1]='2030-01-01'
    with pytest.raises(cohort.InvalidCohort): run(parts)


def test_official_crosscheck_uses_raw_record_and_retains_mismatch():
    parts=fixture();parts[4]['2330.TW']['raw_record']['ClosingPrice']=12345
    row=next(x for x in run(parts)['rows'] if x['symbol']=='2330.TW')
    assert row['features'] is not None
    assert 'official_crosscheck_unavailable' in row['reasons']
    assert row['decision_eligible'] is False
    parts=fixture();record=parts[4]['2330.TW'];raw=record['raw_record'];raw['TradeVolume']+=1
    parts[4]['2330.TW']=parse_official_price_rows('TWSE OpenAPI',[raw],fetched_at=STAMP)['2330.TW']
    row=next(x for x in run(parts)['rows'] if x['symbol']=='2330.TW')
    assert 'official_crosscheck_mismatch' in row['reasons']


def test_no_events_complete_coverage_and_cash_only_warning():
    assert action(coverage())['status']=='research_basis_checked'
    result=action(coverage(events=[event()]))
    assert result['status']=='research_basis_checked'
    assert result['warnings']==['cash_dividend_raw_price_gap_not_total_return']


@pytest.mark.parametrize('changes',[
    {'dividend_type':'權'}, {'dividend_type':'權息'}, {'dividend_type':None},
    {'cash_dividend':None}, {'cash_dividend':0}, {'cash_dividend':-1}, {'cash_dividend':float('nan')},
    {'stock_dividend_shares':None}, {'stock_dividend_shares':100},
    {'rights_subscription_ratio':None}, {'rights_subscription_shares':50},
])
def test_ambiguous_cash_events_quarantine(changes):
    assert action(coverage(events=[event(**changes)]))['reason']=='ambiguous_action'


@pytest.mark.parametrize('kind',sorted(cohort.STRUCTURAL_EVENTS))
def test_structural_actions_quarantine(kind):
    assert action(coverage(events=[event(kind=kind)]))['reason']=='structural_action'


def test_same_day_cash_and_reduction_never_waives_structural_coverage():
    cash=event();reduction=event(kind='capital_reduction',source_method='corporate_actions.capital_changes',raw_record_sha256='c'*64)
    assert action(coverage(events=[cash,reduction]))['reason']=='structural_action'
    data=coverage(events=[cash]);data['coverage'].pop()
    assert action(data)['reason']=='action_coverage_invalid'


def test_entire_frame_warmup_and_boundary_events_are_kept():
    dates=sessions()
    assert len(dates)>60
    assert action(coverage(events=[event(kind='split',effective_date=dates[1])]))['reason']=='structural_action'
    boundary=(datetime.fromisoformat(dates[0])+timedelta(days=59)).date().isoformat()
    e=event(effective_date=boundary)
    result=action(coverage(events=[e,copy.deepcopy(e)]))
    assert result['warnings']==['cash_dividend_raw_price_gap_not_total_return']
    first=action(coverage(events=[event(kind='split',effective_date=dates[0])]))
    assert first['status']=='research_basis_checked' and first['first_bar_events']==1


def test_unknown_incomplete_or_future_action_proof_never_clear():
    assert action(None)['reason']=='action_coverage_unknown'
    for change in [{'status':'unknown'},{'coverage_from':sessions()[1]},
                   {'through_session':sessions()[-2]},{'observed_at':'2030-01-01T00:00:00Z'},
                   {'events':[event(kind='unknown')]},{'events':[event(raw_record_sha256='wrong')]}]:
        data=coverage();data.update(change)
        assert action(data)['status']=='held'


def nonprice():
    return {'symbol':'2330.TW','market':'TW','source_batch':'2026-10-10 16:00:00',
            'observed_at':STAMP,'groups':{
                'financial':{'available':True,'source':'existing financial snapshot',
                  'as_of':'2026-08-15T00:00:00Z','observed_at':STAMP,'values':{'eps':7.2}},
                'news':{'available':True,'source':'existing verified news',
                  'as_of':STAMP,'observed_at':STAMP,'expires_at':'2026-10-10T20:00:00Z','values':{'news_penalty':2}},
            }}


def test_nonprice_fields_require_provenance_never_copied_from_old_model():
    data=nonprice();result=run(nonprice={'2330.TW':data})
    row=next(x for x in result['rows'] if x['symbol']=='2330.TW')
    assert row['nonprice']['financial']['values']=={'eps':7.2}
    assert row['nonprice']['fundamental'] is None
    assert row['nonprice']['institution'] is None
    assert row['nonprice']['news']['values']['news_penalty']==2
    assert 'score' not in row['nonprice']
    assert row['plans'] is None


@pytest.mark.parametrize('change',['future','expired','extra_score','nan','bool','no_source','batch','market'])
def test_invalid_nonprice_not_synthesized(change):
    data=nonprice()
    if change=='future': data['observed_at']='2030-01-01T00:00:00Z'
    elif change=='expired': data['groups']['news']['expires_at']=STAMP
    elif change=='extra_score': data['groups']['news']['values']['score']=99
    elif change=='nan': data['groups']['news']['values']['news_penalty']=float('nan')
    elif change=='bool': data['groups']['news']['values']['news_penalty']=False
    elif change=='no_source': data['groups']['news']['source']=''
    elif change=='batch': data['source_batch']='other'
    else: data['market']='US'
    row=next(x for x in run(nonprice={'2330.TW':data})['rows'] if x['symbol']=='2330.TW')
    assert row['nonprice'].get('news') is None
    assert any(reason.startswith('nonprice_') for reason in row['reasons'])


def test_sanitized_projection_cannot_export_values_even_if_private_contains_taint():
    private=run()
    private.update(raw_bars='SECRET',token='SECRET',price=123.456,anything='SECRET')
    private['rows'][0].update(secret='SECRET',close=123.456)
    safe=cohort.safe_status(private)
    serialized=json.dumps(safe)
    assert 'SECRET' not in serialized and '123.456' not in serialized
    assert 'rows' not in safe and 'price' not in safe and 'symbol' not in safe
    assert safe['market_values_exported'] is False and safe['decision_eligible'] is False
    assert safe['formal_v6_unchanged'] is True
    assert cohort.safe_status({})['formal_v6_unchanged'] is False


def test_no_provider_change_or_cash_amount_is_added_to_raw_returns():
    bars=fixture()[2]['2330.TW']['bars']
    bars[-2].update(close=100,open=100,high=101,low=99)
    bars[-1].update(close=94,open=94,high=95,low=93)
    features=cohort.daily_features(bars)
    assert features['change_pct']==pytest.approx(-6)
    assert 'total_return' not in features


@pytest.mark.parametrize('mode,rsi',[('up',100),('down',0),('flat',50)])
def test_rsi_zero_loss_cases_are_defined_not_missing_input_defaults(mode,rsi):
    bars=fixture()[2]['2330.TW']['bars']
    for i,bar in enumerate(bars):
        value=100+i if mode=='up' else 200-i if mode=='down' else 100
        bar.update(open=value,high=value+1,low=value-1,close=value)
    assert cohort.daily_features(bars)['rsi']==rsi


def test_unknown_or_contradictory_first_bar_is_not_excused_by_boundary():
    assert action(coverage(events=[event(kind='unknown',effective_date=sessions()[0])]))['reason']=='ambiguous_action'
    assert action(coverage(events=[event(dividend_type='權',effective_date=sessions()[0])]))['reason']=='ambiguous_action'
    e=event();conflict={**e,'kind':'split'}
    assert action(coverage(events=[e,conflict]))['reason']=='ambiguous_action'


def test_nonprice_cannot_claim_asof_before_it_was_observed():
    data=nonprice();data['groups']['news']['observed_at']='2026-10-10T07:00:00Z'
    row=next(x for x in run(nonprice={'2330.TW':data})['rows'] if x['symbol']=='2330.TW')
    assert row['nonprice']['news'] is None


@pytest.mark.parametrize("request_value", [None, [], "D", 1])
def test_malformed_history_request_is_held(request_value):
    parts=fixture();parts[2]['2330.TW']['request']=request_value
    row=next(x for x in run(parts)['rows'] if x['symbol']=='2330.TW')
    assert row['reasons']==['history_provenance']

def test_history_cannot_be_observed_before_last_bar_settles():
    parts=fixture(); history=parts[2]['2330.TW']
    history['requested_at']=sessions()[-1]+'T07:00:00+00:00'
    history['observed_at']=sessions()[-1]+'T08:29:59+00:00'
    row=next(x for x in run(parts)['rows'] if x['symbol']=='2330.TW')
    assert row['reasons']==['history_provenance']


def test_manifest_cannot_override_explicit_frozen_venue():
    parts=fixture();parts[0]['data'][1]['venue']='TPEX_EMERGING'
    with pytest.raises(cohort.InvalidCohort,match='manifest_identity'):
        run(parts)


def test_formal_batch_cannot_predate_its_final_session():
    parts=fixture();parts[0]['updated_at']='2026-01-01 16:00:00'
    with pytest.raises(cohort.InvalidCohort,match='formal_batch_timestamp'):
        run(parts)

def test_nonprice_metadata_drops_old_scores_and_raw_bars():
    data=nonprice();data['groups']['financial'].update(old_score=99,raw_bars=[{'close':999}],expires_at={'raw_bars':[1]})
    row=next(x for x in run(nonprice={'2330.TW':data})['rows'] if x['symbol']=='2330.TW')
    assert 'old_score' not in row['nonprice']['financial']
    assert 'raw_bars' not in row['nonprice']['financial']
    assert 'expires_at' not in row['nonprice']['financial']
