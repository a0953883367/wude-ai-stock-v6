import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from tw_signal_confirmation_shadow import update_tw_signal_confirmation_shadow


def _history(trending=True):
    dates = pd.date_range("2026-01-02", periods=180, freq="B")
    close = [100 + index * (0.25 if trending else 0.01 * (-1) ** index) for index in range(180)]
    return pd.DataFrame({
        "open": [value - 0.2 for value in close],
        "high": [value + 0.7 for value in close],
        "low": [value - 0.7 for value in close],
        "close": close,
        "volume": [1_000_000] * 180,
    }, index=dates)


def _row(**overrides):
    row = {
        "symbol": "2330.TW", "market": "TW", "rsi": 52,
        "ma20_slope5_pct": 0.2, "change_pct": 1.2, "attack_volume": 25,
        "tw_above_vwap": True, "tw_breakout_opening_15m": True,
        "tw_intraday_is_current_session": True,
        "tw_intraday_session_date": "2026-09-07",
        "official_session_date": "2026-09-07",
        "official_open_price": 100, "official_close_price": 101,
    }
    row.update(overrides)
    return row


def test_intraday_is_provisional_then_close_confirms_without_rank_changes():
    with TemporaryDirectory() as directory:
        reports = Path(directory)
        row = _row(overall_rank=7, score=88)
        original = dict(row)
        noon = update_tw_signal_confirmation_shadow(
            reports, [row], {"2330.TW": _history()}, period="noon", updated_at="2026-09-07 12:00:00"
        )
        assert noon["signals"][0]["status"] == "provisional"
        assert noon["signals"][0]["intraday_direction"] == "UP"
        evening = update_tw_signal_confirmation_shadow(
            reports, [row], {"2330.TW": _history()}, period="evening", updated_at="2026-09-07 20:00:00"
        )
        assert evening["signals"][0]["status"] == "confirmed"
        assert evening["summary"]["confirmation_rate_pct"] == 100.0
        assert row == original
        assert evening["policy"]["formal_rankings_unchanged"] is True
        assert evening["policy"]["automatic_orders"] is False


def test_close_rejects_wrong_intraday_direction():
    with TemporaryDirectory() as directory:
        reports = Path(directory)
        row = _row()
        update_tw_signal_confirmation_shadow(
            reports, [row], {"2330.TW": _history()}, period="noon", updated_at="2026-09-07 12:00:00"
        )
        row["official_close_price"] = 98
        result = update_tw_signal_confirmation_shadow(
            reports, [row], {"2330.TW": _history()}, period="evening", updated_at="2026-09-07 20:00:00"
        )
        assert result["signals"][0]["status"] == "rejected"
        assert result["signals"][0]["confirmed"] is False


def test_neutral_intraday_observation_is_not_counted_as_a_signal():
    with TemporaryDirectory() as directory:
        reports = Path(directory)
        result = update_tw_signal_confirmation_shadow(
            reports,
            [_row(change_pct=0.1, attack_volume=0, tw_breakout_opening_15m=False)],
            {"2330.TW": _history()},
            period="noon",
            updated_at="2026-09-07 12:00:00",
        )
        assert result["signals"] == []
        assert result["summary"]["tracked_signals"] == 0


def test_kd_and_rsi_diagnostics_are_explainable_and_serializable():
    with TemporaryDirectory() as directory:
        reports = Path(directory)
        result = update_tw_signal_confirmation_shadow(
            reports, [_row()], {"2330.TW": _history(False)}, period="evening", updated_at="2026-09-07 20:00:00"
        )
        diagnostic = result["diagnostics"][0]
        assert diagnostic["kd_resonance"] in {"ALIGNED_UP", "ALIGNED_DOWN", "DIVERGENT", "INSUFFICIENT"}
        assert diagnostic["rsi_regime"] in {"SIDEWAYS", "TREND_UP", "TREND_DOWN", "MIXED"}
        saved = json.loads((reports / "tw_signal_confirmation_shadow.json").read_text(encoding="utf-8"))
        assert saved["rules"]["rsi_regime"].startswith("RSI中性區")
