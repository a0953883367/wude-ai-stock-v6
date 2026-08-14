import json
from pathlib import Path

from performance import update_performance


def _row(price: float, action: str = "🟢 可分批") -> dict:
    return {
        "symbol": "2330.TW", "name": "台積電", "market": "TW",
        "score": 80, "action": action, "price": price,
        "backtest_group": "TW", "backtest_rank": 1,
    }


def test_performance_records_and_evaluates_next_business_day(tmp_path: Path):
    update_performance(tmp_path, [_row(100)], [_row(100)], "2026-08-14 20:00:00", "evening")
    summary = update_performance(
        tmp_path, [_row(110)], [_row(110)], "2026-08-17 06:00:00", "morning"
    )
    metric = summary["horizons"]["1"]
    assert metric["samples"] == 1
    assert metric["win_rate_pct"] == 100.0
    assert metric["avg_return_pct"] == 10.0
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    assert history["snapshots"][0]["predictions"][0]["outcomes"]["1"] == 10.0


def test_red_signal_counts_falling_price_as_a_hit(tmp_path: Path):
    update_performance(tmp_path, [_row(100, "🔴 暫不買")], [_row(100)], "2026-08-14 20:00:00", "evening")
    summary = update_performance(
        tmp_path, [_row(90)], [_row(90)], "2026-08-17 06:00:00", "morning"
    )
    assert summary["signals"]["🔴"]["1"]["win_rate_pct"] == 100.0
