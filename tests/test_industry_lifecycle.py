import copy
import json
import sqlite3

from prediction_engine.engine import run_prediction_engine
from prediction_engine.industry_lifecycle import analyze_industry_lifecycle
from prediction_engine.models import forecast
from prediction_engine.storage import PredictionStore


def stock(**overrides):
    row = {
        "market": "TW",
        "type": "個股",
        "symbol": "2330.TW",
        "name": "測試公司",
        "industry": "先進封裝",
        "official_session_date": "2026-09-04",
        "official_open_price": 99,
        "official_high_price": 102,
        "official_low_price": 98,
        "official_close_price": 100,
        "price": 100,
        "market_contract_valid": True,
        "market_data_quality_score": 95,
        "technical_score": 70,
        "volume_score": 65,
        "fundamental_score": 72,
        "valuation_score": 35,
        "entry_score": 60,
        "news_data_available": True,
        "revenue_yoy_pct": 32,
        "revenue_mom_pct": 8,
        "revenue_date": "2026-08-17",
        "revenue_source": "MOPS via TWSE OpenAPI",
        "valuation_date": "2026-09-03",
        "valuation_source": "TWSE OpenAPI",
    }
    row.update(overrides)
    return row


def test_lifecycle_uses_only_dated_point_in_time_evidence():
    result = analyze_industry_lifecycle(stock(), "2026-09-04")
    assert result["industry_position"] == "先進封裝"
    assert result["stage"] == "成長期"
    assert result["evidence"] == "營收年增 +32.0%、營收月增 +8.0%"
    assert result["risk"] == "估值偏高"
    assert result["judged_at"] == "2026-09-04"
    assert result["status"] == "point_in_time_frozen"
    assert {item["source"] for item in result["sources"]} == {
        "MOPS via TWSE OpenAPI", "TWSE OpenAPI"
    }


def test_future_or_undated_evidence_is_rejected_instead_of_imputed():
    row = stock(
        revenue_date="2026-09-05",
        valuation_date="",
        order_growth_pct=80,
        order_date="",
        order_source="公司新聞稿",
    )
    result = analyze_industry_lifecycle(row, "2026-09-04")
    assert result["stage"] == "資料不足"
    assert result["evidence"] == "資料不足"
    assert result["risk"] == "資料不足"
    assert result["sources"] == []
    assert result["shadow_score"] == 50


def test_stable_60d_champion_does_not_change_before_shadow_validation():
    base = {
        "trend": .7, "volume": .6, "capital_flow": .5, "positioning": .55,
        "sector": .65, "market_regime": .6, "fundamental": .7,
        "valuation": .45, "news": .55, "entry": .6,
        "shadow_consensus": .5, "quality": .95,
    }
    growth = {**base, "industry_lifecycle": .75}
    insufficient = {**base, "industry_lifecycle": .5}
    arguments = {"chase_risk_points": 0, "data_quality_pct": 95, "trade_blocked": False}
    assert forecast(growth, "UP_60D", **arguments) == forecast(insufficient, "UP_60D", **arguments)


def test_existing_forward_snapshot_cannot_be_rewritten(tmp_path):
    store = PredictionStore(tmp_path / "engine.sqlite3")
    first = stock()
    first["_industry_lifecycle"] = analyze_industry_lifecycle(first, "2026-09-04")
    assert store.record_forward_cohort("TW", "2026-09-04", [first], "t0") == 1
    changed = stock(revenue_yoy_pct=-50)
    changed["_industry_lifecycle"] = analyze_industry_lifecycle(changed, "2026-09-04")
    assert store.record_forward_cohort("TW", "2026-09-04", [changed], "t1") == 0
    with store.connect() as db:
        prior = json.loads(db.execute(
            "SELECT prior_json FROM forward_outcome_candidates"
        ).fetchone()[0])
    assert prior["industry_lifecycle"]["stage"] == "成長期"


def test_only_existing_60d_stock_shadow_receives_variable_feature(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    database = tmp_path / "engine.sqlite3"
    before = copy.deepcopy(stock())
    result = run_prediction_engine(
        reports, [before], period="test", updated_at="2026-09-04T12:00:00Z",
        intraday=False, db_path=database,
    )
    assert before.get("_industry_lifecycle") is None
    assert result["policy"]["formal_v6_unchanged"] is True
    assert result["policy"]["automatic_orders"] is False
    with sqlite3.connect(database) as db:
        rows = db.execute(
            "SELECT horizon_code,feature_json,evidence_json FROM predictions"
        ).fetchall()
    by_horizon = {code: (json.loads(features), json.loads(evidence)) for code, features, evidence in rows}
    assert by_horizon["UP_60D"][0]["industry_lifecycle"] == 0.75
    assert by_horizon["UP_60D"][1]["industry_lifecycle"]["stage"] == "成長期"
    assert all(
        values[0]["industry_lifecycle"] == 0.5
        and "industry_lifecycle" not in values[1]
        for code, values in by_horizon.items() if code != "UP_60D"
    )
    chunk = json.loads((reports / "prediction_engine_data_TW_STOCK_UP_60D.json").read_text())
    lifecycle = chunk["predictions"][0]["industry_lifecycle"]
    assert lifecycle["industry_position"] == "先進封裝"
    assert lifecycle["stage"] == "成長期"
    assert lifecycle["formal_v6_unchanged"] is True
    policy = json.loads((reports / "forward_outcome_ledger.json").read_text())["policy"]
    assert policy["industry_lifecycle_60d_only"] is True
    assert policy["industry_lifecycle_preliminary_review_sessions"] == 20
    assert policy["industry_lifecycle_comparison_sessions"] == 60
    assert policy["industry_lifecycle_formal_promotion_requires_owner_approval"] is True


def test_tw_and_us_lifecycle_statistics_remain_separate_and_wait_for_sixty_cohorts(tmp_path):
    store = PredictionStore(tmp_path / "engine.sqlite3")
    signal = stock()
    signal["_industry_lifecycle"] = analyze_industry_lifecycle(signal, "2026-01-01")
    # Align the source dates so this historical test snapshot is valid.
    signal["_industry_lifecycle"] = {
        **signal["_industry_lifecycle"], "stage": "成長期", "status": "point_in_time_frozen"
    }
    store.record_forward_cohort("TW", "2026-01-01", [signal], "t0")
    for session in range(1, 61):
        day = f"2026-{1 + session // 28:02d}-{1 + session % 28:02d}"
        current = stock(
            official_session_date=day,
            official_open_price=100,
            official_high_price=111,
            official_low_price=98,
            official_close_price=110,
        )
        store.advance_forward_outcomes(
            "TW", day, [current], f"t{session}",
            horizons={"UP_60D": 60}, round_trip_cost_pct=0.685,
        )
    summary = store.forward_outcome_summary({"UP_60D": 60})["markets"]
    tw = summary["TW"]["horizons"]["UP_60D"]["industry_lifecycle_validation"]
    us = summary["US"]["horizons"]["UP_60D"]["industry_lifecycle_validation"]
    assert tw["completed_signal_cohorts"] == 1
    assert tw["status"] == "collecting_without_conclusion"
    assert tw["can_compare_shadow_improvement"] is False
    assert tw["stages"]["成長期"]["samples"] == 1
    assert us["completed_signal_cohorts"] == 0
    assert us["stages"] == {}
    assert tw["can_modify_formal_v6"] is False
