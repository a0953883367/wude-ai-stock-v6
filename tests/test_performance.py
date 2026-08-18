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
    assert summary["calibration"]["affects_ai_score"] is True
    assert summary["calibration"]["eligible_one_day_samples"] == 1
    assert summary["calibration"]["remaining_trading_days"] == 0
    history = json.loads((tmp_path / "prediction_history.json").read_text(encoding="utf-8"))
    assert history["snapshots"][0]["predictions"][0]["outcomes"]["1"] == 10.0


def test_red_signal_counts_falling_price_as_a_hit(tmp_path: Path):
    update_performance(tmp_path, [_row(100, "🔴 暫不買")], [_row(100)], "2026-08-14 20:00:00", "evening")
    summary = update_performance(
        tmp_path, [_row(90)], [_row(90)], "2026-08-17 06:00:00", "morning"
    )
    assert summary["signals"]["🔴"]["1"]["win_rate_pct"] == 100.0


def test_performance_is_split_by_market_group_and_us_feed(tmp_path: Path):
    tw = _row(100)
    us = {
        **_row(200), "symbol": "NVDA", "name": "NVIDIA", "market": "US",
        "backtest_group": "US", "us_live_feed": "sip",
        "us_live_source": "Alpaca SIP", "us_live_data_available": True,
    }
    update_performance(tmp_path, [tw, us], [tw, us], "2026-08-14 20:00:00", "evening")
    tw_next = {**tw, "price": 102}
    us_next = {**us, "price": 210}
    summary = update_performance(
        tmp_path, [tw_next, us_next], [tw_next, us_next],
        "2026-08-17 06:00:00", "morning",
    )
    assert summary["groups"]["TW"]["horizons"]["1"]["samples"] == 1
    assert summary["groups"]["US"]["horizons"]["1"]["samples"] == 1
    assert summary["us_feeds"]["sip"]["horizons"]["1"]["win_rate_pct"] == 100.0
