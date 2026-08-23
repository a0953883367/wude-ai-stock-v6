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
    expected_net = CAPITAL_TWD * (1 - ROUND_TRIP_COST_PCT) / 100
    assert model["days"][0]["net_profit_twd"] == round(expected_net, 2)
    assert model["metrics"]["win_rate_pct"] == 100
    assert model["pending"]["signal_session_date"] == "2026-08-24"


def test_intraday_does_nothing_and_evening_stops_all_models_after_five_days():
    state = empty_state()
    update_state(state, universe(), period="evening", updated_at="start", intraday=True)
    assert all(model["pending"] is None for model in state["models"].values())

    update_state(state, universe(), period="evening", updated_at="start")
    for day in range(24, 29):
        update_state(
            state, universe(f"2026-08-{day}"), period="evening",
            updated_at=f"2026-08-{day} 20:00:00",
        )
    assert state["status"] == "complete"
    assert state["winner_model"] in state["models"]
    assert all(model["completed_days"] == 5 for model in state["models"].values())
    assert all(model["pending"] is None for model in state["models"].values())
