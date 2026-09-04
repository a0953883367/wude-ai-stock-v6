import pytest

from prediction_engine.storage import PredictionStore


HORIZONS = {"UP_5D": 5, "UP_45D": 45, "UP_60D": 60, "UP_126D": 126}


def rows(day: str, *, market: str = "US", session: int = 0, split: bool = False):
    result = []
    for index in range(30):
        high = 102 + index / 10
        close = 101 + index / 20
        if index == 20 and session == 3:
            high = 125
            close = 117
        row = {
            "market": market,
            "type": "個股",
            "symbol": f"S{index:02d}",
            "name": f"股票{index:02d}",
            "official_session_date": day,
            "official_open_price": 100,
            "official_high_price": high,
            "official_low_price": 98,
            "official_close_price": close,
            "overall_display_rank": index + 1,
            "overall_rank": index + 1 if index < 20 else None,
            "overall_ranking_score": 90 - index,
            "market_data_quality_score": 95,
            "technical_score": 70,
            "volume_score": 60,
            "institution_score": 50,
            "entry_score": 65,
        }
        if split and index == 0:
            row.update({
                "official_open_price": 51,
                "official_high_price": 55,
                "official_low_price": 50,
                "official_close_price": 54,
                "official_stock_splits": [{"date": day, "ratio": 2}],
            })
        result.append(row)
    benchmark = "0050.TW" if market == "TW" else "VOO"
    result.append({
        "market": market,
        "type": "ETF",
        "symbol": benchmark,
        "name": benchmark,
        "official_session_date": day,
        "official_open_price": 100,
        "official_high_price": 102,
        "official_low_price": 99,
        "official_close_price": 105 if session == 5 else 101,
    })
    return result


def test_forward_ledger_uses_next_open_and_only_matures_after_five_sessions(tmp_path):
    store = PredictionStore(tmp_path / "engine.sqlite3")
    signal = rows("2026-09-01")
    assert store.record_forward_cohort("US", "2026-09-01", signal, "2026-09-01 20:00") == 30

    # Same-session data can never become the entry or answer.
    assert store.advance_forward_outcomes(
        "US", "2026-09-01", signal, "2026-09-01 21:00",
        horizons=HORIZONS, round_trip_cost_pct=0.20,
    ) == 0

    for session in range(1, 6):
        day = f"2026-09-{session + 1:02d}"
        settled = store.advance_forward_outcomes(
            "US", day, rows(day, session=session), f"{day} 20:00",
            horizons=HORIZONS, round_trip_cost_pct=0.20,
        )
        assert settled == (30 if session == 5 else 0)

    summary = store.forward_outcome_summary(HORIZONS)["markets"]["US"]["horizons"]["UP_5D"]
    assert summary["matured_cohorts"] == 1
    assert summary["valid_samples"] == 30
    winner = summary["latest_winners"][0]
    assert winner["symbol"] == "S20"
    assert winner["max_return_pct"] == 25
    assert winner["peak_session_no"] == 3
    assert winner["close_return_pct"] == 2
    assert winner["net_close_return_pct"] == 1.8
    assert winner["benchmark_return_pct"] == 5
    assert winner["excess_return_pct"] == -3
    assert winner["frozen_rank"] == 21
    assert winner["captured_top20"] is False


def test_forward_ledger_adjusts_entry_and_extrema_for_split(tmp_path):
    store = PredictionStore(tmp_path / "engine.sqlite3")
    store.record_forward_cohort("US", "2026-09-01", rows("2026-09-01"), "t0")
    store.advance_forward_outcomes(
        "US", "2026-09-02", rows("2026-09-02", session=1), "t1",
        horizons={"UP_2D": 2}, round_trip_cost_pct=0.20,
    )
    store.advance_forward_outcomes(
        "US", "2026-09-03", rows("2026-09-03", session=2, split=True), "t2",
        horizons={"UP_2D": 2}, round_trip_cost_pct=0.20,
    )
    with store.connect() as db:
        result = db.execute(
            "SELECT * FROM forward_outcome_results WHERE symbol='S00' AND horizon_code='UP_2D'"
        ).fetchone()
    assert result["status"] == "valid"
    assert result["adjusted_entry"] == 50
    assert round(result["max_return_pct"], 6) == 10
    assert round(result["close_return_pct"], 6) == 8


def test_missing_ohlc_is_quarantined_at_maturity(tmp_path):
    store = PredictionStore(tmp_path / "engine.sqlite3")
    store.record_forward_cohort("TW", "2026-09-01", rows("2026-09-01", market="TW"), "t0")
    for session in range(1, 6):
        day = f"2026-09-{session + 1:02d}"
        current = rows(day, market="TW", session=session)
        if session == 2:
            current[5]["official_high_price"] = None
        store.advance_forward_outcomes(
            "TW", day, current, f"t{session}",
            horizons=HORIZONS, round_trip_cost_pct=0.685,
        )
    summary = store.forward_outcome_summary(HORIZONS)["markets"]["TW"]["horizons"]["UP_5D"]
    assert summary["valid_samples"] == 29
    assert summary["incomplete_samples"] == 1


def test_stock_shadow_training_uses_matured_next_open_answer_only(tmp_path):
    store = PredictionStore(tmp_path / "engine.sqlite3")
    signal = rows("2026-09-01")
    store.record_session("US", "2026-09-01", "t0")
    store.record_forward_cohort("US", "2026-09-01", signal, "t0")
    store.insert_predictions([{
        "market": "US", "asset_group": "US_STOCK", "session_date": "2026-09-01",
        "symbol": "S00", "name": "股票00", "horizon_code": "UP_5D",
        "horizon_sessions": 5, "target_side": "UP", "model_version": "test",
        "source_price": 100, "probability_pct": 60, "expected_return_pct": 2,
        "buyability_score": 70, "downside_risk_pct": 4, "data_quality_pct": 95,
        "features": {"trend": 0.7}, "evidence": {}, "created_at": "t0",
    }])
    assert store.training_rows("US", "US_STOCK", "UP_5D") == []
    for session in range(1, 6):
        day = f"2026-09-{session + 1:02d}"
        store.advance_forward_outcomes(
            "US", day, rows(day, session=session), f"t{session}",
            horizons=HORIZONS, round_trip_cost_pct=0.20,
        )
    learned = store.training_rows("US", "US_STOCK", "UP_5D")
    assert len(learned) == 1
    assert learned[0]["features"] == {"trend": 0.7}
    assert learned[0]["realized_return_pct"] == pytest.approx(1)
