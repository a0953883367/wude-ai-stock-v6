import pandas as pd

from strategy import build_features, score_candidates


def test_candidate_scoring_has_prices_and_ranking():
    dates = pd.date_range("2026-01-01", periods=65, freq="B")
    daily = pd.DataFrame({
        "close": [100 + i * 0.2 for i in range(65)],
        "open": [99.8 + i * 0.2 for i in range(65)],
        "high": [101 + i * 0.2 for i in range(65)],
        "low": [99 + i * 0.2 for i in range(65)],
        "volume": [1_000_000 + i * 1_000 for i in range(65)],
    }, index=dates)
    intraday = pd.DataFrame({
        "open": [112.8, 113.0, 113.2],
        "close": [113.0, 113.2, 113.4],
        "volume": [100_000, 120_000, 150_000],
    })
    row = build_features(
        {"symbol": "2330.TW", "name": "測試股", "type": "個股", "theme": "半導體", "industry": "晶圓"},
        daily,
        intraday,
        {"foreign": 2_000_000, "trust": 100_000, "dealer": -50_000},
    )
    ranked = score_candidates([row])
    assert ranked[0]["rank"] == 1
    assert ranked[0]["support1"] <= ranked[0]["resistance2"]
    assert 0 <= ranked[0]["score"] <= 100

