import copy
import json

from comprehensive_shadow_ranking import update_comprehensive_shadow_ranking


def _evidence(source, direction, strength, confidence, status="collecting_only"):
    return {
        "source_id": source,
        "direction": direction,
        "strength": strength,
        "confidence": confidence,
        "status": status,
        "reason": source,
    }


def _decision(symbol, market, score, *, session_date="2026-09-01", evidence=None):
    return {
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "asset_type": "個股",
        "industry": "測試",
        "session_date": session_date,
        "formal_rank": 1,
        "formal_score": score,
        "shadow_baseline": {
            "short": {"score": score, "confidence": 80},
            "medium": {"score": score, "confidence": 80},
            "long": {"score": score, "confidence": 80},
        },
        "horizons": {
            key: {"score": score, "confidence": 80}
            for key in ("short", "medium", "long")
        },
        "evidence": evidence or [],
        "core_data_missing": [],
        "risk_blocks": [],
    }


def test_shadow_ranking_uses_bounded_horizon_specific_overlays(tmp_path):
    strong = _decision("STRONG.TW", "TW", 70, evidence=[
        _evidence("rotation_shadow", "support", 90, 80),
        _evidence("capital_flow_shadow", "support", 90, 90),
        _evidence("valuation_shadow", "oppose", 70, 70, "shadow_only"),
        _evidence("inverse_etf_shadow", "neutral", 20, 30),
    ])
    plain = _decision("PLAIN.TW", "TW", 71)
    original = copy.deepcopy([strong, plain])
    report = update_comprehensive_shadow_ranking(
        tmp_path, [strong, plain], period="evening",
        updated_at="2026-09-01 20:00:00", intraday=False,
    )
    assert [strong, plain] == original
    short = report["markets"]["TW"]["horizons"]["short"]
    long = report["markets"]["TW"]["horizons"]["long"]
    strong_short = next(row for row in short if row["symbol"] == "STRONG.TW")
    strong_long = next(row for row in long if row["symbol"] == "STRONG.TW")
    assert strong_short["shadow_rank"] == 1
    assert strong_short["baseline_rank"] == 2
    assert strong_short["rank_change"] == 1
    assert 0 < strong_short["adjustment_points"] <= 8
    assert strong_long["adjustment_points"] < 0
    assert report["policy"]["formal_v6_locked"] is True
    assert report["policy"]["automatic_orders"] is False
    index = json.loads((tmp_path / "comprehensive_shadow_ranking.json").read_text())
    assert "horizons" not in index["markets"]["TW"]
    assert (tmp_path / index["ranking_files"]["TW"]["short"]).exists()


def test_missing_shadow_data_is_not_imputed_or_rewarded(tmp_path):
    report = update_comprehensive_shadow_ranking(
        tmp_path, [_decision("TEST", "US", 60)], period="morning",
        updated_at="2026-09-01 06:00:00", intraday=False,
    )
    row = report["markets"]["US"]["horizons"]["short"][0]
    assert row["shadow_score"] == 60
    assert row["adjustment_points"] == 0
    assert row["shadow_coverage_pct"] == 0
    assert all(item["available"] is False for item in row["adjustments"])


def test_history_freezes_once_and_records_only_closed_market(tmp_path):
    rows = [
        _decision("TW.TW", "TW", 70),
        _decision("US", "US", 70, session_date="2026-08-31"),
    ]
    for _ in range(2):
        update_comprehensive_shadow_ranking(
            tmp_path, rows, period="evening",
            updated_at="2026-09-01 20:00:00", intraday=False,
        )
    history = json.loads((tmp_path / "comprehensive_shadow_history.json").read_text())
    assert len(history["records"]) == 1
    assert history["records"][0]["market"] == "TW"
    assert history["valid_trading_days"] == {"TW": 1, "US": 0}
    assert history["records"][0]["top10"]["short"][0]["symbol"] == "TW.TW"


def test_intraday_never_creates_forward_snapshot(tmp_path):
    update_comprehensive_shadow_ranking(
        tmp_path, [_decision("TW.TW", "TW", 70)], period="evening",
        updated_at="2026-09-01 12:00:00", intraday=True,
    )
    history = json.loads((tmp_path / "comprehensive_shadow_history.json").read_text())
    assert history["records"] == []
