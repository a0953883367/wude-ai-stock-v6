from copy import deepcopy
from datetime import timedelta
import pytest

from strategy import _entry_plan
from shadow_stock_conclusion import build_conclusion
from trade_plan_shadow import _plan_for_horizon, build_trade_plan_report
from test_shadow_stock_conclusion import row_for, calendar_file, NOW, write_hub


@pytest.mark.parametrize('market,asset',[('TW','個股'),('TW','ETF'),('US','個股'),('US','ETF')])
@pytest.mark.parametrize('horizon',['short','medium','long'])
def test_cohorts_never_use_creation_snapshot_as_entry_quote(tmp_path,market,asset,horizon):
    row=row_for(market,asset)
    raw={'market':market,'type':asset,'price':1000,'atr14':20,'rsi':50}
    generated=_entry_plan(raw,80,80,80,80,80,80)
    assert generated['buy_zone_high'] < raw['price']
    if horizon=='short':
        low,high=generated['buy_zone_low'],generated['buy_zone_high']
    elif horizon=='medium':
        low,high=generated['better_buy_low'],generated['better_buy_high']
    else:
        # Long support/MA-based zones need not be below creation price.
        low,high=980,1005
    row['horizons'][horizon].update(entry_low=low,entry_high=high)
    source={'short':'short_plan','medium':'medium_45d','long':'long_6m'}[horizon]
    row['evidence'][0].update(source_id=source,horizon=horizon)
    row['_source_updated_at']='2026-10-06 06:00:00'
    original=deepcopy(row)
    plan=_plan_for_horizon(row,horizon)
    result=build_conclusion(row,plan,calendar=calendar_file(tmp_path),now=NOW)
    assert result['mode']=='plan_only'
    assert result['code']=='wait'
    assert result['entry_evaluation']['status']=='not_evaluated'
    assert result['entry_evaluation']['eligible'] is None
    assert result['plan_assessment']['data_gates_passed'] is True
    assert result['plan_assessment']['predictive_efficacy_validated'] is False
    assert result['plan_snapshot']['levels']['entry_high']==plan['entry_high']
    assert result['plan_snapshot']['source_batch_at']==row['_source_updated_at']
    assert row==original


def test_even_in_range_same_snapshot_is_not_candidate_and_levels_stay_frozen(tmp_path):
    row=row_for();plan=_plan_for_horizon(row,'short')
    assert plan['entry_low'] <= row['price'] <= plan['entry_high']
    cal=calendar_file(tmp_path)
    first=build_conclusion(row,plan,calendar=cal,now=NOW)
    later=build_conclusion(row,plan,calendar=cal,now=NOW+timedelta(minutes=5))
    assert first['code']==later['code']=='wait'
    assert first['plan_snapshot']==later['plan_snapshot']
    assert first['entry_evaluation']['evaluation_quote'] is None
    # Adding an unrecognized quote cannot silently create a new feed contract.
    row['evaluation_quote']={'price':990,'source':'invented','observed_at':NOW.isoformat()}
    assert build_conclusion(row,plan,calendar=cal,now=NOW)['entry_evaluation']['status']=='not_evaluated'


def test_candidate_metric_is_unknown_not_zero_and_formal_fields_preserved(tmp_path):
    row=row_for();write_hub(tmp_path,row)
    report=build_trade_plan_report(tmp_path,now=NOW)
    assert report['mode']=='shadow_plan_only'
    assert report['summary']['candidate'] is None
    assert report['summary']['entry_not_evaluated']==1
    assert report['entry_evaluation']['candidate_count'] is None
    assert report['plans'][0]['formal_rank']==row['formal_rank']
    assert report['plans'][0]['formal_score']==row['formal_score']
    assert report['plans'][0]['plans']['short']['entry_high']==row['horizons']['short']['entry_high']
