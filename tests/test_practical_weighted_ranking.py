from __future__ import annotations

import json

from practical_weighted_ranking import WEIGHTS, update_practical_weighted_ranking


def _row(symbol: str, *, technical: float = 80.0) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "market": "TW",
        "type": "個股",
        "official_session_date": "2026-09-25",
        "price": 100.0,
        "overall_rank": 1 if symbol == "GOOD.TW" else 2,
        "overall_ranking_score": 75.0,
        "technical_score": technical,
        "kline_score": technical,
        "mid_long_score": technical,
        "tw_sector_context_score": technical,
        "group_score": technical,
        "volume_score": technical,
        "market_flow_score": technical,
        "fundamental_score": technical,
        "growth_score": technical,
        "financial_quality_score": technical,
        "entry_score": technical,
        "macro_score": technical,
        "market_data_quality_score": 90.0,
        "overall_confidence": 85.0,
    }


def _decision(symbol: str, *, usable_flow: bool = True) -> dict:
    return {
        "symbol": symbol,
        "market": "TW",
        "risk_blocks": [],
        "core_data_missing": [],
        "prediction_engine": {
            "horizons": {
                "UP_10D": {"probability_pct": 78, "buyability_score": 80},
                "UP_21D": {"probability_pct": 75, "buyability_score": 76},
                "UP_63D": {"probability_pct": 72, "buyability_score": 74},
                "UP_126D": {"probability_pct": 70, "buyability_score": 72},
            }
        },
        "horizons": {
            "short": {"execution": {"code": "entry_confirm"}},
            "medium": {"execution": {"code": "entry_wait_confirmation"}},
            "long": {"execution": {"code": "watch"}},
        },
        "evidence": [
            {
                "source_id": "capital_flow_shadow", "direction": "support",
                "strength": 90, "decision_usage_level": "usable" if usable_flow else "limited_reference",
            },
            {
                "source_id": "tw_official_institution", "direction": "support",
                "strength": 80, "decision_usage_level": "usable",
            },
            {
                "source_id": "valuation_shadow", "direction": "oppose",
                "strength": 30, "decision_usage_level": "usable",
            },
            {
                "source_id": "inverse_etf_shadow", "direction": "neutral",
                "strength": 50, "decision_usage_level": "limited_reference",
            },
            {
                "source_id": "verified_news", "direction": "neutral",
                "strength": 50, "decision_usage_level": "usable",
            },
        ],
    }


def test_practical_ranking_builds_four_independent_100_point_lists(tmp_path) -> None:
    rows = [_row("GOOD.TW", technical=85), _row("WEAK.TW", technical=55)]
    decisions = [_decision("GOOD.TW"), _decision("WEAK.TW")]

    report = update_practical_weighted_ranking(
        tmp_path, rows, decisions, period="noon",
        updated_at="2026-09-27 12:00:00", intraday=True,
    )

    assert set(WEIGHTS) == {"10d", "21d", "63d", "126d"}
    assert all(sum(matrix.values()) == 100 for matrix in WEIGHTS.values())
    assert all(len(matrix) == 9 for matrix in WEIGHTS.values())
    assert report["policy"]["horizons_are_never_used_as_weights_in_one_mixed_score"] is True
    assert set(report["rankings"]["TW_STOCK"]) == set(WEIGHTS)
    ranking = report["rankings"]["TW_STOCK"]["10d"]
    assert ranking[0]["symbol"] == "GOOD.TW"
    assert ranking[0]["practical_shadow_score"] > ranking[1]["practical_shadow_score"]
    assert ranking[0]["decision_usage_level"] == "shadow_only"
    assert ranking[0]["formal_v6_unchanged"] is True
    assert report["policy"]["automatic_orders"] is False
    saved = json.loads((tmp_path / "practical_weighted_ranking.json").read_text())
    assert saved["weights_by_horizon"] == WEIGHTS


def test_limited_reference_flow_never_adds_points(tmp_path) -> None:
    row = _row("GOOD.TW")
    usable = update_practical_weighted_ranking(
        tmp_path / "usable", [row], [_decision("GOOD.TW", usable_flow=True)],
        period="noon", updated_at="now", intraday=True,
    )["rankings"]["TW_STOCK"]["10d"][0]
    limited = update_practical_weighted_ranking(
        tmp_path / "limited", [row], [_decision("GOOD.TW", usable_flow=False)],
        period="noon", updated_at="now", intraday=True,
    )["rankings"]["TW_STOCK"]["10d"][0]

    assert usable["components"]["capital_volume_institution"]["score"] > limited["components"]["capital_volume_institution"]["score"]
    assert usable["practical_shadow_score"] > limited["practical_shadow_score"]
