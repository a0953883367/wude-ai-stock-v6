import pandas as pd

from strategy import _candlestick_features, build_features, score_candidates


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
        {
            "foreign": 2_000_000, "trust": 100_000, "dealer": -50_000,
            "available": 1, "institution_1d": 2_050_000,
            "institution_3d": 4_000_000, "institution_5d": 6_000_000,
            "institution_10d": 8_000_000,
        },
    )
    row.update({
        "credit_available": 1,
        "margin_5d_change": 100_000,
        "short_5d_change": -20_000,
        "sbl_5d_change": 30_000,
        "fundamental_available": 1,
        "per": 18.5,
        "pbr": 4.2,
        "dividend_yield": 2.5,
        "revenue_yoy_pct": 20.0,
        "revenue_mom_pct": 9.1,
        "financial_quality_available": 1,
        "eps": 2.0,
        "eps_yoy_pct": 33.3,
        "gross_margin_pct": 40.0,
        "operating_margin_pct": 20.0,
        "roe_pct": 8.3,
        "debt_ratio_pct": 40.0,
        "operating_cash_flow_positive": 1,
    })
    ranked = score_candidates([row])
    assert ranked[0]["rank"] == 1
    assert ranked[0]["support1"] <= ranked[0]["resistance2"]
    assert ranked[0]["ma5"] >= ranked[0]["ma10"] >= ranked[0]["ma20"]
    assert ranked[0]["avg_volume5"] > 0
    assert ranked[0]["institution_5d"] == 6_000_000
    assert 0 <= ranked[0]["credit_score"] <= 100
    assert 0 <= ranked[0]["kline_score"] <= 100
    assert 0 <= ranked[0]["valuation_score"] <= 100
    assert 0 <= ranked[0]["fundamental_score"] <= 100
    assert 0 <= ranked[0]["financial_quality_score"] <= 100
    assert ranked[0]["volume_price_pattern"]
    assert 0 <= ranked[0]["score"] <= 100


def test_candlestick_detects_volume_breakout():
    close = pd.Series([100 + i * 0.1 for i in range(20)] + [110.0])
    open_ = close - 0.5
    high = close + 0.2
    low = open_ - 0.2
    volume = pd.Series([1_000_000] * 20 + [2_000_000])
    result = _candlestick_features(open_, high, low, close, volume, 1_000_000)
    assert result["breakout20"] is True
    assert "突破20日高" in result["kline_pattern"]
    assert result["volume_price_pattern"] == "價漲量增"
