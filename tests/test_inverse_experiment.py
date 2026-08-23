from copy import deepcopy
import json

import pandas as pd

from inverse_experiment import (
    build_inverse_market_rows,
    empty_state,
    load_inverse_experiment_watchlist,
    update_state,
)


def stock_rows(market: str, session_date: str, close: float = 101.0) -> list[dict]:
    return [
        {
            "symbol": f"{market}{index}",
            "name": f"{market}股票{index}",
            "market": market,
            "type": "個股",
            "overall_rank_tier": 2,
            "overall_ranking_score": 100 - index,
            "score": 100 - index,
            "official_session_date": session_date,
            "official_open_price": 100,
            "official_close_price": close,
        }
        for index in range(12)
    ]


def inverse_row(market: str, session_date: str, open_price: float = 100, close: float = 101) -> dict:
    return {
        "market": market,
        "symbol": "00632R.TW" if market == "TW" else "SH",
        "name": "inverse",
        "daily_target": -1,
        "session_date": session_date,
        "open": open_price,
        "close": close,
        "data_complete": True,
    }


def regime(market: str, session_date: str, label: str = "bear", open_price: float = 100, close: float = 99) -> dict:
    return {
        market: {
            session_date: {
                "market": market,
                "session_date": session_date,
                "regime": label,
                "benchmark": "加權指數" if market == "TW" else "S&P 500",
                "benchmark_open": open_price,
                "benchmark_close": close,
                "ma20": 98,
                "ma60": 102,
                "return20_pct": -3,
                "ma20_slope5_pct": -1,
                "classification_rule": "past_only",
            }
        }
    }


def merge_regimes(*items: dict) -> dict:
    output = {"TW": {}, "US": {}}
    for item in items:
        for market, rows in item.items():
            output[market].update(rows)
    return output


def test_watchlist_is_a_dedicated_one_per_market_minus_one_universe():
    watchlist = load_inverse_experiment_watchlist()
    assert {item["symbol"] for item in watchlist} == {"00632R.TW", "SH"}
    assert {item["market"] for item in watchlist} == {"TW", "US"}
    assert all(item["daily_target"] == -1 for item in watchlist)
    payload = json.loads((__import__("pathlib").Path(__file__).parents[1] / "inverse_experiment_watchlist.json").read_text())
    assert {"ALL", "TOP10", "official_watchlist", "V6_ranking"}.issubset(payload["excluded_from"])


def test_history_extractor_uses_exact_session_without_future_or_prior_fill():
    watchlist = load_inverse_experiment_watchlist()
    histories = {
        "00632R.TW": pd.DataFrame(
            {"open": [10, 20], "close": [11, 21]},
            index=pd.to_datetime(["2026-08-21", "2026-08-25"]),
        ),
        "SH": pd.DataFrame(
            {"open": [30], "close": [31]}, index=pd.to_datetime(["2026-08-25"])
        ),
    }
    extracted = build_inverse_market_rows(
        watchlist, histories, {"TW": "2026-08-24", "US": "2026-08-25"}
    )
    assert extracted["TW"]["data_complete"] is False
    assert extracted["TW"]["open"] is None
    assert extracted["US"]["open"] == 30


def test_bear_signal_is_frozen_then_next_session_opens_and_tracks_1_2_3_5():
    state = empty_state()
    signal_rows = stock_rows("TW", "2026-08-21", close=100)
    update_state(
        state, signal_rows, {"TW": inverse_row("TW", "2026-08-21")},
        regime("TW", "2026-08-21"), period="evening", updated_at="signal",
    )
    cohort = state["markets"]["TW"]["cohorts"][0]
    assert cohort["status"] == "pending_entry"
    assert cohort["signal_session_date"] == "2026-08-21"
    frozen_hash = cohort["integrity_sha256"]

    sessions = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]
    all_regimes = merge_regimes(*[regime("TW", day, "sideways", 100, 99 - index) for index, day in enumerate(sessions)])
    for index, day in enumerate(sessions):
        update_state(
            state,
            stock_rows("TW", day, close=101 + index),
            {"TW": inverse_row("TW", day, 100, 102 + index)},
            all_regimes,
            period="evening",
            updated_at=day,
        )

    cohort = state["markets"]["TW"]["cohorts"][0]
    assert cohort["integrity_sha256"] == frozen_hash
    assert cohort["status"] == "complete"
    assert set(cohort["outcomes"]) == {"1", "2", "3", "5"}
    assert all(item["status"] == "valid" for item in cohort["outcomes"].values())
    assert cohort["outcomes"]["1"]["strategies"]["B_CASH"]["net_return_pct"] == 0
    assert cohort["outcomes"]["1"]["strategies"]["C_INVERSE_ETF"]["net_return_pct"] == 1.515
    metrics = state["markets"]["TW"]["summary"]["1"]["C_INVERSE_ETF"]
    assert metrics["valid_samples"] == 1
    assert metrics["win_rate_pct"] == 100
    assert metrics["average_return_pct"] == 1.515
    assert metrics["cumulative_return_pct"] == 1.515
    assert metrics["max_drawdown_pct"] == 0
    assert metrics["average_excess_vs_benchmark_pct"] == 2.515


def test_bull_and_sideways_sessions_do_not_create_cohorts():
    state = empty_state()
    for label in ("bull", "sideways"):
        day = "2026-08-21" if label == "bull" else "2026-08-24"
        update_state(
            state, stock_rows("TW", day), {"TW": inverse_row("TW", day)},
            regime("TW", day, label), period="evening", updated_at=day,
        )
    assert state["markets"]["TW"]["cohorts"] == []


def test_missing_entry_data_quarantines_whole_sample_and_never_counts_it():
    state = empty_state()
    update_state(
        state, stock_rows("TW", "2026-08-21"), {"TW": inverse_row("TW", "2026-08-21")},
        regime("TW", "2026-08-21"), period="evening", updated_at="signal",
    )
    next_rows = stock_rows("TW", "2026-08-24")
    next_rows[0]["official_open_price"] = None
    update_state(
        state, next_rows, {"TW": inverse_row("TW", "2026-08-24")},
        regime("TW", "2026-08-24", "sideways"), period="evening", updated_at="entry",
    )
    cohort = state["markets"]["TW"]["cohorts"][0]
    assert cohort["status"] == "quarantined"
    assert "top10_entry_open:TW0" in cohort["quarantine_reasons"]
    assert state["markets"]["TW"]["summary"]["1"]["A_TOP10_LONG"]["valid_samples"] == 0
    assert state["markets"]["TW"]["summary"]["1"]["A_TOP10_LONG"]["invalid_samples"] == 1


def test_tampered_frozen_signal_and_intraday_runs_cannot_advance():
    state = empty_state()
    update_state(
        state, stock_rows("US", "2026-08-21"), {"US": inverse_row("US", "2026-08-21")},
        regime("US", "2026-08-21"), period="morning", updated_at="signal",
    )
    before = deepcopy(state)
    update_state(
        state, stock_rows("US", "2026-08-24"), {"US": inverse_row("US", "2026-08-24")},
        regime("US", "2026-08-24", "sideways"), period="morning", updated_at="live", intraday=True,
    )
    assert state == before

    state["markets"]["US"]["cohorts"][0]["top10"][0]["symbol"] = "TAMPERED"
    update_state(
        state, stock_rows("US", "2026-08-24"), {"US": inverse_row("US", "2026-08-24")},
        regime("US", "2026-08-24", "sideways"), period="morning", updated_at="close",
    )
    cohort = state["markets"]["US"]["cohorts"][0]
    assert cohort["status"] == "quarantined"
    assert "frozen_snapshot_hash_mismatch" in cohort["quarantine_reasons"]
    assert state["policy"]["broker_orders"] is False
    assert state["policy"]["automatic_merge"] is False
    assert state["policy"]["sixty_day_gate_modified"] is False


def test_markets_only_advance_on_their_own_completed_close_period():
    state = empty_state()
    rows = stock_rows("TW", "2026-08-21") + stock_rows("US", "2026-08-21")
    inverses = {
        "TW": inverse_row("TW", "2026-08-21"),
        "US": inverse_row("US", "2026-08-21"),
    }
    regimes = merge_regimes(regime("TW", "2026-08-21"), regime("US", "2026-08-21"))
    update_state(state, rows, inverses, regimes, period="evening", updated_at="tw-close")
    assert len(state["markets"]["TW"]["cohorts"]) == 1
    assert state["markets"]["US"]["cohorts"] == []
    update_state(state, rows, inverses, regimes, period="morning", updated_at="us-close")
    assert len(state["markets"]["US"]["cohorts"]) == 1
