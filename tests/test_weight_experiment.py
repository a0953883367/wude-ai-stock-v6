import pandas as pd
from copy import deepcopy

from weight_experiment import (
    ALLOCATION_TWD,
    CAPITAL_TWD,
    ROUND_TRIP_COST_PCT,
    empty_state,
    ranking_score,
    select_picks,
    update_state,
)


def row(index: int, session_date: str = "2026-08-21") -> dict:
    base = 81 - index
    accumulation = index * 6.5
    return {
        "symbol": f"TW{index:02d}", "name": f"台股{index}",
        "market": "TW", "type": "個股",
        "short_term_rank_tier": 1,
        "short_term_score": base,
        "short_term_base_score": base,
        "short_term_base_ranking_score": base,
        "short_term_ranking_score": round(base * .8 + accumulation * .2, 1),
        "tw_accumulation_available": True,
        "tw_accumulation_score": accumulation,
        "official_session_date": session_date,
        "tw_official_session_date": session_date,
        "tw_official_price_available": True,
        "official_open_price": 100,
        "official_close_price": 101,
        "market_contract_valid": True,
    }


def universe(session_date: str = "2026-08-21") -> list[dict]:
    rows = [row(index, session_date) for index in range(1, 16)]
    rows.append({**row(20, session_date), "symbol": "ETF", "type": "ETF"})
    rows.append({**row(21, session_date), "symbol": "US", "market": "US"})
    return rows


def test_three_weights_rebuild_rank_without_changing_base_or_filling_missing_data():
    sample = row(10)
    assert ranking_score(sample, 0) == sample["short_term_base_ranking_score"]
    assert ranking_score(sample, .2) == sample["short_term_ranking_score"]

    missing = dict(sample, tw_accumulation_available=False, tw_accumulation_score=None)
    assert ranking_score(missing, 0) == ranking_score(missing, .1) == ranking_score(missing, .2)


def test_settlement_preserves_signal_creation_receipt_without_claiming_verified_clock():
    import hashlib
    import json
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="2026-08-21 20:00:00")
    pending = deepcopy(state["models"]["base_0"]["pending"])
    expected = hashlib.sha256(json.dumps({"snapshot_id": pending["snapshot_id"],
        "signal_session_date": pending["signal_session_date"], "created_at": pending["created_at"],
        "picks": pending["picks"]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    update_state(state, universe("2026-08-24"), period="evening", updated_at="2026-08-24 20:00:00")
    day = state["models"]["base_0"]["days"][0]
    assert day["signal_created_at"] == "2026-08-21 20:00:00"
    assert day["signal_snapshot_receipt_sha256"] == expected
    assert day["signal_timestamp_evidence_status"] == "recorded_not_independently_verified"


def test_weight_models_use_same_capital_rules_but_can_select_different_top_ten():
    rows = universe()
    base = select_picks(rows, 0)
    weighted = select_picks(rows, .2)
    assert len(base) == len(weighted) == 10
    assert all(pick["allocation_twd"] == ALLOCATION_TWD for pick in base + weighted)
    assert {pick["symbol"] for pick in base} != {pick["symbol"] for pick in weighted}
    assert all(pick["symbol"] not in {"ETF", "US"} for pick in base + weighted)


def test_experiment_freezes_previous_rank_then_settles_open_to_close_net_of_costs():
    state = empty_state()
    first = universe()
    update_state(state, first, period="evening", updated_at="2026-08-23 20:00:00")
    frozen = state["models"]["accumulation_20"]["pending"]
    assert frozen["signal_session_date"] == "2026-08-21"

    update_state(state, first, period="evening", updated_at="repeat")
    assert state["models"]["accumulation_20"]["pending"] == frozen
    assert state["models"]["accumulation_20"]["completed_days"] == 0

    update_state(state, universe("2026-08-24"), period="evening", updated_at="close")
    model = state["models"]["accumulation_20"]
    assert model["completed_days"] == 1
    assert model["days"][0]["session_date"] == "2026-08-24"
    assert model["days"][0]["gross_profit_twd"] == 10_000
    assert model["days"][0]["data_complete"] is True
    assert model["days"][0]["available_positions"] == 10
    expected_net = CAPITAL_TWD * (1 - ROUND_TRIP_COST_PCT) / 100
    assert model["days"][0]["net_profit_twd"] == round(expected_net, 2)
    assert model["metrics"]["win_rate_pct"] == 100
    assert len(model["days"][0]["ranking_snapshot_id"]) == 12
    assert model["comparison_vs_base"]["interpretation"] == "法人權重相較原模型的額外效益"
    assert model["pending"]["signal_session_date"] == "2026-08-24"


def test_one_missing_stock_per_model_settles_and_holds_allocation_as_cash():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    mixed = universe("2026-08-24")
    frozen_symbols = {
        model["pending"]["picks"][0]["symbol"]
        for model in state["models"].values()
    }
    for item in mixed:
        if item["symbol"] in frozen_symbols:
            item["official_session_date"] = "2026-08-21"
            item["official_open_price"] = None

    update_state(state, mixed, period="evening", updated_at="partial close")

    for model in state["models"].values():
        assert model["completed_days"] == 1
        day = model["days"][0]
        assert day["available_positions"] == 9
        assert day["minimum_required_positions"] == 9
        assert day["nine_of_ten_settlement"] is True
        assert day["invested_twd"] == 900_000
        assert day["idle_twd"] == 100_000
        assert len(day["missing_symbols"]) == 1


def test_only_eight_available_stocks_are_quarantined_without_blocking_future_days():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    mixed = universe("2026-08-24")
    frozen_symbols = {
        pick["symbol"]
        for model in state["models"].values()
        for pick in model["pending"]["picks"][:2]
    }
    for item in mixed:
        if item["symbol"] in frozen_symbols:
            item["official_session_date"] = "2026-08-21"
            item["official_open_price"] = None

    update_state(state, mixed, period="evening", updated_at="partial close")

    for model in state["models"].values():
        assert model["completed_days"] == 0
        assert model["days"] == []
        assert model["invalid_days"][0]["status"] == "data_incomplete"
        assert model["invalid_days"][0]["available_positions"] == 8
        assert model["invalid_days"][0]["session_date"] == "2026-08-24"
        assert model["invalid_days"][0]["invalid_reason"] == "正式收盤後未達至少9/10檔同日官方開盤與收盤價"

    update_state(state, universe("2026-08-25"), period="evening", updated_at="next close")
    for model in state["models"].values():
        assert model["completed_days"] == 0
        assert model["invalid_days"][0]["session_date"] == "2026-08-24"
        assert model["pending"]["signal_session_date"] == "2026-08-25"


def test_legacy_nine_of_ten_day_remains_valid_and_holds_cash():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    update_state(state, universe("2026-08-24"), period="evening", updated_at="day one")
    for model in state["models"].values():
        model["days"][0]["positions"][0].update({
            "data_available": False,
            "open_price": None,
            "sell_price": None,
        })
        model["days"][0]["invested_twd"] = CAPITAL_TWD - ALLOCATION_TWD

    update_state(state, universe("2026-08-25"), period="evening", updated_at="migration")

    for model in state["models"].values():
        assert model["completed_days"] == 2
        assert model["days"][0]["session_date"] == "2026-08-24"
        assert model["days"][0]["available_positions"] == 9
        assert model["days"][0]["invested_twd"] == 900_000
        assert model["days"][0]["idle_twd"] == 100_000
        assert model["invalid_days"] == []


def test_legacy_day_with_only_eight_available_stocks_is_quarantined():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    update_state(state, universe("2026-08-24"), period="evening", updated_at="day one")
    for model in state["models"].values():
        for position in model["days"][0]["positions"][:2]:
            position.update({
                "data_available": False,
                "open_price": None,
                "sell_price": None,
            })
        model["days"][0]["invested_twd"] = CAPITAL_TWD - 2 * ALLOCATION_TWD

    update_state(state, universe("2026-08-25"), period="morning", updated_at="migration")

    for model in state["models"].values():
        assert model["completed_days"] == 1
        assert model["days"][0]["session_date"] == "2026-08-25"
        assert model["invalid_days"][0]["status"] == "data_incomplete"
        assert model["invalid_days"][0]["available_positions"] == 8


def test_diagnostics_explain_equal_weight_overlap_and_rank_quality():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    update_state(state, universe("2026-08-24"), period="evening", updated_at="close")

    diagnostics = state["diagnostics"]
    assert diagnostics["valid_sessions_compared"] == 1
    assert diagnostics["daily_membership_overlap"][0]["base_vs_moderate"]["count"] <= 10
    for model in state["models"].values():
        assert "avg_rank_return_spearman" in model["metrics"]
        assert "avg_top20_capture_rate_pct" in model["metrics"]


def test_intraday_does_nothing_and_evening_starts_next_cycle_after_five_days():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start", intraday=True)
    assert all(model["pending"] is None for model in state["models"].values())

    update_state(state, universe(), period="evening", updated_at="start")
    for day in range(24, 29):
        update_state(
            state, universe(f"2026-08-{day}"), period="evening",
            updated_at=f"2026-08-{day} 20:00:00",
        )
    assert state["status"] == "collecting"
    assert state["winner_model"] is None
    assert state["observed_best_model"] in state["models"]
    assert state["preliminary_assessment"]["status"] == "collecting"
    assert state["completed_cycles"] == 1
    assert state["current_cycle"] == 2
    assert state["current_cycle_completed_days"] == 0
    assert all(model["completed_days"] == 5 for model in state["models"].values())
    assert all(model["completed_cycles"] == 1 for model in state["models"].values())
    assert all(model["current_cycle"] == 2 for model in state["models"].values())
    assert all(model["current_cycle_completed_days"] == 0 for model in state["models"].values())
    assert all(model["pending"]["signal_session_date"] == "2026-08-28" for model in state["models"].values())
    assert all(model["days"][-1]["cycle"] == 1 for model in state["models"].values())
    assert all(model["days"][-1]["cycle_day"] == 5 for model in state["models"].values())

    update_state(
        state, universe("2026-08-31"), period="evening",
        updated_at="2026-08-31 20:00:00",
    )
    assert state["completed_days"] == 6
    assert state["current_cycle"] == 2
    assert state["current_cycle_completed_days"] == 1
    for model in state["models"].values():
        assert model["completed_days"] == 6
        assert model["days"][-1]["cycle"] == 2
        assert model["days"][-1]["cycle_day"] == 1
        assert model["cycles"][0]["status"] == "complete"
        assert model["cycles"][1]["status"] == "collecting"
        assert model["pending"]["signal_session_date"] == "2026-08-31"


def test_twenty_days_records_preliminary_failure_without_promoting_weight():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    # Twenty actual weekdays, rather than counting weekends as observations.
    for timestamp in pd.bdate_range("2026-08-24", periods=20):
        session_date = timestamp.date().isoformat()
        losing = universe(session_date)
        for item in losing:
            item["official_close_price"] = 99
        update_state(state, losing, period="evening", updated_at=session_date)

    assessment = state["preliminary_assessment"]
    assert state["completed_days"] == 20
    assert state["status"] == "failed_preliminary"
    assert state["winner_model"] is None
    assert state["observed_best_model"] in state["models"]
    assert assessment["failure_feedback_recorded"] is True
    assert assessment["formal_v6_changed"] is False
    assert assessment["broker_orders"] is False
    assert assessment["next_shadow_actions"]
    for model in state["models"].values():
        metrics = model["metrics"]
        assert metrics["gross_profit_twd"] < 0
        assert metrics["transaction_cost_twd"] > 0
        assert metrics["cost_drag_pct"] > 0
        assert metrics["losing_days"] == 20


def test_legacy_complete_state_reopens_collection_without_inventing_missed_day():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start")
    for day in range(24, 29):
        update_state(
            state, universe(f"2026-08-{day}"), period="evening",
            updated_at=f"2026-08-{day} 20:00:00",
        )
    for model in state["models"].values():
        model["status"] = "complete"
        model["pending"] = None
    state["status"] = "complete"

    update_state(
        state, universe("2026-08-31"), period="evening",
        updated_at="2026-08-31 21:00:00",
    )

    assert state["status"] == "collecting"
    assert state["completed_days"] == 5
    assert state["current_cycle"] == 2
    assert state["current_cycle_completed_days"] == 0
    for model in state["models"].values():
        assert [day["session_date"] for day in model["days"]] == [
            "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",
        ]
        assert model["pending"]["signal_session_date"] == "2026-08-31"


def test_morning_settles_existing_pending_but_never_creates_new_snapshot():
    state = empty_state()
    update_state(state, universe(), period="morning", updated_at="morning without signal")
    assert all(model["pending"] is None for model in state["models"].values())

    update_state(state, universe(), period="evening", updated_at="evening signal")
    update_state(
        state, universe("2026-08-24"), period="morning",
        updated_at="morning backfill",
    )

    for model in state["models"].values():
        assert model["completed_days"] == 1
        assert model["days"][0]["session_date"] == "2026-08-24"
        assert model["pending"] is None


def test_exact_historical_price_repairs_legacy_missing_position_before_settlement():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="signal")
    update_state(state, universe("2026-08-24"), period="evening", updated_at="day one")
    for model in state["models"].values():
        missing = model["days"][0]["positions"][0]
        missing.update({
            "data_available": False,
            "open_price": None,
            "sell_price": None,
            "gross_return_pct": None,
            "net_return_pct": None,
            "gross_profit_twd": 0.0,
            "net_profit_twd": 0.0,
        })
        model["days"][0]["invested_twd"] = CAPITAL_TWD - ALLOCATION_TWD
    missing_symbols = {
        model["days"][0]["positions"][0]["symbol"]
        for model in state["models"].values()
    }
    history = {
        symbol: pd.DataFrame(
            {"open": [100.0], "close": [103.0]},
            index=pd.to_datetime(["2026-08-24"]),
        )
        for symbol in missing_symbols
    }

    update_state(
        state, universe("2026-08-25"), period="morning",
        updated_at="repair", price_history=history,
    )

    for model in state["models"].values():
        assert model["completed_days"] == 2
        repaired = model["days"][0]["positions"][0]
        assert repaired["data_available"] is True
        assert repaired["historical_price_repair"] is True
        assert model["days"][0]["historical_price_repairs"] == [repaired["symbol"]]
        assert model["invalid_days"] == []


def test_weekend_prices_never_settle_or_freeze_a_new_signal():
    state = empty_state()
    update_state(state, universe('2026-09-18'), period='evening', updated_at='signal')
    frozen = deepcopy([m['pending']['picks'] for m in state['models'].values()])
    weekend = universe('2026-09-20')
    for item in weekend:
        item['tw_official_session_date'] = '2026-09-18'
    update_state(state, weekend, period='evening', updated_at='Sunday')
    assert state['completed_days'] == 0
    assert [m['pending']['picks'] for m in state['models'].values()] == frozen
    empty = empty_state()
    update_state(empty, weekend, period='evening', updated_at='Sunday')
    assert all(m['pending'] is None for m in empty['models'].values())


def test_exchange_date_mismatch_holds_cash_and_cannot_supply_tenth_position():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='signal')
    rows = universe('2026-08-24')
    # Make every model lose exactly its first pick, without trusting its price.
    for model in state['models'].values():
        rows_for_model = deepcopy(rows)
        bad = model['pending']['picks'][0]['symbol']
        for item in rows_for_model:
            item['tw_official_session_date'] = '2026-08-21' if item['symbol'] == bad else '2026-08-24'
        single = empty_state()
        single['models'] = {model['key']: deepcopy(model)}
        update_state(single, rows_for_model, period='morning', updated_at='outcome')
        day = single['models'][model['key']]['days'][0]
        assert day['available_positions'] == 9
        assert day['idle_twd'] == ALLOCATION_TWD
        position = next(p for p in day['positions'] if p['symbol'] == bad)
        assert position['data_available'] is False
        assert position['open_price'] is None
        assert position['net_profit_twd'] == 0


def test_legacy_sunday_is_preserved_but_excluded_and_prior_verdict_kept_once():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='signal')
    update_state(state, universe('2026-08-24'), period='morning', updated_at='outcome')
    originals = {}
    for key, model in state['models'].items():
        bad = deepcopy(model['days'][0])
        bad.update(day=2, session_date='2026-09-20', signal_session_date='2026-09-18', ranking_snapshot_id='legacy-Sunday')
        model['days'].append(bad)
        originals[key] = deepcopy(bad)
    previous = deepcopy(state['preliminary_assessment'])
    update_state(state, universe('2026-08-24'), period='morning', updated_at='audit')
    assert state['completed_days'] == 1
    for key, model in state['models'].items():
        assert model['days'][1] == originals[key]
        assert model['session_validation']['raw_days'] == 2
        assert model['session_validation']['valid_days'] == 1
        assert model['session_validation']['excluded_days'][0]['reason'] == 'non_trading_weekend'
        assert model['metrics']['evaluated_positions'] == 10
        assert sum(c['completed_days'] for c in model['cycles']) == 1
    assert state['assessment_before_session_validation'][0]['assessment'] == previous
    update_state(state, universe('2026-08-24'), period='morning', updated_at='repeat')
    assert len(state['assessment_before_session_validation']) == 1
    assert state['completed_days'] == 1


def test_duplicate_and_non_forward_outcomes_are_excluded_from_metrics():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='signal')
    update_state(state, universe('2026-08-24'), period='morning', updated_at='outcome')
    for model in state['models'].values():
        duplicate = deepcopy(model['days'][0])
        backwards = deepcopy(duplicate)
        backwards.update(session_date='2026-08-21', signal_session_date='2026-08-24')
        model['days'].extend([duplicate, backwards])
    update_state(state, universe('2026-08-24'), period='morning', updated_at='audit')
    assert state['completed_days'] == 1
    assert state['diagnostics']['valid_sessions_compared'] == 1
    for model in state['models'].values():
        assert len(model['days']) == 3
        reasons = {x['reason'] for x in model['session_validation']['excluded_days']}
        assert reasons == {'duplicate_outcome_session', 'outcome_not_after_signal'}


def test_invalid_legacy_pending_is_preserved_and_valid_collection_resumes():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='signal')
    for model in state['models'].values():
        model['pending']['signal_session_date'] = '2026-09-20'
    update_state(state, universe('2026-09-21'), period='evening', updated_at='resume')
    assert state['completed_days'] == 0
    for model in state['models'].values():
        assert model['invalid_pending'][0]['original_pending']['signal_session_date'] == '2026-09-20'
        assert model['pending']['signal_session_date'] == '2026-09-21'
    update_state(state, universe('2026-09-22'), period='morning', updated_at='next')
    assert state['completed_days'] == 1
    assert all(len(m['invalid_pending']) == 1 for m in state['models'].values())


def test_missing_exchange_date_is_not_trusted_as_new_official_evidence():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='signal')
    missing_source = universe('2026-08-24')
    for item in missing_source:
        item.pop('tw_official_session_date')
    update_state(state, missing_source, period='evening', updated_at='missing source')
    assert state['completed_days'] == 0
    for model in state['models'].values():
        assert set(model['pending']['rejected_prices'].values()) == {'exchange_session_unavailable'}
    update_state(state, universe('2026-08-24'), period='morning', updated_at='verified retry')
    assert state['completed_days'] == 1


def test_readonly_reassessment_keeps_all_observations_and_pending_picks():
    from weight_experiment import refresh_session_evaluation
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='signal')
    update_state(state, universe('2026-08-24'), period='evening', updated_at='outcome')
    raw = {key: {field: deepcopy(model.get(field)) for field in
            ('days', 'invalid_days', 'pending', 'last_pick_symbols')}
           for key, model in state['models'].items()}
    refresh_session_evaluation(state, 'review only')
    assert all(all(model.get(field) == value for field, value in raw[key].items())
               for key, model in state['models'].items())
    assert state['completed_days'] == 1


def strict_rows(session="2026-08-24", next_session="2026-08-25"):
    import hashlib, json
    rows = universe(session)
    for r in rows:
        if r['market'] != 'TW':
            continue
        proof = {'status': 'verified', 'date': session, 'open': 100, 'close': 101,
                 'source': 'TWSE OpenAPI', 'calendar_status': {'available': True, 'is_session': True},
                 'session_complete': True, 'next_session_date': next_session}
        proof['sha256'] = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        r['shadow_price_contract_required'] = True
        r['shadow_price_evidence'] = proof
    return rows


def test_legacy_pending_without_next_session_is_archived_not_backfilled():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='2026-08-21 20:00:00')
    original = deepcopy(state['models']['base_0']['pending'])
    original_days = deepcopy(state['models']['base_0']['days'])
    rows = strict_rows()
    before_rows = deepcopy(rows)
    update_state(state, rows, period='evening', updated_at='2026-08-24 20:00:00', preserve_raw_records=True)
    model = state['models']['base_0']
    assert model['days'] == original_days
    assert model['invalid_pending'][0]['original_pending'] == original
    assert model['invalid_pending'][0]['reason'] == 'legacy_next_session_unverifiable'
    assert model['pending']['signal_session_date'] == '2026-08-24'
    assert model['pending']['expected_execution_session_date'] == '2026-08-25'
    assert rows == before_rows
    archive = deepcopy(model['invalid_pending'])
    update_state(state, rows, period='evening', updated_at='2026-08-24 20:01:00', preserve_raw_records=True)
    assert model['invalid_pending'] == archive


def test_legacy_pending_not_released_on_morning_or_unverified_rows():
    state = empty_state()
    update_state(state, universe(), period='evening', updated_at='2026-08-21 20:00:00')
    snapshot = state['models']['base_0']['pending']['snapshot_id']
    update_state(state, strict_rows(), period='morning', updated_at='2026-08-25 06:00:00', preserve_raw_records=True)
    assert state['models']['base_0']['pending']['snapshot_id'] == snapshot
    rows = strict_rows()
    for r in rows:
        r['shadow_price_evidence'] = {}
    update_state(state, rows, period='evening', updated_at='2026-08-25 20:00:00', preserve_raw_records=True)
    assert state['models']['base_0']['pending']['snapshot_id'] == snapshot


def test_verified_next_session_still_settles_without_legacy_quarantine():
    state = empty_state()
    update_state(state, strict_rows('2026-08-21', '2026-08-24'), period='evening',
                 updated_at='2026-08-21 20:00:00', preserve_raw_records=True)
    assert state['models']['base_0']['pending']['expected_execution_session_date'] == '2026-08-24'
    update_state(state, strict_rows(), period='evening', updated_at='2026-08-24 20:00:00', preserve_raw_records=True)
    model = state['models']['base_0']
    assert len(model['days']) == 1
    assert model['days'][0]['session_date'] == '2026-08-24'
    assert not model.get('invalid_pending')
