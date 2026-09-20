import json
from pathlib import Path

from trade_plan_shadow import build_trade_plan_report, _sell_split


def _sample_row():
    return {
        "symbol": "2330.TW",
        "name": "台積電",
        "market": "TW",
        "asset_type": "個股",
        "industry": "半導體",
        "price": 1000,
        "session_date": "2026-09-20",
        "formal_rank": 1,
        "formal_score": 80,
        "data_quality": "8/8",
        "unresolved_conflict_count": 0,
        "risk_blocks": [],
        "final": {"recommendation": "can_scale", "confidence": 80},
        "horizons": {
            "short": {
                "label": "1～5 日",
                "recommendation": "can_scale",
                "action": "可分批評估",
                "score": 78,
                "confidence": 80,
                "entry_low": 980,
                "entry_high": 1005,
                "stop": 950,
                "target1": 1060,
                "target2": 1120,
                "execution": {"code": "entry_confirm", "label": "進入買進區", "reason": "量價確認"},
            },
            "medium": {
                "label": "45 日",
                "recommendation": "wait_pullback",
                "action": "等待買點",
                "score": 70,
                "confidence": 70,
                "entry_low": 960,
                "entry_high": 990,
                "stop": 930,
                "target1": 1150,
                "target2": 1250,
                "execution": {"code": "no_chase", "label": "高於買進區", "reason": "等回買進區"},
            },
            "long": {
                "label": "6 個月",
                "recommendation": "watch",
                "action": "列入觀察",
                "score": 58,
                "confidence": 60,
                "entry_low": 920,
                "entry_high": 950,
                "stop": 890,
                "target1": 1300,
                "target2": None,
                "execution": {"code": "no_chase", "label": "高於買進區", "reason": "等待"},
            },
        },
        "prediction_engine": {
            "horizons": {
                "UP_5D": {"data_quality_pct": 85, "probability_pct": 70, "expected_return_pct": 3, "downside_risk_pct": 5, "chase_risk_points": 0},
                "UP_45D": {"data_quality_pct": 80, "probability_pct": 68, "expected_return_pct": 8, "downside_risk_pct": 12, "chase_risk_points": 0},
                "UP_126D": {"data_quality_pct": 80, "probability_pct": 65, "expected_return_pct": 15, "downside_risk_pct": 20, "chase_risk_points": 0},
            }
        },
        "next_session_prediction": {"data_quality_pct": 80},
    }


def test_trade_plan_builds_entry_validity_and_staged_exit(tmp_path: Path):
    reports = tmp_path
    (reports / "decision_hub.json").write_text(
        json.dumps({
            "model_version": "CENTRAL-DECISION-HUB-V6",
            "updated_at": "2026-09-20 23:18:23",
            "readiness": {"validation_60d": {"collected_trading_days": 21, "target_trading_days": 60, "ready": False}},
        }),
        encoding="utf-8",
    )
    (reports / "decision_hub_01.json").write_text(
        json.dumps({"decisions": [_sample_row()]}),
        encoding="utf-8",
    )
    report = build_trade_plan_report(reports)
    assert report["policy"]["automatic_orders"] is False
    assert report["policy"]["formal_v6_unchanged"] is True
    assert report["validation"]["trading_days_collected"] == 21
    row = report["plans"][0]
    assert row["preferred_horizon"] == "short"
    short = row["plans"]["short"]
    assert 1 <= short["buy_window_sessions"] <= 3
    assert short["entry_low"] == 980
    assert short["entry_high"] == 1005
    assert short["stop_sell_pct"] == 100
    assert short["target1_pct"] == 25
    assert short["target2_pct"] == 25
    assert short["runner_pct"] == 50
    assert short["reward_risk_1"] is not None


def test_low_confidence_sell_split_is_more_defensive():
    split = _sell_split(55, 50, 120)
    assert split == {"target1_pct": 40, "target2_pct": 40, "runner_pct": 20}
