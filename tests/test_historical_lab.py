from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from historical_lab import build_features, build_report, evaluate_market


def history(start: str, rows: int, slope: float = 0.15, jump_index: int | None = None) -> pd.DataFrame:
    index = pd.bdate_range(start, periods=rows)
    close = 100 + np.arange(rows) * slope + np.sin(np.arange(rows) / 4)
    if jump_index is not None:
        close[jump_index:] *= 1.5
    return pd.DataFrame(
        {
            "Open": close * 0.997,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Adj Close": close,
            "Volume": 1_000_000 + np.arange(rows) * 100,
        },
        index=index,
    )


def universe() -> list[dict[str, str]]:
    rows = []
    for market, suffix in (("TW", ".TW"), ("US", "")):
        for number in range(12):
            rows.append({"symbol": f"T{number}{suffix}" if market == "TW" else f"U{number}", "name": f"{market}{number}", "market": market, "type": "個股"})
    return rows


def all_histories(rows: int = 110) -> dict[str, pd.DataFrame]:
    values = {row["symbol"]: history("2024-01-01", rows, 0.1 + index * 0.005) for index, row in enumerate(universe())}
    values["0050.TW"] = history("2024-01-01", rows, 0.1)
    values["VOO"] = history("2024-01-01", rows, 0.1)
    return values


def test_features_do_not_look_ahead():
    original = history("2024-01-01", 110)
    changed = original.copy()
    changed.iloc[100:, changed.columns.get_loc("Close")] *= 3
    changed.iloc[100:, changed.columns.get_loc("Adj Close")] *= 3
    first = build_features(original).iloc[90]
    second = build_features(changed).iloc[90]
    for column in ("ma20", "ma60", "momentum5", "momentum20", "volume_ratio", "rsi14", "core_score", "eligible"):
        assert first[column] == second[column]


def test_trade_uses_next_session_open_and_close_and_deducts_cost():
    histories = all_histories()
    result = evaluate_market(histories, universe(), "US")
    assert result["status"] == "complete"
    assert result["rank_portfolio"]["evaluated_sessions"] > 0
    recent = result["recent_days"][-1]
    assert recent["trade_date"] > recent["signal_date"]
    assert recent["rank"]["net_profit_twd"] < recent["rank"]["gross_profit_twd"]
    assert round(recent["rank"]["gross_profit_twd"] - recent["rank"]["net_profit_twd"], 2) == 2000.00


def test_strict_portfolio_can_hold_cash():
    histories = all_histories()
    for symbol in [row["symbol"] for row in universe() if row["market"] == "TW"]:
        falling = history("2024-01-01", 110, slope=-0.05)
        histories[symbol] = falling
    result = evaluate_market(histories, universe(), "TW")
    assert result["rank_portfolio"]["average_positions"] == 10
    assert result["strict_portfolio"]["average_positions"] < 10
    assert any(day["strict"]["idle_twd"] > 0 for day in result["recent_days"])


def test_report_is_explicitly_isolated_and_not_exact_v6(tmp_path: Path):
    report = build_report(all_histories(), universe(), tmp_path, "5y")
    assert report["mode"] == "historical_lab_only"
    assert report["is_exact_v6_backtest"] is False
    assert report["official_ranking_affected"] is False
    assert report["official_ledgers_affected"] is False
    assert report["configured_universe_count"] == len(universe())
    assert any("存活者偏差" in note for note in report["limitations"])
    assert report["methodology"]["signal_timing"].startswith("交易日收盤後")
    assert set(report["markets"]) == {"TW_STOCK", "US_STOCK", "TW_ETF", "US_ETF"}


def test_missing_market_data_is_not_treated_as_zero_return():
    result = evaluate_market({"0050.TW": history("2024-01-01", 100)}, universe(), "TW")
    assert result["status"] == "data_insufficient"
    assert result["rank_portfolio"]["evaluated_sessions"] == 0
    assert result["reason"]
