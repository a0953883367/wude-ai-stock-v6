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
    _write(tmp_path / "stockq_market_context.json", {
        "updated_at": "2026-08-29 20:00:00", "status": "ok",
        "cache_status": "refreshed", "indicator_count": 16,
        "market_signal": {"score": 44.4, "regime": "中性"},
        "affects_formal_ranking": False,
    })


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
        "institution_available": True, "institution_date": "2026-08-29",
        "institution_source": "TWSE T86", "institution_score": 82,
        "institution_net": 3_700_000, "foreign_net": 3_000_000,
        "trust_net": 500_000, "dealer_net": 200_000,
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
    assert item["horizons"]["short"]["entry_low"] == 98
    assert item["horizons"]["short"]["stop"] == 94
    assert item["horizons"]["short"]["target1"] == 110
    assert item["horizons"]["short"]["execution"]["code"] == "entry_confirm"
    assert any(conflict["code"] == "trend_vs_valuation" for conflict in item["conflicts"])
    assert item["horizons"]["short"]["recommendation"] == "can_scale"
    assert item["horizons"]["long"]["recommendation"] != "can_scale"
    assert item["formal_ranking_unchanged"] is True
    assert item["shadow_baseline"]["short"]["score"] == 82
    assert report["comprehensive_shadow"]["report"] == "comprehensive_shadow_ranking.json"
    shadow = json.loads((tmp_path / "comprehensive_shadow_ranking.json").read_text())
    chunk = json.loads((tmp_path / shadow["ranking_files"]["TW"]["short"]).read_text())
    shadow_item = chunk["rankings"][0]
    assert shadow_item["formal_ranking_unchanged"] is True
    assert shadow_item["places_orders"] is False
    stockq = report["readiness"]["stockq_market_context"]
    assert stockq["status"] == "ok"
    assert stockq["indicator_count"] == 16
    assert stockq["affects_formal_ranking"] is False


def test_inverse_etf_mapping_is_linked_as_visible_shadow_evidence(tmp_path):
    _reports(tmp_path, valuation_score=30)
    _write(tmp_path / "inverse_etf_database.json", {
        "updated_at": "2026-08-29 20:00:00",
        "mappings": [{
            "symbol": "TEST.TW", "market": "TW", "group": "tw_broad",
            "mapping_strength": "broad", "mapping_quality_score": None,
            "mapping_data_status": "waiting_for_aligned_history",
            "inverse_symbol": "00632R.TW", "inverse_name": "元大台灣50反1",
        }],
    })
    _write(tmp_path / "inverse_etf_shadow.json", {
        "updated_at": "2026-08-29 20:00:00",
        "markets": {
            "TW": {
                "cohorts": [],
                "current_candidates": [{
                    "group": "tw_broad", "bear_score": 82,
                    "status": "confirmed", "evidence": {},
                }],
                "summary": {"1": {"samples": 0}},
            },
            "US": {"cohorts": [], "current_candidates": [], "summary": {}},
        },
    })
    report = update_decision_hub(
        tmp_path, [_row()], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    item = report["decisions"][0]
    inverse = next(row for row in item["evidence"] if row["source_id"] == "inverse_etf_shadow")
    assert inverse["direction"] == "oppose"
    assert inverse["status"] == "collecting_only"
    assert inverse["affects_decision"] is False
    assert item["inverse_shadow"]["group"] == "tw_broad"
    assert item["inverse_shadow"]["affects_formal_ranking"] is False


def test_tw_official_institution_is_visible_once_in_central_evidence(tmp_path):
    _reports(tmp_path, valuation_score=30)
    status = {
        "ranking_eligible": True, "ai_eligible": True,
        "session_date": "2026-08-29", "returned_count": 187,
        "expected_count": 188, "coverage_pct": 99.5,
        "minimum_coverage_pct": 95,
    }
    report = update_decision_hub(
        tmp_path, [_row()], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
        institution_status=status,
    )
    item = report["decisions"][0]
    evidence = next(
        row for row in item["evidence"]
        if row["source_id"] == "tw_official_institution"
    )
    assert evidence["direction"] == "support"
    assert evidence["status"] == "linked_formal_once"
    assert evidence["affects_decision"] is True
    assert evidence["provenance"] == "TWSE T86"
    assert "中央不重複加分" in evidence["reason"]
    assert item["institutional_link"]["total_net_shares"] == 3_700_000
    assert item["institutional_link"]["additional_central_adjustment_points"] == 0
    assert report["summary"]["institution_linked_count"] == 1
    readiness = report["readiness"]["tw_institutional"]
    assert readiness["available"] is True
    assert readiness["coverage_pct"] == 99.5


def test_standalone_refresh_derives_tw_institution_coverage_from_rows(tmp_path):
    _reports(tmp_path, valuation_score=30)
    report = update_decision_hub(
        tmp_path, [_row()], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    readiness = report["readiness"]["tw_institutional"]
    source = report["source_status"]["tw_institutional_official"]
    assert readiness["available"] is True
    assert readiness["coverage_pct"] == 100.0
    assert readiness["returned_count"] == 1
    assert source["available"] is True


def test_tw_institution_is_isolated_when_whole_market_coverage_is_too_low(tmp_path):
    _reports(tmp_path, valuation_score=30)
    rows = [
        _row(symbol="READY.TW"),
        _row(
            symbol="MISSING.TW", institution_available=False,
            institution_date=None, institution_source=None,
            institution_net=None,
        ),
    ]
    report = update_decision_hub(
        tmp_path, rows, period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    ready = next(item for item in report["decisions"] if item["symbol"] == "READY.TW")
    assert report["readiness"]["tw_institutional"]["coverage_pct"] == 50.0
    assert report["readiness"]["tw_institutional"]["available"] is False
    assert ready["institutional_link"]["available"] is False
    assert report["summary"]["institution_linked_count"] == 0


def test_us_decision_never_uses_tw_institution_evidence(tmp_path):
    _reports(tmp_path, valuation_score=30)
    report = update_decision_hub(
        tmp_path,
        [_row(symbol="TEST", market="US", institution_available=True)],
        period="evening", updated_at="2026-08-29 20:00:00", intraday=False,
    )
    item = report["decisions"][0]
    link = item["institutional_link"]
    evidence = next(
        row for row in item["evidence"]
        if row["source_id"] == "tw_official_institution"
    )
    assert link["applicable"] is False
    assert link["available"] is False
    assert evidence["status"] == "not_applicable"
    assert evidence["affects_decision"] is False
    assert "tw_official_institution" not in item["data_missing"]
    assert report["summary"]["institution_linked_by_market"] == {"TW": 0, "US": 0}


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
    assert item["horizons"]["short"]["execution"]["code"] == "no_data"


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


def test_completed_quality_flow_links_to_short_decision_without_using_amount(tmp_path):
    _reports(tmp_path, valuation_score=30)
    _write(tmp_path / "capital_flow_daily.json", {
        "updated_at": "2026-08-29T06:00:00+00:00",
        "mode": "closed_session_shadow_only",
        "policy": {"intraday_exposed": False},
        "markets": {"TW": [{
            "market": "TW", "session_date": "2026-08-29", "closed": True,
            "complete": True, "session_scope": "regular_hours_only",
            "source": "Fubon Neo", "ranking_basis": "signal_quality",
            "top_inflows": [{
                "symbol": "TEST.TW", "net_flow": 99_000_000,
                "confidence": 80, "persistence_pct": 70,
                "buy_ratio_pct": 75,
            }],
            "top_outflows": [],
        }], "US": []},
    })
    report = update_decision_hub(
        tmp_path, [_row()], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    item = report["decisions"][0]
    link = item["capital_flow_shadow"]
    evidence = next(
        row for row in item["evidence"]
        if row["source_id"] == "capital_flow_shadow"
    )
    assert link["linked"] is True
    assert link["validated_for_decision"] is True
    assert link["short_adjustment_points"] == 1.5
    assert link["amount_affects_decision"] is False
    assert item["horizons"]["short"]["score"] == 83.5
    assert evidence["affects_decision"] is True
    assert "金額只顯示、不參與加分" in evidence["reason"]
    assert report["summary"]["capital_flow_active_count"] == 1


def test_low_quality_or_wrong_session_flow_never_changes_decision(tmp_path):
    _reports(tmp_path, valuation_score=30)
    _write(tmp_path / "capital_flow_daily.json", {
        "mode": "closed_session_shadow_only",
        "policy": {"intraday_exposed": False},
        "markets": {"TW": [{
            "session_date": "2026-08-29", "closed": True, "complete": True,
            "session_scope": "regular_hours_only", "ranking_basis": "signal_quality",
            "top_inflows": [],
            "top_outflows": [{
                "symbol": "TEST.TW", "net_flow": -900_000_000,
                "confidence": 35, "persistence_pct": 100,
            }],
        }], "US": []},
    })
    item = update_decision_hub(
        tmp_path, [_row()], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )["decisions"][0]
    assert item["capital_flow_shadow"]["linked"] is True
    assert item["capital_flow_shadow"]["validated_for_decision"] is False
    assert item["capital_flow_shadow"]["short_adjustment_points"] == 0
    assert item["horizons"]["short"]["score"] == 82


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
