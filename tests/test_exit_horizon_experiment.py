from copy import deepcopy

from exit_horizon_experiment import CAPITAL_TWD, empty_state, update_state


def rows(market: str, session_date: str, close: float = 101.0) -> list[dict]:
    output = []
    for index in range(12):
        output.append({
            "symbol": f"{market}{index}", "name": f"{market}股票{index}",
            "market": market, "type": "個股", "short_term_rank_tier": 2 if index < 6 else 1,
            "short_term_ranking_score": 100 - index, "short_term_score": 100 - index,
            "official_session_date": session_date, "official_open_price": 100,
            "official_high_price": max(101, close), "official_low_price": 99,
            "official_close_price": close, "change_pct": close - 100,
        })
    benchmark = "0050.TW" if market == "TW" else "VOO"
    output.append({
        "symbol": benchmark, "name": benchmark, "market": market, "type": "ETF",
        "official_session_date": session_date, "official_open_price": 100,
        "official_high_price": max(101, close), "official_low_price": 99,
        "official_close_price": close,
    })
    return output


def test_one_cohort_uses_same_entry_and_settles_four_distinct_exits():
    state = empty_state()
    update_state(state, rows("TW", "2026-08-21"), period="evening", updated_at="signal")
    pending = state["markets"]["TW"]["pending"]
    assert pending["signal_session_date"] == "2026-08-21"
    assert len(pending["snapshot_id"]) == 12

    update_state(state, rows("TW", "2026-08-24", 101), period="evening", updated_at="day1")
    market = state["markets"]["TW"]
    assert market["entry_session_date"] == "2026-08-24"
    assert market["horizons"]["1"]["status"] == "complete"
    assert market["horizons"]["2"]["status"] == "waiting"
    assert market["horizons"]["1"]["strict"]["executed_positions"] == 6
    expected = 6 * 100_000 * (1 - 0.685) / 100
    assert market["horizons"]["1"]["strict"]["net_profit_twd"] == round(expected, 2)

    # Repeated reports for one session cannot advance the holding counter.
    update_state(state, rows("TW", "2026-08-24", 109), period="evening", updated_at="repeat")
    assert market["observed_sessions"] == ["2026-08-24"]
    assert market["horizons"]["2"]["status"] == "waiting"

    for number, day in enumerate((25, 26, 27, 28), 2):
        update_state(state, rows("TW", f"2026-08-{day}", 100 + number), period="evening", updated_at=f"day{number}")
    assert market["status"] == "complete"
    assert [market["horizons"][str(value)]["exit_session_date"] for value in (1, 2, 3, 5)] == [
        "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-28"
    ]
    assert all(market["horizons"][str(value)]["strict"]["idle_twd"] == 400_000 for value in (1, 2, 3, 5))


def test_tw_and_us_only_advance_on_their_completed_report_periods():
    state = empty_state()
    update_state(state, rows("TW", "2026-08-21") + rows("US", "2026-08-21"), period="noon", updated_at="noon")
    assert state["status"] == "waiting"
    assert state["markets"]["TW"]["pending"] is None
    assert state["markets"]["US"]["pending"] is None

    update_state(state, rows("TW", "2026-08-21") + rows("US", "2026-08-21"), period="evening", updated_at="evening")
    assert state["markets"]["TW"]["pending"] is not None
    assert state["markets"]["US"]["pending"] is None

    update_state(state, rows("TW", "2026-08-21") + rows("US", "2026-08-21"), period="morning", updated_at="morning")
    assert state["markets"]["US"]["pending"] is not None


def test_entry_waits_for_all_ten_prices_and_never_opens_partial_cohort():
    state = empty_state()
    update_state(state, rows("US", "2026-08-21"), period="morning", updated_at="signal")
    missing = state["markets"]["US"]["pending"]["picks"][0]["symbol"]
    partial = [item for item in rows("US", "2026-08-24") if item["symbol"] != missing]
    update_state(state, partial, period="morning", updated_at="partial")

    market = state["markets"]["US"]
    assert market["entry_session_date"] is None
    assert market["positions"] == []
    assert market["status"] == "waiting_data"
    assert missing in market["pending"]["missing_symbols"]

    update_state(state, rows("US", "2026-08-24"), period="morning", updated_at="retry")
    assert market["entry_session_date"] == "2026-08-24"
    assert len(market["positions"]) == 10
    assert all(position["entry_available"] for position in market["positions"])


def test_legacy_partial_entry_is_quarantined_before_new_snapshot():
    state = empty_state()
    update_state(state, rows("US", "2026-08-21"), period="morning", updated_at="signal")
    update_state(state, rows("US", "2026-08-24"), period="morning", updated_at="entry")
    market = state["markets"]["US"]
    market["positions"][0]["entry_available"] = False
    market["positions"][0]["entry_price"] = None

    update_state(state, rows("US", "2026-08-24"), period="morning", updated_at="migration")
    assert market["entry_session_date"] is None
    assert market["positions"] == []
    assert market["pending"]["signal_session_date"] == "2026-08-24"
    assert market["invalid_entries"][-1]["status"] == "data_insufficient"


def test_intraday_updates_never_change_experiment():
    state = empty_state()
    before = deepcopy(state)
    update_state(state, rows("TW", "2026-08-21"), period="evening", updated_at="live", intraday=True)
    assert state["markets"] == before["markets"]
    assert state["mode"] == "web_shadow_only"
    assert state["policy"]["capital_is_not_stacked"] is True
    assert state["policy"]["capital_per_market_twd"] == CAPITAL_TWD
