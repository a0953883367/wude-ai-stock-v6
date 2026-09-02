import copy
import json

from next_session_ranking import _row_prediction, update_next_session_ranking


def _row(symbol="TEST.TW", market="TW", asset_type="個股", **overrides):
    row = {
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "type": asset_type,
        "industry": "測試產業",
        "theme": "測試",
        "price": 100,
        "ma5": 99,
        "ma10": 98,
        "ma20": 97,
        "ma60": 95,
        "rsi": 60,
        "ma20_distance_pct": 3,
        "change_pct": 0,
        "technical_score": 72,
        "volume_score": 68,
        "market_flow_score": 70,
        "institution_score": 70,
        "positioning_score": 68,
        "group_score": 65,
        "entry_score": 70,
        "tw_sector_context_score": 66,
        "tw_sector_context_available": True,
        "tw_market_context_score": 62,
        "tw_market_context_available": True,
        "macro_score": 62,
        "avg_volume20": 1_000_000,
        "daily_volume_ratio": 1.2,
        "relative_volume": 1.2,
        "attack_volume": 5,
        "news_penalty": 0,
        "news_data_available": True,
        "kline_score": 65,
        "kline_pattern": "多方續強",
        "breakout20": False,
        "breakdown20": False,
        "trade_guard_blocked": False,
        "market_contract_valid": True,
        "market_data_quality_score": 90,
        "institution_available": market == "TW",
        "credit_available": market == "TW",
        "broker_available": market == "TW",
        "us_live_data_available": market == "US",
        "us_short_volume_available": market == "US",
        "us_option_data_available": market == "US",
        "intraday_available": True,
        "extended_hours_available": market == "US",
        "extended_change_pct": 0.2,
        "official_session_date": "2026-09-02",
    }
    row.update(overrides)
    return row


def _decision(symbol, *, source="rotation_shadow", direction="support", strength=80):
    return {
        "symbol": symbol,
        "evidence": [{
            "source_id": source,
            "direction": direction,
            "strength": strength,
            "confidence": 80,
            "as_of": "2026-09-02 20:00:00",
            "status": "collecting_only" if source == "rotation_shadow" else "shadow_only",
            "reason": "同交易日影子證據",
        }],
    }


def test_same_day_gainer_is_not_automatically_promoted():
    flat = _row("FLAT.TW", change_pct=0, technical_score=72)
    # The legacy technical score contains +20 direct points from a +8% day.
    # Removing that direct component makes both forecasts comparable, while
    # the large mover receives only a buyability chase-risk deduction.
    gainer = _row("GAIN.TW", change_pct=8, technical_score=92)
    flat_result = _row_prediction(flat, None, period=None, intraday=False)
    gain_result = _row_prediction(gainer, None, period=None, intraday=False)
    assert gain_result["same_session_change_direct_points_removed"] == 20
    assert gain_result["same_session_change_positive_bonus_points"] == 0
    assert gain_result["same_session_change_forecast_penalty_points"] == 0
    assert gain_result["chase_risk_penalty_points"] >= 8
    assert gain_result["forecast_score"] == flat_result["forecast_score"]
    assert gain_result["buyability_score"] < flat_result["buyability_score"]


def test_independently_confirmed_continuation_may_rank_after_a_rising_day():
    continuation = _row(
        "CONTINUE.TW", change_pct=8, technical_score=100,
        volume_score=92, market_flow_score=90, institution_score=90,
        positioning_score=88, group_score=86, entry_score=82,
        tw_sector_context_score=86, tw_market_context_score=78,
        kline_score=88, breakout20=True, attack_volume=25,
    )
    weak = _row(
        "WEAK.TW", change_pct=8, technical_score=92,
        volume_score=42, market_flow_score=40, institution_score=40,
        positioning_score=42, group_score=42, entry_score=45,
        tw_sector_context_score=42, tw_market_context_score=45,
        kline_score=45,
    )
    continued = _row_prediction(continuation, None, period=None, intraday=False)
    weak_result = _row_prediction(weak, None, period=None, intraday=False)
    assert continued["direction"] == "UP"
    assert continued["continuation_evidence_confirmed"] is True
    assert len(continued["continuation_evidence_reasons"]) >= 2
    assert continued["forecast_score"] > weak_result["forecast_score"]
    assert continued["same_session_change_positive_bonus_points"] == 0
    assert continued["chase_risk_penalty_points"] >= 8


def test_future_outcome_fields_cannot_change_prediction():
    original = _row()
    leaked = copy.deepcopy(original)
    leaked.update({
        "actual_next_return_pct": 99,
        "target_session_close": 999,
        "profit_loss": 1_000_000,
        "day5_return_pct": 88,
        "prediction_correct": True,
    })
    first = _row_prediction(original, None, period=None, intraday=False)
    second = _row_prediction(leaked, None, period=None, intraday=False)
    for field in (
        "forecast_score", "up_probability_estimate_pct", "buyability_score",
        "direction", "evidence_families", "model_votes",
    ):
        assert first[field] == second[field]


def test_full_fixed_universe_keeps_up_flat_down_and_separates_assets(tmp_path):
    rows = [
        _row("UP.TW", change_pct=4, technical_score=88),
        _row("FLAT.TW", change_pct=0),
        _row("DOWN.TW", change_pct=-4, technical_score=56),
        _row("0050.TW", asset_type="ETF"),
        _row("USUP", market="US", change_pct=4, technical_score=88),
        _row("SPY", market="US", asset_type="ETF"),
    ]
    original = copy.deepcopy(rows)
    report = update_next_session_ranking(
        tmp_path, rows, period=None, updated_at="2026-09-02 20:00:00", intraday=False
    )
    assert rows == original
    assert report["summary"]["input_count"] == 6
    assert report["summary"]["group_counts"] == {
        "TW_STOCK": 3, "TW_ETF": 1, "US_STOCK": 1, "US_ETF": 1,
    }
    tw_symbols = {
        item["symbol"] for item in report["groups"]["TW_STOCK"]["forecast"]
    }
    assert tw_symbols == {"UP.TW", "FLAT.TW", "DOWN.TW"}
    assert report["policy"]["gainers_prefilter_forbidden"] is True


def test_shadow_evidence_is_time_aligned_and_bounded():
    row = _row()
    aligned = _row_prediction(
        row, _decision(row["symbol"]), period=None, intraday=False
    )
    wrong_day = _decision(row["symbol"])
    wrong_day["evidence"][0]["as_of"] = "2026-09-01 20:00:00"
    ignored = _row_prediction(row, wrong_day, period=None, intraday=False)
    assert 0 < aligned["shadow_adjustment_points"] <= 2
    assert ignored["shadow_adjustment_points"] == 0


def test_new_history_never_touches_existing_five_or_sixty_day_files(tmp_path):
    protected = {
        "performance.json": b'{"five_day":"keep"}',
        "validation_60d.json": b'{"days":7,"keep":true}',
        "million_simulation.json": b'{"positions":"keep"}',
        "tw_weight_experiment.json": b'{"cohort":"keep"}',
    }
    for filename, value in protected.items():
        (tmp_path / filename).write_bytes(value)
    kwargs = {
        "period": "evening",
        "updated_at": "2026-09-02 20:00:00",
        "intraday": False,
    }
    update_next_session_ranking(tmp_path, [_row()], **kwargs)
    history = json.loads((tmp_path / "next_session_ranking_history.json").read_text())
    assert len(history["records"]) == 1
    first_hash = history["records"][0]["integrity_sha256"]
    # A rerun of the same session cannot rewrite the frozen record.
    update_next_session_ranking(
        tmp_path, [_row(change_pct=9, technical_score=92)],
        period="evening", updated_at="2026-09-02 21:00:00", intraday=False,
    )
    history = json.loads((tmp_path / "next_session_ranking_history.json").read_text())
    assert len(history["records"]) == 1
    assert history["records"][0]["integrity_sha256"] == first_hash
    for filename, value in protected.items():
        assert (tmp_path / filename).read_bytes() == value


def test_intraday_run_waits_and_does_not_create_snapshot(tmp_path):
    report = update_next_session_ranking(
        tmp_path, [_row()], period="evening",
        updated_at="2026-09-02 13:00:00", intraday=True,
    )
    item = report["groups"]["TW_STOCK"]["forecast"][0]
    assert item["direction"] == "WAITING"
    history = json.loads((tmp_path / "next_session_ranking_history.json").read_text())
    assert history["records"] == []
