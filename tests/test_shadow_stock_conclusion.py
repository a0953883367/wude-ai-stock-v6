from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from market_calendar import OfficialMarketCalendar
from shadow_stock_conclusion import build_conclusion, source_snapshot
from trade_plan_shadow import build_trade_plan_report, _plan_for_horizon
from test_trade_plan_shadow import _sample_row

NOW = datetime(2026, 10, 5, 22, tzinfo=timezone.utc)


def calendar_file(tmp_path):
    sessions = [f"2026-10-{n:02}" for n in (2, 5, 6, 7, 8, 12, 13, 14, 15, 16, 19)]
    payload = {"version": 1, "markets": {m: {"years": {"2026": {
        "year": 2026, "status": "verified_twse_tpex" if m == "TW" else "verified_alpaca",
        "sources": ["TWSE", "TPEx"] if m == "TW" else ["Alpaca Market Calendar"],
        "sessions": sessions,
        "session_details": {s: {"open": "09:30", "close": "16:00"} for s in sessions},
    }}} for m in ("TW", "US")}}
    (tmp_path / "official_market_calendar.json").write_text(json.dumps(payload))
    return OfficialMarketCalendar(tmp_path / "official_market_calendar.json", auto_refresh=False, allow_network=False)


def row_for(market="TW", asset="個股"):
    row = _sample_row()
    row.update(market=market, asset_type=asset, session_date="2026-10-05")
    row["symbol"] = "2330.TW" if market == "TW" else "TEST"
    row["source_snapshot"] = {
        "market_contract_valid": True, "session_date": "2026-10-05",
        "source_session_date": "2026-10-05", "close": 1000, "source_available": True, "ohlcv_complete": True,
        "source": "TWSE OpenAPI" if market == "TW" else "Alpaca SIP daily bars",
        "unit": "TWD/shares" if market == "TW" else "USD/shares",
    }
    row["evidence"] = [{"source_id": "short_plan", "horizon": "short", "affects_decision": True,
                         "market": market, "symbol": row["symbol"], "as_of": "2026-10-05",
                         "provenance": "short_plan", "direction": "support"}]
    return row


def conclude(tmp_path, row=None, now=NOW):
    row = row or row_for()
    return build_conclusion(row, _plan_for_horizon(row, "short"), calendar=calendar_file(tmp_path), now=now)


@pytest.mark.parametrize("market,asset", [("TW", "個股"), ("US", "個股"), ("TW", "ETF"), ("US", "ETF")])
def test_source_qualified_plan_is_not_same_snapshot_entry_by_market_asset(tmp_path, market, asset):
    row = row_for(market, asset)
    original = deepcopy(row)
    result = conclude(tmp_path, row)
    assert result["code"] == "wait"
    assert result["entry_evaluation"]["status"] == "not_evaluated"
    assert result["entry_evaluation"]["eligible"] is None
    assert result["shadow_only"] is True
    assert result["automatic_orders"] is False
    assert result["probability_pct"] is None
    assert result["validation"]["formal_adoption_ready"] is False
    assert result["evidence"]["additional_weight"] == 0
    assert all(g["passed"] for g in result["gates"])
    assert row == original
    if asset == "ETF":
        assert result["applicability"]["company_financials"] == "not_applicable"
    if market == "US":
        assert result["applicability"]["tw_institution"] == "not_applicable"
        assert result["expires_at"] == "2026-10-06T16:00:00-04:00"
    else:
        assert result["expires_at"] == "2026-10-06T13:30:00+08:00"


@pytest.mark.parametrize("field,value", [("source", None), ("source", "Yahoo / unknown"),
                                        ("source", "Alpaca SIP daily bars"), ("source_available", False),
                                        ("source_session_date", "2026-10-02"), ("unit", "USD/shares"),
                                        ("close", None), ("market_contract_valid", False)])
def test_source_gates_never_bypass_with_high_score(tmp_path, field, value):
    row = row_for()
    row["source_snapshot"][field] = value
    row["horizons"]["short"].update(score=100, confidence=100)
    assert conclude(tmp_path, row)["code"] == "insufficient"


@pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf"), True, 1002])
def test_missing_invalid_or_mismatched_price_blocks(tmp_path, price):
    row = row_for()
    row["price"] = price
    assert conclude(tmp_path, row)["code"] == "insufficient"


def test_stale_and_unfinished_sessions_block(tmp_path):
    assert conclude(tmp_path, now=datetime(2026, 10, 6, 5, 30, tzinfo=timezone.utc))["code"] == "insufficient"
    assert conclude(tmp_path, now=datetime(2026, 10, 5, 5, 29, tzinfo=timezone.utc))["code"] == "insufficient"


def test_no_calendar_and_cross_year_coverage_never_guess_weekdays(tmp_path):
    row = row_for()
    missing = OfficialMarketCalendar(tmp_path / "missing.json", auto_refresh=False, allow_network=False)
    result = build_conclusion(row, _plan_for_horizon(row, "short"), calendar=missing, now=NOW)
    assert result["code"] == "insufficient"
    assert result["expires_at"] is None
    assert conclude(tmp_path, now=datetime(2027, 1, 1, tzinfo=timezone.utc))["code"] == "insufficient"


def test_early_close_and_separate_market_clock(tmp_path):
    cal = calendar_file(tmp_path)
    path = tmp_path / "official_market_calendar.json"
    payload = json.loads(path.read_text())
    payload["markets"]["US"]["years"]["2026"]["session_details"]["2026-10-06"]["close"] = "13:00"
    path.write_text(json.dumps(payload))
    cal = OfficialMarketCalendar(path, auto_refresh=False, allow_network=False)
    row = row_for("US")
    result = build_conclusion(row, _plan_for_horizon(row, "short"), calendar=cal, now=NOW)
    assert result["expires_at"] == "2026-10-06T13:00:00-04:00"
    at = datetime(2026, 10, 6, 6, tzinfo=timezone.utc)
    assert build_conclusion(row, _plan_for_horizon(row, "short"), calendar=cal, now=at)["code"] == "wait"
    assert conclude(tmp_path, row_for("TW"), now=at)["code"] == "insufficient"


def test_missing_us_close_time_blocks(tmp_path):
    cal = calendar_file(tmp_path)
    cal._state["markets"]["US"]["years"]["2026"]["session_details"]["2026-10-06"].pop("close")
    row = row_for("US")
    assert build_conclusion(row, _plan_for_horizon(row, "short"), calendar=cal, now=NOW)["code"] == "insufficient"


def test_reasons_deduplicate_and_correlated_evidence_never_adds_votes(tmp_path):
    row = row_for()
    row["evidence"] *= 3
    result = conclude(tmp_path, row)
    assert result["code"] == "wait"
    assert result["evidence"]["duplicate_count"] == 2
    assert result["evidence"]["correlated_groups"] == ["existing_price_and_model"]
    assert result["evidence"]["additional_weight"] == 0
    row["evidence"][0]["as_of"] = "2026-10-02"
    assert conclude(tmp_path, row)["code"] == "insufficient"


@pytest.mark.parametrize("mutate,expected", [("risk", "avoid"), ("wait", "wait"),
                                            ("conflict", "wait"), ("no_chase", "wait"), ("core", "insufficient")])
def test_reuses_existing_plan_gates(tmp_path, mutate, expected):
    row = row_for()
    if mutate == "risk":
        row["risk_blocks"] = ["事件風險"]
    elif mutate == "wait":
        row["horizons"]["short"]["recommendation"] = "watch"
    elif mutate == "conflict":
        row["unresolved_conflict_count"] = 1
    elif mutate == "no_chase":
        row["horizons"]["short"]["entry_high"] = 999
    else:
        row["core_data_missing"] = ["current_price"]
    assert conclude(tmp_path, row)["code"] == expected


def write_hub(tmp_path, row):
    calendar_file(tmp_path)
    index = {"decision_files": ["decision_hub_01.json"], "updated_at": "2026-10-06 06:00:00", "readiness": {"validation_60d": {"ready": True}}}
    chunk = {"updated_at": index["updated_at"], "decisions": [row]}
    (tmp_path / "decision_hub.json").write_text(json.dumps(index))
    (tmp_path / "decision_hub_01.json").write_text(json.dumps(chunk))
    return chunk


def test_report_integrates_conclusion_and_blocks_mixed_batches(tmp_path):
    row = row_for()
    chunk = write_hub(tmp_path, row)
    report = build_trade_plan_report(tmp_path, now=NOW)
    plan = report["plans"][0]["plans"]["short"]
    assert plan["conclusion"]["code"] == "wait"
    assert report["validation"]["formal_adoption_ready"] is False
    assert report["plans"][0]["formal_rank"] == row["formal_rank"]
    assert report["plans"][0]["formal_score"] == row["formal_score"]
    chunk["updated_at"] = "old"
    (tmp_path / "decision_hub_01.json").write_text(json.dumps(chunk))
    plan = build_trade_plan_report(tmp_path, now=NOW)["plans"][0]["plans"]["short"]
    assert plan["conclusion"]["code"] == "insufficient"
    assert plan["active_entry_plan"] is False
    assert plan["no_buy_reason"]


def test_duplicate_stock_outputs_one_fail_closed_row(tmp_path):
    chunk = write_hub(tmp_path, row_for())
    chunk["decisions"] *= 2
    (tmp_path / "decision_hub_01.json").write_text(json.dumps(chunk))
    report = build_trade_plan_report(tmp_path, now=NOW)
    assert len(report["plans"]) == 1
    assert report["plans"][0]["plans"]["short"]["conclusion"]["code"] == "insufficient"


def test_source_snapshot_does_not_invent_daily_provider_or_apply_tw_fields_to_us():
    result = source_snapshot({"market": "US", "official_session_date": "2026-10-05",
                              "tw_price_source": "TWSE OpenAPI", "us_live_source": "Alpaca SIP"})
    assert result["source"] is None
    assert result["source_available"] is False


@pytest.mark.parametrize('as_of', ['2026-10-05T23:59:59Z', '2026-10-05T23:59:59-04:00',
                                  '2026-10-05-broken', '2026-10-06 06:01:00'])
def test_future_or_malformed_evidence_never_becomes_eligible(tmp_path, as_of):
    row = row_for()
    row['evidence'][0]['as_of'] = as_of
    assert conclude(tmp_path, row)['code'] == 'insufficient'


def test_naive_report_time_is_taipei_and_aware_evidence_is_market_local(tmp_path):
    row = row_for('US')
    row['evidence'][0]['as_of'] = '2026-10-06 05:00:00'  # Oct 5, 17:00 New York.
    assert conclude(tmp_path, row)['code'] == 'wait'


def test_future_report_timestamp_blocks_even_when_chunks_match(tmp_path):
    chunk = write_hub(tmp_path, row_for())
    index = json.loads((tmp_path / 'decision_hub.json').read_text())
    index['updated_at'] = chunk['updated_at'] = '2026-10-06 06:01:00'
    (tmp_path / 'decision_hub.json').write_text(json.dumps(index))
    (tmp_path / 'decision_hub_01.json').write_text(json.dumps(chunk))
    assert build_trade_plan_report(tmp_path, now=NOW)['plans'][0]['plans']['short']['conclusion']['code'] == 'insufficient'


@pytest.mark.parametrize('status', ['stale', 'expired', 'missing', 'unverified', 'invalid', 'data_blocked'])
def test_same_day_bad_evidence_status_does_not_pass(tmp_path, status):
    row = row_for()
    row['evidence'][0]['status'] = status
    assert conclude(tmp_path, row)['code'] == 'insufficient'


def test_missing_declared_chunk_blocks_existing_rows(tmp_path):
    write_hub(tmp_path, row_for())
    path = tmp_path / 'decision_hub.json'
    hub = json.loads(path.read_text())
    hub['decision_files'].append('decision_hub_02.json')
    path.write_text(json.dumps(hub))
    assert build_trade_plan_report(tmp_path, now=NOW)['plans'][0]['plans']['short']['conclusion']['code'] == 'insufficient'


def test_incomplete_synthetic_candle_cannot_be_eligible(tmp_path):
    row = row_for()
    row['source_snapshot']['ohlcv_complete'] = False
    assert conclude(tmp_path, row)['code'] == 'insufficient'


def test_legacy_yahoo_attestation_never_becomes_an_allowed_us_entry_source(tmp_path):
    row = row_for('US')
    row['source_snapshot']['source'] = 'Yahoo Finance daily bars'
    result = conclude(tmp_path, row)
    assert result['code'] == 'insufficient'
    assert next(g for g in result['gates'] if g['code'] == 'source')['passed'] is False
    assert next(g for g in result['gates'] if g['code'] == 'calendar')['passed'] is True
    cal = calendar_file(tmp_path)
    cal._state['markets']['US']['years'] = {}
    assert build_conclusion(row, _plan_for_horizon(row, 'short'), calendar=cal, now=NOW)['code'] == 'insufficient'


def test_input_categories_are_references_separate_assets_and_hide_synthetic_ohlcv():
    from shadow_stock_conclusion import input_evidence_categories
    raw = {'market': 'US', 'type': 'ETF', 'official_session_date': '2026-10-05',
           'official_open_price': 100, 'official_close_price': 100, 'official_volume': 0,
           'kline_pattern': 'fake-filled', 'rsi': 60, 'ma20': 98, 'chart_pattern_weekly_k': 80,
           'institution_net': 1000, 'financial_report_date': '2026-06-30', 'per': 20}
    original = deepcopy(raw)
    groups = {g['id']: g for g in input_evidence_categories(raw)}
    assert groups['daily_candle']['status'] == 'unverified'
    vals={i['key']:i['value'] for i in groups['daily_candle']['items']}
    assert vals['official_open_price']==100
    assert vals['kline_pattern'] is vals['official_volume'] is None
    assert groups['fundamentals']['status'] == 'not_applicable'
    assert groups['tw_institution']['status'] == 'not_applicable'
    assert groups['price_indicators']['status'] == 'reference'
    assert '週線可能尚未完成' in groups['momentum_audit']['note']
    assert all(g['additional_weight'] == 0 for g in groups.values())
    assert raw == original
    raw.update(source_daily_ohlcv_complete=True, source_daily_ohlcv_session_date='2026-10-02')
    assert source_snapshot(raw)['ohlcv_complete'] is False
    raw['source_daily_ohlcv_session_date'] = '2026-10-05'
    assert source_snapshot(raw)['ohlcv_complete'] is True


def test_hub_carries_attested_source_and_actual_inputs_without_changing_formal_row():
    from decision_hub import _build_decision
    from test_decision_hub import _row
    raw = _row(official_open_price=98, official_high_price=101, official_low_price=97,
               official_close_price=100, official_volume=12345,
               tw_official_price_available=True, tw_price_source='TWSE OpenAPI',
               tw_price_unit='TWD/shares', tw_official_session_date='2026-08-29',
               source_daily_ohlcv_complete=True, source_daily_ohlcv_session_date='2026-08-29')
    original = deepcopy(raw)
    decision = _build_decision(raw, valuation=None, rotation=None, inverse_mapping=None,
                               inverse_candidate=None, inverse_ready=False, capital_flow=None,
                               institution_eligible=True, adaptive_trust=None,
                               updated_at='2026-08-29 20:00:00')
    assert decision['source_snapshot']['ohlcv_complete'] is True
    candle = next(g for g in decision['input_evidence_categories'] if g['id'] == 'daily_candle')
    assert next(i for i in candle['items'] if i['key'] == 'official_volume')['value'] == 12345
    assert decision['formal_score'] == original['overall_ranking_score']
    assert raw == original


def test_news_after_price_session_before_evaluation_is_not_future_data(tmp_path):
    row=row_for()
    row['evidence'].append({'source_id':'verified_news','horizon':'risk','affects_decision':True,
        'market':'TW','symbol':row['symbol'],'as_of':'2026-10-05T21:00:00Z',
        'provenance':'news scan','direction':'neutral','status':'available'})
    # Taipei Oct6 scan is after Oct5 price session but before NOW Oct5 22:00Z.
    assert conclude(tmp_path,row)['code']=='wait'
    row['evidence'][-1]['direction']='oppose'
    row['risk_blocks']=['已確認新聞風險']
    assert conclude(tmp_path,row)['code']=='avoid'


@pytest.mark.parametrize('stamp',['2026-10-05T23:00:00Z','2026-10-04T00:00:00Z','2026-10-05'])
def test_news_observation_future_stale_or_date_only_blocks(tmp_path,stamp):
    row=row_for()
    row['evidence'].append({'source_id':'verified_news','horizon':'risk','affects_decision':True,
        'market':'TW','symbol':row['symbol'],'as_of':stamp,'provenance':'news scan','direction':'neutral'})
    assert conclude(tmp_path,row)['code']=='insufficient'


def test_news_stale_fallback_cannot_become_fresh(tmp_path):
    row=row_for();row['source_snapshot']['news_cache_stale']=True
    row['evidence'].append({'source_id':'verified_news','horizon':'risk','affects_decision':True,
        'market':'TW','symbol':row['symbol'],'as_of':'2026-10-05T21:00:00Z','provenance':'news scan','direction':'neutral'})
    assert conclude(tmp_path,row)['code']=='insufficient'


def test_missing_new_attestation_is_pending_not_claim_missing_prices(tmp_path):
    row=row_for();row['source_snapshot'].update(attestation_status='pending',ohlcv_complete=False)
    result=conclude(tmp_path,row)
    assert result['code']=='insufficient'
    assert result['data_status']['code']=='source_attestation_pending'
    assert result['price_basis']['aligned'] is True


def test_quote_and_completed_close_mismatch_stays_noneligible(tmp_path):
    row=row_for();row['price']=1002
    result=conclude(tmp_path,row)
    assert result['code']=='insufficient'
    assert result['data_status']['code']=='price_basis_mismatch'
    assert result['price_basis']['reported_quote']==1002
    assert result['price_basis']['completed_close']==1000


def test_news_freshness_expiry_limits_cached_conclusion(tmp_path):
    row=row_for()
    row['evidence'].append({'source_id':'verified_news','horizon':'risk','affects_decision':True,
        'market':'TW','symbol':row['symbol'],'as_of':'2026-10-05T10:00:00Z',
        'provenance':'news scan','direction':'neutral'})
    result=conclude(tmp_path,row)
    assert result['code']=='wait'
    assert datetime.fromisoformat(result['expires_at'])==datetime(2026,10,6,4,tzinfo=timezone.utc)
    assert conclude(tmp_path,row,now=datetime(2026,10,6,4,tzinfo=timezone.utc))['code']=='insufficient'


def test_future_official_proof_is_not_available_at_earlier_cutoff(tmp_path):
    row=row_for()
    row['source_snapshot']['daily_proof']={'status':'attested','fetched_at':'2026-10-05T23:00:00Z'}
    assert conclude(tmp_path,row)['code']=='insufficient'
    row['source_snapshot']['daily_proof']['fetched_at']='2026-10-05T21:00:00Z'
    assert conclude(tmp_path,row)['code']=='wait'


def test_verified_emerging_scope_explains_ohlcv_block_without_buy_hint(tmp_path):
    row = row_for()
    row['source_snapshot'].update(ohlcv_complete=False, attestation_status='invalid',
        daily_proof={'reason': 'unsupported_emerging_market', 'venue':'TPEX_EMERGING',
                     'fetched_at':'2026-10-05T06:00:00+00:00'})
    result = conclude(tmp_path, row)
    assert result['code'] == 'insufficient'
    assert result['entry_evaluation']['eligible'] is None
    ohlcv = next(g for g in result['gates'] if g['code'] == 'ohlcv')
    assert ohlcv['passed'] is False
    assert '興櫃市場' in ohlcv['reason']
    assert '最新成交／均價不能補成完整 OHLCV' in ohlcv['reason']
