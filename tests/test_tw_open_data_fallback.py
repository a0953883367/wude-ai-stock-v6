from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tw_open_data_fallback import parse_current_csv, supplement_twse_prices, completed_tw_session, SOURCE
from tw_official_data import overlay_official_daily
from daily_price_provenance import tw_official_daily_metadata

NOW = datetime(2026, 10, 14, 8, tzinfo=ZoneInfo('Asia/Taipei'))
HEADER = '日期,證券代號,開盤價,最高價,最低價,收盤價,成交股數\n'
CSV = HEADER + '1151013,0050,112.55,113.20,111.90,112.40,242070746\n'

class Calendar:
    def lookup(self, *args):
        return {'available': True, 'status': 'verified', 'sessions': ['2026-10-13', '2026-10-14']}
    def session_status(self, market, day):
        return {"available":True,"is_session":True}
    def session_complete(self, market, day, *, at_epoch):
        return day == '2026-10-13'


def run(prices=None, text=CSV, calendar=None):
    calls = []
    def fetch():
        calls.append(1)
        if isinstance(text, Exception):
            raise text
        return text
    result, status = supplement_twse_prices(prices or {}, {'0050'}, Path('/unused'),
        now=NOW, fetch_text=fetch, calendar=calendar or Calendar())
    return result, status, calls


def test_licensed_csv_exact_code_date_and_share_units():
    result, status, calls = run()
    assert len(calls) == 1
    assert status['status'] == 'complete'
    assert result['0050']['volume'] == 242070746
    assert result['0050']['date'] == '2026-10-13'
    assert result['0050']['tw_price_source'] == SOURCE
    assert result['0050']['close'] == 112.40
    assert parse_current_csv(CSV, {'50'}, '2026-10-13') == {}


@pytest.mark.parametrize('row', [
    '1151012,0050,112.55,113.20,111.90,112.40,242070746',
    '1151014,0050,112.55,113.20,111.90,112.40,242070746',
    '1150230,0050,112.55,113.20,111.90,112.40,242070746',
    '1151013,0050,--,113.20,111.90,112.40,242070746',
    '1151013,0050,112.55,110,111.90,112.40,242070746',
    '1151013,0050,112.55,113.20,113,112.40,242070746',
    '1151013,0050,112.55,113.20,111.90,0,242070746',
    '1151013,0050,112.55,113.20,111.90,nan,242070746',
    '1151013,0050,112.55,113.20,111.90,inf,242070746',
    '1151013,0050,112.55,113.20,111.90,112.40,-1',
    '1151013,0050,112.55,113.20,111.90,112.40,1.5',
])
def test_invalid_or_wrong_session_rows_are_not_prices(row):
    result, status, calls = run(text=HEADER + row + '\n')
    assert result == {}
    assert status['status'] == 'incomplete'
    assert len(calls) == 1


def test_duplicate_identifier_rejected_even_identical():
    assert run(text=CSV + CSV.splitlines()[1] + '\n')[0] == {}


@pytest.mark.parametrize('text', ['<html>blocked</html>', '', RuntimeError('network failure')])
def test_failed_source_keeps_primary_and_does_not_raise(text):
    primary = {'0050': {'date': '2026-10-12', 'close': 111.0}}
    result, status, calls = run(primary, text)
    assert result == primary
    assert status['status'] == 'unavailable'
    assert len(calls) == 1


def test_fresh_primary_avoids_request_and_newer_primary_never_downgraded():
    fresh = parse_current_csv(CSV, {'0050'}, '2026-10-13')
    assert run(fresh)[2] == []
    fresh['0050']['date'] = '2026-10-14'
    fresh['0050']['close'] = None
    assert run(fresh)[0] == fresh
    assert run(fresh)[2] == []


def test_verified_completed_calendar_required_without_network():
    class Missing(Calendar):
        def lookup(self, *args):
            return {'available': False}
    assert run(calendar=Missing())[2] == []
    class Cached(Calendar):
        def lookup(self, *args):
            return {'available': True, 'status': 'cached', 'sessions': ['2026-10-13']}
    assert run(calendar=Cached())[2] == []
    assert completed_tw_session(Calendar(), NOW.replace(tzinfo=None)) is None
    assert completed_tw_session(Calendar(), NOW.astimezone(ZoneInfo('UTC'))) == '2026-10-13'


def test_actual_full_snapshot_attests_but_never_invents_history():
    snapshot = run()[0]['0050']
    frame = pd.DataFrame({'open':[110.,None], 'high':[112.,None], 'low':[109.,None],
                          'close':[111.,None], 'volume':[1000.,None], 'dividends':[0.,0.]},
                          index=pd.to_datetime(['2026-10-12','2026-10-13']))
    overlaid = overlay_official_daily(frame, snapshot)
    assert len(overlaid) == 2
    assert overlaid.iloc[-1]['volume'] == 242070746
    assert tw_official_daily_metadata(overlaid, snapshot)['source_daily_ohlcv_complete'] is True
    changed = overlaid.copy()
    changed.iloc[-1, changed.columns.get_loc('close')] = 112.41
    assert tw_official_daily_metadata(changed, snapshot)['source_daily_ohlcv_complete'] is False
    empty = frame.iloc[:0]
    assert overlay_official_daily(empty, snapshot).empty


def test_adapter_opt_in_and_original_credit_sidecar_do_not_change(monkeypatch, tmp_path):
    import tw_official_data as official
    import tw_open_data_fallback as fallback
    primary = {'0050': {'date':'2026-10-12','open':110.,'high':112.,'low':109.,'close':111.,'volume':1000.}}
    credit = {'0050': {'credit_date':'2026-10-12','margin_balance':10}}
    monkeypatch.setattr(official, '_parse_twse', lambda *a, **kw:(primary,{},credit,{},{}))
    def supplement(prices, ids, path):
        result, status, _ = run(prices)
        return result, status
    monkeypatch.setattr(fallback,'supplement_twse_prices',supplement)
    universe = [{'symbol':'0050.TW','market':'TW'}]
    before = official.fetch_taiwan_official_data(universe,tmp_path/'disabled.json')
    after = official.fetch_taiwan_official_data(universe,tmp_path/'enabled.json',use_open_data_fallback=True)
    assert before['prices']['0050']['date']=='2026-10-12'
    assert after['prices']['0050']['date']=='2026-10-13'
    assert after['credit']==before['credit']==credit
    assert after['price_fallback_status']['replaced_count']==1
    assert primary['0050']['date']=='2026-10-12'


def test_features_validation_and_original_report_guard_end_to_end():
    from strategy import build_features
    from market_models import validate_taiwan_data, enforce_market_contract
    from briefing import _report_session_issues
    snapshot = run()[0]['0050']
    frame=pd.DataFrame({'open':[110.]*30,'high':[112.]*30,'low':[109.]*30,
                        'close':[111.]*30,'volume':[1000.]*30},
                       index=pd.bdate_range(end='2026-10-12',periods=30))
    frame.loc[pd.Timestamp('2026-10-13')]=float('nan')
    daily=overlay_official_daily(frame,snapshot)
    item={'symbol':'0050.TW','market':'TW','type':'ETF','name':'fixture'}
    row=build_features(item,daily,None,None)
    row.update(tw_official_price_available=True,tw_official_session_date=snapshot['date'],
               tw_price_source=SOURCE,tw_price_unit='TWD/shares',**tw_official_daily_metadata(daily,snapshot))
    row=enforce_market_contract(validate_taiwan_data(row))
    assert row['official_session_date']=='2026-10-13'
    assert row['official_close_price']==112.40
    assert row['official_volume']==242070746
    assert row['source_daily_ohlcv_complete'] is True
    assert row['tw_official_price_available'] is True
    previous={'data':[{**item,'official_session_date':'2026-10-13'}]}
    assert _report_session_issues([row],previous)==[]
    # A stale CSV cannot satisfy the unchanged accepted-session guard.
    stale={**row,'official_session_date':'2026-10-12'}
    assert _report_session_issues([stale],previous)
    from weight_price_evidence import prepare_rows
    safe=prepare_rows([row],{'0050':snapshot},Calendar(),'2026-10-14 08:00:00')[0]
    assert safe['shadow_price_evidence']['status'] == 'unverified'


@pytest.mark.parametrize("volume", ["-1e-400", "1.0000000000000001", "9007199254740993"])
def test_raw_share_precision_cannot_be_rounded_into_validity(volume):
    assert run(text=CSV.replace("242070746", volume))[0] == {}


def test_http_timeout_is_one_attempt_and_preserves_primary(monkeypatch):
    import requests
    import tw_open_data_fallback as fallback
    calls=[]
    def timeout(*args,**kwargs):
        calls.append(kwargs)
        raise requests.Timeout('timeout')
    monkeypatch.setattr(fallback.requests,'get',timeout)
    primary={'0050':{'date':'2026-10-12','close':111.}}
    result,status=supplement_twse_prices(primary,{'0050'},Path('/unused'),now=NOW,calendar=Calendar())
    assert result==primary and status['status']=='unavailable'
    assert len(calls)==1 and calls[0]['timeout']<=15 and calls[0]['stream'] is True


def test_response_size_cap_rejects_before_parsing(monkeypatch):
    import tw_open_data_fallback as fallback
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
        def iter_content(self,size):
            yield b'x'*2_000_001
            raise AssertionError('must stop reading oversized response')
    monkeypatch.setattr(fallback.requests,'get',lambda *a,**kw:Response())
    with pytest.raises(ValueError,match='oversized'):
        fallback._fetch_csv()
