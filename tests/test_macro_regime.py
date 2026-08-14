import json

from macro_regime import evaluate_macro_regime, update_macro_regime


def _market(vix=18.0):
    return {
        "美元台幣": {"price": 31.9, "change_pct": -0.2},
        "美元指數": {"price": 103.0, "change_pct": 0.1},
        "VIX": {"price": vix, "change_pct": 0.0},
        "美國10年期公債殖利率": {"price": 4.2, "change_pct": 0.1},
    }


def test_high_vix_lowers_macro_score():
    calm = evaluate_macro_regime(_market(14.0))
    stressed = evaluate_macro_regime(_market(32.0))
    assert calm["score"] > stressed["score"]
    assert stressed["data_available"] is True


def test_macro_stays_non_scoring_until_16_valid_days(tmp_path):
    for day in range(1, 16):
        result = update_macro_regime(
            tmp_path, _market(), f"2026-09-{day:02d} 20:00:00", "evening"
        )
        assert result["calibration"]["affects_ai_score"] is False

    result = update_macro_regime(
        tmp_path, _market(), "2026-09-16 20:00:00", "evening"
    )
    assert result["calibration"]["trading_days_collected"] == 16
    assert result["calibration"]["affects_ai_score"] is True


def test_same_date_replaces_snapshot_instead_of_counting_three_reports(tmp_path):
    for period in ("morning", "noon", "evening"):
        result = update_macro_regime(
            tmp_path, _market(), "2026-09-01 20:00:00", period
        )
    assert result["calibration"]["trading_days_collected"] == 1
    payload = json.loads((tmp_path / "macro_history.json").read_text(encoding="utf-8"))
    assert len(payload["snapshots"]) == 1
