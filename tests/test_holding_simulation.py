from holding_simulation import (
    LONG_ALLOCATION_TWD,
    MEDIUM_ALLOCATION_TWD,
    empty_state,
    select_mid_long_picks,
    update_state,
)


def row(index: int, market: str = "TW", session_date: str = "2026-08-21", close_offset: float = 1) -> dict:
    return {
        "symbol": f"{market}{index:02d}",
        "name": f"{market}股票{index}",
        "market": market,
        "type": "個股",
        "score": 80 - index,
        "mid_long_score": 70 + index,
        "mid_long_rank_tier": 2 if index <= 8 else 1,
        "mid_long_ranking_score": 100 - index,
        "mid_long_eligible": index <= 8,
        "official_session_date": session_date,
        "official_open_price": 100 + index,
        "official_close_price": 100 + index + close_offset,
    }


def universe(session_date: str = "2026-08-21", close_offset: float = 1) -> list[dict]:
    rows = [row(index, "TW", session_date, close_offset) for index in range(1, 13)]
    rows += [row(index, "US", session_date, close_offset) for index in range(1, 13)]
    rows += [{**row(20, "TW", session_date), "symbol": "ETF", "type": "ETF"}]
    return rows


def start_state():
    state = empty_state()
    base = universe("2026-08-21")
    update_state(state, base, period="evening", updated_at="2026-08-23 20:00:00")
    update_state(state, base, period="morning", updated_at="2026-08-24 06:00:00")
    return state


def test_medium_selects_top_five_and_long_selects_top_one_per_market():
    rows = universe()
    medium = select_mid_long_picks(rows, "TW", count=5, allocation_twd=MEDIUM_ALLOCATION_TWD)
    long = select_mid_long_picks(rows, "US", count=1, allocation_twd=LONG_ALLOCATION_TWD)

    assert [pick["symbol"] for pick in medium] == ["TW01", "TW02", "TW03", "TW04", "TW05"]
    assert all(pick["allocation_twd"] == 200_000 for pick in medium)
    assert [pick["symbol"] for pick in long] == ["US01"]
    assert long[0]["allocation_twd"] == 500_000


def test_pending_lists_are_frozen_before_monday_and_not_entered_early():
    state = start_state()

    assert state["medium"]["TW"]["status"] == "pending"
    assert state["medium"]["US"]["status"] == "pending"
    assert len(state["medium"]["TW"]["pending"]["picks"]) == 5
    assert len(state["medium"]["US"]["pending"]["picks"]) == 5
    assert len(state["long"]["pending"]["TW"]["picks"]) == 1
    assert len(state["long"]["pending"]["US"]["picks"]) == 1
    assert state["medium"]["TW"]["positions"] == []


def test_next_session_open_enters_medium_and_long_as_separate_accounts():
    state = start_state()
    monday = universe("2026-08-24", close_offset=2)
    update_state(state, monday, period="evening", updated_at="2026-08-24 20:00:00")
    update_state(state, monday, period="morning", updated_at="2026-08-25 06:00:00")

    assert len(state["medium"]["TW"]["positions"]) == 5
    assert len(state["medium"]["US"]["positions"]) == 5
    assert state["medium"]["TW"]["target_exit_date"] == "2026-10-08"
    assert state["medium"]["US"]["target_exit_date"] == "2026-10-08"
    assert len(state["long"]["positions"]) == 2
    assert {position["market"] for position in state["long"]["positions"]} == {"TW", "US"}
    assert all(position["target_exit_date"] == "2027-02-24" for position in state["long"]["positions"])
    # The top stock is intentionally held in both medium and long accounts.
    assert state["medium"]["TW"]["positions"][0]["symbol"] == state["long"]["positions"][0]["symbol"]


def test_medium_exits_after_45_days_while_long_remains_active():
    state = start_state()
    monday = universe("2026-08-24", close_offset=1)
    update_state(state, monday, period="evening", updated_at="2026-08-24 20:00:00")
    update_state(state, monday, period="morning", updated_at="2026-08-25 06:00:00")

    maturity = universe("2026-10-08", close_offset=10)
    update_state(state, maturity, period="evening", updated_at="2026-10-08 20:00:00")
    update_state(state, maturity, period="morning", updated_at="2026-10-09 06:00:00")

    assert state["medium"]["TW"]["status"] == "complete"
    assert state["medium"]["US"]["status"] == "complete"
    assert state["medium"]["TW"]["gross_profit_twd"] > 0
    assert state["medium"]["TW"]["net_profit_twd"] < state["medium"]["TW"]["gross_profit_twd"]
    assert state["medium"]["TW"]["positions"][0]["estimated_cost_twd"] == 1370.0
    assert state["long"]["status"] == "active"
    assert state["long"]["realized"] is False


def test_long_exits_at_six_month_target_and_intraday_never_changes_state():
    state = start_state()
    monday = universe("2026-08-24", close_offset=1)
    update_state(state, monday, period="evening", updated_at="2026-08-24 20:00:00")
    update_state(state, monday, period="morning", updated_at="2026-08-25 06:00:00")
    before = state["long"]["gross_profit_twd"]
    update_state(
        state,
        universe("2026-08-25", close_offset=-10),
        period="evening",
        updated_at="intraday",
        intraday=True,
    )
    assert state["long"]["gross_profit_twd"] == before

    maturity = universe("2027-02-24", close_offset=20)
    update_state(state, maturity, period="evening", updated_at="2027-02-24 20:00:00")
    update_state(state, maturity, period="morning", updated_at="2027-02-25 06:00:00")
    assert state["long"]["status"] == "complete"
    assert state["long"]["realized"] is True
    assert all(position["realized"] for position in state["long"]["positions"])
    assert state["long"]["gross_profit_twd"] > 0
    assert state["long"]["net_profit_twd"] < state["long"]["gross_profit_twd"]
