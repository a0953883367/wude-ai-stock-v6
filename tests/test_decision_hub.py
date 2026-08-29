import copy
import json

import decision_hub
from briefing import _update_decision_hub_safely
from decision_hub import update_decision_hub


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _reports(tmp_path, *, valuation_score=78):
    _write(tmp_path / "valuation_risk_shadow.json", {
        "updated_at": "2026-08-29 20:00:00",
        "coverage": {"TW": {"stocks_total": 1, "stocks_ready": 1}},
        "data": [{
            "symbol": "TEST.TW", "status": "ready", "session_date": "2026-08-29",
            "valuation_pressure_score": valuation_score,
            "valuation_pressure_label": "極高估值壓力",
        }],
        "validation": {"weight_review_ready": False},
    })
    _write(tmp_path / "market_rotation_shadow.json", {
        "updated_at": "2026-08-29 20:00:00",
        "markets": {"TW": {"status": "collecting_only", "snapshots": [{"sectors": [{
            "industry": "測試產業", "rotation_score": 80, "stage_reason": "只累積資料",
        }]}]}, "US": {"status": "waiting", "snapshots": []}},
    })
    _write(tmp_path / "inverse_etf_shadow.json", {
        "updated_at": "2026-08-29 20:00:00",
        "markets": {"TW": {"cohorts": [], "summary": {}}, "US": {"cohorts": [], "summary": {}}},
    })
    _write(tmp_path / "accuracy.json", {
        "updated_at": "2026-08-29 20:00:00",
        "calibration": {"trading_days_collected": 5, "minimum_trading_days": 60,
                        "remaining_trading_days": 55, "ready_for_model_selection": False,
                        "status": "collecting_clean_outcomes"},
    })
    for name in ("holding_simulation", "million_simulation", "system_guard"):
        _write(tmp_path / f"{name}.json", {"updated_at": "2026-08-29 20:00:00"})


def _row(**overrides):
    row = {
        "symbol": "TEST.TW", "name": "測試股", "market": "TW", "type": "個股",
        "industry": "測試產業", "price": 100, "official_session_date": "2026-08-29",
        "rank": 2, "overall_rank": 2, "score": 70, "overall_ranking_score": 70,
        "overall_confidence": 90, "overall_data_quality": "完整",
        "market_contract_valid": True, "trade_guard_blocked": False,
        "market_data_quality_score": 90, "market_data_missing": [],
        "short_term_score": 82, "short_term_confidence": 85, "short_term_eligible": True,
        "short_term_reason": "短線動能偏強", "short_term_entry_low": 98,
        "short_term_entry_high": 100, "short_term_stop": 94, "short_term_target1": 110,
        "mid_long_score": 75, "mid_long_confidence": 85, "mid_long_eligible": True,
        "mid_long_batch1_low": 95, "mid_long_batch1_high": 98,
        "mid_long_batch2_low": 90, "mid_long_batch2_high": 93,
        "mid_long_stop": 85, "mid_long_target1": 115, "mid_long_target2": 130,
        "technical_score": 80, "positioning_score": 75, "financial_quality_score": 72,
        "growth_score": 70, "fundamental_score": 71,
        "news_data_available": True, "news_verified": False, "news_penalty": 0,
        "news_summary": "沒有已驗證重大風險",
    }
    row.update(overrides)
    return row


def test_report_locks_formal_rank_and_explains_horizon_conflict(tmp_path):
    _reports(tmp_path, valuation_score=78)
    rows = [_row()]
    original = copy.deepcopy(rows)
    report = update_decision_hub(
        tmp_path, rows, period="evening", updated_at="2026-08-29 20:00:00", intraday=False
    )
    item = report["decisions"][0]
    assert rows == original
    assert report["policy"]["formal_ranking_locked"] is True
    assert report["policy"]["automatic_orders"] is False
    assert item["formal_rank"] == 2
    assert item["formal_score"] == 70
    assert {key for key in item["horizons"]} == {"short", "medium", "long"}
    assert any(conflict["code"] == "trend_vs_valuation" for conflict in item["conflicts"])
    assert item["horizons"]["short"]["recommendation"] == "can_scale"
    assert item["horizons"]["long"]["recommendation"] != "can_scale"
    assert item["formal_ranking_unchanged"] is True


def test_invalid_market_contract_is_hard_block(tmp_path):
    _reports(tmp_path, valuation_score=30)
    report = update_decision_hub(
        tmp_path, [_row(market_contract_valid=False)], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    item = report["decisions"][0]
    assert item["final"]["recommendation"] == "data_insufficient"
    assert "market_contract" in item["data_missing"]
    assert any(conflict["code"] == "positive_signal_vs_data_block" for conflict in item["conflicts"])


def test_trade_risk_block_is_avoid_not_missing_data(tmp_path):
    _reports(tmp_path, valuation_score=30)
    report = update_decision_hub(
        tmp_path,
        [_row(trade_guard_blocked=True, trade_guard_reason="價格已跌破20日低點")],
        period="evening", updated_at="2026-08-29 20:00:00", intraday=False,
    )
    item = report["decisions"][0]
    assert item["final"]["recommendation"] == "avoid"
    assert item["final"]["reason"] == "價格已跌破20日低點"
    assert item["core_data_missing"] == []
    assert report["summary"]["data_insufficient_count"] == 0
    assert report["summary"]["risk_blocked_count"] == 1
    assert any(
        conflict["code"] == "positive_signal_vs_risk_block"
        for conflict in item["conflicts"]
    )


def test_conflicts_are_reported_as_resolved(tmp_path):
    _reports(tmp_path, valuation_score=78)
    report = update_decision_hub(
        tmp_path, [_row()], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    assert report["summary"]["detected_conflict_count"] == 1
    assert report["summary"]["resolved_conflict_count"] == 1
    assert report["summary"]["unresolved_conflict_count"] == 0


def test_full_universe_news_cache_repairs_optional_news_without_changing_rank(tmp_path):
    _reports(tmp_path, valuation_score=30)
    _write(tmp_path / "news_risk_cache.json", {
        "updated_at": "2026-08-29T20:00:00+00:00",
        "symbols": {"TEST.TW": {
            "news_data_available": True, "news_verified": False,
            "news_penalty": 0, "news_summary": "快取完整掃描",
            "news_scanned_at": "2026-08-29T20:00:00+00:00",
        }},
    })
    item_row = _row(news_data_available=False, news_summary=None)
    report = update_decision_hub(
        tmp_path, [item_row], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    item = report["decisions"][0]
    assert report["summary"]["news_coverage_count"] == 1
    assert "verified_news" not in item["data_missing"]
    assert item["formal_rank"] == item_row["overall_rank"]


def test_missing_shadow_sources_are_explicit_and_not_imputed(tmp_path):
    report = update_decision_hub(
        tmp_path, [_row()], period="evening", updated_at="2026-08-29 20:00:00", intraday=False
    )
    assert report["status"] == "warning"
    assert "valuation" in report["missing_sources"]
    evidence = report["decisions"][0]["evidence"]
    valuation = next(item for item in evidence if item["source_id"] == "valuation_shadow")
    assert valuation["direction"] == "missing"
    assert valuation["affects_decision"] is False
    assert report["policy"]["missing_data_never_imputed"] is True


def test_verified_material_news_overrides_positive_models(tmp_path):
    _reports(tmp_path, valuation_score=30)
    item = update_decision_hub(
        tmp_path, [_row(news_verified=True, news_penalty=8, news_summary="重大事件已驗證")],
        period="evening", updated_at="2026-08-29 20:00:00", intraday=False,
    )["decisions"][0]
    assert item["final"]["recommendation"] == "avoid"
    assert all(plan["recommendation"] == "avoid" for plan in item["horizons"].values())
    assert any(conflict["code"] == "verified_material_risk" for conflict in item["conflicts"])


def test_safe_wrapper_quarantines_failure(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("hub unavailable")

    monkeypatch.setattr(decision_hub, "update_decision_hub", fail)
    success = _update_decision_hub_safely(
        tmp_path, [_row()], period="evening", updated_at="2026-08-29 20:00:00", intraday=False
    )
    health = json.loads((tmp_path / "decision_hub_health.json").read_text(encoding="utf-8"))
    assert success is False
    assert health["formal_pipeline_continues"] is True
    assert health["changes_rankings"] is False
    assert health["places_orders"] is False
