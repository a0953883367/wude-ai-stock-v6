import pandas as pd

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


def test_only_eight_available_stocks_wait_without_counting_a_day():
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
        assert model["pending"]["settlement_status"] == "waiting_for_official_prices"
        assert model["pending"]["available_positions"] == 8


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
