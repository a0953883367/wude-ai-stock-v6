import pandas as pd

from million_simulation import (
    ALLOCATION_PER_PICK,
    CAPITAL_PER_MARKET,
    empty_state,
    select_picks,
    update_state,
)


def row(index: int, market: str = "TW", session_date: str = "2026-08-21") -> dict:
    return {
        "symbol": f"{market}{index:02d}",
        "name": f"{market}股票{index}",
        "market": market,
        "type": "個股",
        "score": 90 - index,
        "entry_score": 80 - index,
        "overall_rank_tier": 2 if index < 8 else 1,
        "overall_ranking_score": 100 - index,
        "short_term_rank_tier": 2 if index % 2 else 1,
        "short_term_ranking_score": 70 + index,
        "short_term_score": 60 + index,
        "official_session_date": session_date,
        "official_open_price": 100 + index,
        "official_close_price": 101 + index,
    }


def universe(session_date: str = "2026-08-21") -> list[dict]:
    rows = [row(index, "TW", session_date) for index in range(1, 16)]
    rows += [row(index, "US", session_date) for index in range(1, 16)]
    rows += [{**row(index, "TW", session_date), "symbol": f"ETF{index}", "type": "ETF"} for index in range(1, 4)]
    rows += [
        {**row(30, "TW", session_date), "symbol": "0050.TW", "type": "ETF"},
        {**row(31, "US", session_date), "symbol": "VOO", "type": "ETF"},
    ]
    return rows


def history_for(rows: list[dict], session_date: str) -> dict[str, pd.DataFrame]:
    return {
        item["symbol"]: pd.DataFrame(
            {
                "Open": [item["official_open_price"]],
                "High": [item.get("official_high_price", item["official_open_price"])],
                "Low": [item.get("official_low_price", item["official_open_price"])],
                "Close": [item["official_close_price"]],
            },
            index=pd.to_datetime([session_date]),
        )
        for item in rows
    }


def test_selects_exact_top_ten_per_strategy_and_excludes_etfs():
    rows = universe()
    overall = select_picks(rows, "TW", "overall")
    short = select_picks(rows, "TW", "short")

    assert len(overall) == 10
    assert len(short) == 10
    assert all(not pick["symbol"].startswith("ETF") for pick in overall + short)
    assert all(pick["allocation_twd"] == ALLOCATION_PER_PICK for pick in overall + short)
    assert overall[0]["symbol"] == "TW01"
    assert short[0]["symbol"] == "TW15"


def test_tw_and_us_start_only_after_their_completed_report_period():
    state = empty_state()
    rows = universe()

    update_state(state, rows, period="evening", updated_at="2026-08-23 20:00:00")
    assert state["markets"]["TW"]["pending"] is not None
    assert state["markets"]["US"]["pending"] is None

    update_state(state, rows, period="morning", updated_at="2026-08-24 06:00:00")
    assert state["markets"]["US"]["pending"] is not None
    assert state["policy"]["start_date"] == "2026-08-24"


def test_next_session_open_to_close_profit_is_recorded_without_lookahead():
    state = empty_state()
    first = universe("2026-08-21")
    update_state(state, first, period="evening", updated_at="2026-08-23 20:00:00")

    # A repeat report for the same official session must not settle or replace
    # the frozen picks.
    original = state["markets"]["TW"]["pending"]
    update_state(state, first, period="evening", updated_at="2026-08-23 21:00:00")
    assert state["markets"]["TW"]["completed_days"] == 0
    assert state["markets"]["TW"]["pending"] == original

    second = universe("2026-08-24")
    update_state(state, second, period="evening", updated_at="2026-08-24 20:00:00")

    market = state["markets"]["TW"]
    assert market["completed_days"] == 1
    assert len(market["days"]) == 1
    assert market["days"][0]["session_date"] == "2026-08-24"
    assert market["days"][0]["gross_profit_twd"] > 0
    assert market["days"][0]["net_profit_twd"] < market["days"][0]["gross_profit_twd"]
    assert market["cumulative_net_profit_twd"] == market["days"][0]["net_profit_twd"]
    assert market["days"][0]["strategies"]["overall"]["positions"][0]["estimated_cost_twd"] == 342.5
    assert market["days"][0]["strict_portfolio"]["invested_twd"] < CAPITAL_PER_MARKET
    assert market["days"][0]["strict_portfolio"]["idle_twd"] > 0
    assert market["days"][0]["strategies"]["overall"]["invested_twd"] == 500_000
    assert market["days"][0]["benchmark"]["symbol"] == "0050.TW"
    assert market["days"][0]["benchmark"]["data_available"] is True
    assert len(market["days"][0]["ranking_snapshot_id"]) == 12
    assert market["days"][0]["ending_capital_twd"] > CAPITAL_PER_MARKET
    assert market["pending"]["signal_session_date"] == "2026-08-24"


def test_strict_portfolio_does_not_pretend_a_locked_limit_up_stock_was_bought():
    state = empty_state()
    first = universe("2026-08-21")
    update_state(state, first, period="evening", updated_at="start")
    second = universe("2026-08-24")
    selected_symbol = state["markets"]["TW"]["pending"]["strategies"]["overall"][0]["symbol"]
    selected = next(item for item in second if item["symbol"] == selected_symbol)
    selected.update({
        "change_pct": 10.0,
        "official_open_price": 110,
        "official_high_price": 110,
        "official_low_price": 110,
        "official_close_price": 110,
    })
    update_state(state, second, period="evening", updated_at="close")
    position = next(
        item for item in state["markets"]["TW"]["days"][0]["strategies"]["overall"]["positions"]
        if item["symbol"] == selected_symbol
    )
    assert position["data_available"] is True
    assert position["strict_executed"] is False
    assert position["strict_block_reason"] == "鎖漲停買不到"


def test_intraday_refresh_never_starts_or_settles_the_experiment():
    state = empty_state()
    update_state(
        state,
        universe("2026-08-21"),
        period="evening",
        updated_at="2026-08-21 19:30:00",
        intraday=True,
    )
    assert state["markets"]["TW"]["pending"] is None
    assert state["markets"]["TW"]["days"] == []

    update_state(state, universe("2026-08-21"), period="morning", updated_at="signal")
    update_state(
        state, universe("2026-08-24"), period="morning", updated_at="intraday",
        intraday=True,
        price_history=history_for(universe("2026-08-24"), "2026-08-24"),
    )
    assert state["markets"]["US"]["completed_days"] == 0
    assert state["markets"]["US"]["pending"]["signal_session_date"] == "2026-08-21"


def test_missing_frozen_prices_waits_and_never_counts_partial_profit():
    state = empty_state()
    update_state(state, universe("2026-08-21"), period="morning", updated_at="signal")
    current = universe("2026-08-24")
    missing_symbol = state["markets"]["US"]["pending"]["strategies"]["overall"][0]["symbol"]
    current = [item for item in current if item["symbol"] != missing_symbol]

    update_state(state, current, period="morning", updated_at="first close attempt")
    market = state["markets"]["US"]
    assert market["completed_days"] == 0
    assert market["days"] == []
    assert market["status"] == "waiting_data"
    assert market["pending"]["settlement_status"] == "waiting_for_official_prices"
    assert market["pending"]["available_positions"] < 20
    assert missing_symbol in market["pending"]["missing_symbols"]

    update_state(state, universe("2026-08-24"), period="morning", updated_at="retry same close")
    assert market["completed_days"] == 1
    assert market["days"][0]["session_date"] == "2026-08-24"
    assert market["days"][0]["net_profit_twd"] != 0


def test_unresolved_prices_are_quarantined_when_next_session_arrives():
    state = empty_state()
    update_state(state, universe("2026-08-21"), period="morning", updated_at="signal")
    current = universe("2026-08-24")
    missing_symbol = state["markets"]["US"]["pending"]["strategies"]["overall"][0]["symbol"]
    current = [item for item in current if item["symbol"] != missing_symbol]
    update_state(state, current, period="morning", updated_at="incomplete")

    update_state(state, universe("2026-08-25"), period="morning", updated_at="next close")
    market = state["markets"]["US"]
    assert market["completed_days"] == 0
    assert market["days"] == []
    assert market["invalid_days"][-1]["status"] == "data_insufficient"
    assert market["invalid_days"][-1]["session_date"] == "2026-08-24"
    assert missing_symbol in market["invalid_days"][-1]["missing_symbols"]
    assert market["pending"]["signal_session_date"] == "2026-08-25"


def test_exact_historical_prices_settle_frozen_day_after_report_advances():
    state = empty_state()
    update_state(state, universe("2026-08-21"), period="morning", updated_at="signal")
    current = universe("2026-08-24")
    missing_symbol = state["markets"]["US"]["pending"]["strategies"]["overall"][0]["symbol"]
    current = [item for item in current if item["symbol"] != missing_symbol]
    update_state(state, current, period="morning", updated_at="incomplete")

    history = history_for(universe("2026-08-24"), "2026-08-24")
    history[missing_symbol] = pd.DataFrame(
        {"Open": [100.0], "High": [103.0], "Low": [99.0], "Close": [102.0]},
        index=pd.to_datetime(["2026-08-24"]),
    )
    update_state(
        state, universe("2026-08-25"), period="morning", updated_at="next report",
        price_history=history,
    )

    market = state["markets"]["US"]
    assert market["completed_days"] == 1
    assert market["days"][0]["session_date"] == "2026-08-24"
    assert market["invalid_days"] == []
    repaired = next(
        position
        for position in market["days"][0]["strategies"]["overall"]["positions"]
        if position["symbol"] == missing_symbol
    )
    assert repaired["open_price"] == 100.0
    assert repaired["sell_price"] == 102.0


def test_historical_fallback_never_uses_a_different_session():
    state = empty_state()
    update_state(state, universe("2026-08-21"), period="morning", updated_at="signal")
    current = universe("2026-08-24")
    missing_symbol = state["markets"]["US"]["pending"]["strategies"]["overall"][0]["symbol"]
    current = [item for item in current if item["symbol"] != missing_symbol]
    update_state(state, current, period="morning", updated_at="incomplete")

    wrong_date_history = history_for(universe("2026-08-24"), "2026-08-24")
    wrong_date_history[missing_symbol] = pd.DataFrame(
        {"Open": [88.0], "Close": [89.0]},
        index=pd.to_datetime(["2026-08-23"]),
    )
    update_state(
        state, universe("2026-08-25"), period="morning", updated_at="next report",
        price_history=wrong_date_history,
    )

    market = state["markets"]["US"]
    assert market["completed_days"] == 0
    assert market["days"] == []
    assert market["invalid_days"][-1]["session_date"] == "2026-08-24"
    assert missing_symbol in market["invalid_days"][-1]["missing_symbols"]


def test_legacy_partial_day_is_removed_from_totals_and_quarantined():
    state = empty_state()
    update_state(state, universe("2026-08-21"), period="morning", updated_at="signal")
    update_state(state, universe("2026-08-24"), period="morning", updated_at="close")
    market = state["markets"]["US"]
    market["days"][0]["strategies"]["overall"]["positions"][0].update({
        "data_available": False,
        "open_price": None,
        "sell_price": None,
    })
    assert market["completed_days"] == 1

    update_state(state, universe("2026-08-24"), period="morning", updated_at="migration")
    assert market["completed_days"] == 0
    assert market["days"] == []
    assert market["cumulative_net_profit_twd"] == 0
    assert market["invalid_days"][-1]["reason"].startswith("舊版曾在")


def test_experiment_stops_after_five_completed_sessions():
    state = empty_state()
    update_state(state, universe("2026-08-21"), period="evening", updated_at="start")
    for day in range(24, 29):
        update_state(
            state,
            universe(f"2026-08-{day}"),
            period="evening",
            updated_at=f"2026-08-{day} 20:00:00",
        )
    market = state["markets"]["TW"]
    assert market["completed_days"] == 5
    assert market["status"] == "complete"
    assert market["pending"] is None
