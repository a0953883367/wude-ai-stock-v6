import pandas as pd

from strategy import _candlestick_features, _entry_plan, _next_day_scenario, _positioning_radar, _short_term_plan, _mid_long_term_plan, build_features, score_candidates


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
    assert 0 <= ranked[0]["growth_score"] <= 100
    assert 0 <= ranked[0]["fundamental_score"] <= 100
    assert 0 <= ranked[0]["financial_quality_score"] <= 100
    assert ranked[0]["volume_price_pattern"]
    assert 0 <= ranked[0]["score"] <= 100
    assert ranked[0]["scenario_defense_low"] <= ranked[0]["scenario_defense_high"]
    assert ranked[0]["scenario_no_chase_low"] <= ranked[0]["scenario_no_chase_high"]
    assert "不追" in ranked[0]["scenario_no_chase"]


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


def test_next_day_scenario_never_invents_missing_institution_data():
    row = {
        "market": "TW",
        "price": 75.0,
        "support1": 73.0,
        "support2": 70.0,
        "resistance1": 76.0,
        "resistance2": 80.0,
        "daily_volume_ratio": 2.5,
        "institution_available": False,
        "intraday_available": False,
    }
    result = _next_day_scenario(row)
    assert result["scenario_defense_low"] == 73.0
    assert "法人資料未取得" in result["scenario_basis"]
    assert "待開盤15～30分鐘資料" in result["scenario_continuation"]
    assert result["scenario_data_quality"] == "部分資料"


def test_us_scenario_does_not_use_taiwan_institution_wording():
    row = {
        "market": "US",
        "price": 225.0,
        "support1": 220.0,
        "support2": 210.0,
        "resistance1": 230.0,
        "resistance2": 240.0,
        "daily_volume_ratio": 1.4,
        "institution_available": False,
        "intraday_available": True,
    }
    result = _next_day_scenario(row)
    assert result["scenario_title"] == "🇺🇸 美股下個交易日劇本"
    assert "美股不套用台股三大法人" in result["scenario_basis"]
    assert "美股正式開盤後15～30分鐘" in result["scenario_continuation"]
    assert result["scenario_data_quality"] == "完整"



def test_tw_positioning_radar_uses_tw_disclosures_without_changing_score():
    row = {
        "market": "TW", "change_pct": 3.0, "daily_volume_ratio": 1.8,
        "institution_available": 1, "institution_1d": 100_000,
        "institution_5d": 400_000, "credit_available": 1,
        "short_5d_change": -40_000, "sbl_5d_change": -20_000,
        "margin_5d_change": 0, "avg_volume20": 1_000_000,
        "intraday_available": 1, "opening_attack_15m": 15,
        "opening_attack_30m": 18, "broker_available": 0,
    }
    result = _positioning_radar(row)
    assert result["positioning_score"] >= 68
    assert result["positioning_signal"] == "🔥 多方押注"
    assert result["positioning_affects_ai_score"] is False


def test_us_positioning_radar_labels_short_volume_as_transaction_data():
    row = {
        "market": "US", "change_pct": -3.0, "daily_volume_ratio": 1.7,
        "us_short_volume_available": 1, "us_short_volume_ratio_pct": 62,
        "us_short_volume_date": "2026-08-13", "intraday_available": 1,
        "opening_attack_15m": -18, "opening_attack_30m": -20,
    }
    result = _positioning_radar(row)
    assert result["positioning_score"] <= 32
    assert result["positioning_signal"] == "🔴 放空／賣壓增強"
    assert "≠未回補空單" in result["positioning_disclaimer"]


def test_us_positioning_radar_stays_partial_when_finra_is_missing():
    result = _positioning_radar({
        "market": "US", "change_pct": 0, "daily_volume_ratio": 1,
        "intraday_available": 0,
    })
    assert result["positioning_data_quality"] == "部分資料"
    assert any("不推定空單部位" in text for text in result["positioning_evidence"])


def _plan(row):
    defaults = {
        "price": 100.0, "market": "US", "type": "個股", "atr14": 2.0,
        "rsi": 50, "ma20_distance_pct": 0, "resistance1": 110,
        "volume_pace": 1.0, "avg_volume20": 1_000_000, "ma20": 95,
        "fundamental_available": 1, "financial_quality_available": 1,
        "news_data_available": 1, "us_short_volume_available": 1,
    }
    defaults.update(row)
    return _entry_plan(defaults, 80, 80, 75, 70, 70, 65)


def test_tw_stock_entry_zones_are_separated_and_use_valid_ticks():
    result = _plan({"market": "TW", "price": 2370.0, "atr14": 5.0})
    assert result["entry_profile"] == "台股個股"
    assert result["buy_zone_high"] <= 2370 * 0.99
    assert result["buy_zone_low"] <= result["buy_zone_high"]
    assert result["better_buy_high"] < result["buy_zone_low"]
    assert result["better_buy_low"] <= 2370 * 0.96
    assert all(value % 5 == 0 for value in (
        result["buy_zone_low"], result["buy_zone_high"],
        result["better_buy_low"], result["better_buy_high"],
    ))


def test_us_regular_entry_zones_require_meaningful_pullback():
    result = _plan({"market": "US", "price": 274.48, "atr14": 5.0})
    assert result["entry_profile"] == "美股一般"
    assert result["buy_zone_high"] <= 274.48 * 0.985 + 0.01
    assert result["better_buy_high"] < result["buy_zone_low"]
    assert result["better_buy_low"] <= 274.48 * 0.94 + 0.01


def test_us_high_volatility_stock_uses_wider_zones():
    result = _plan({"market": "US", "price": 40.0, "atr14": 2.4})
    assert result["entry_profile"] == "美股高波動"
    assert result["buy_zone_high"] <= 39.0
    assert result["better_buy_high"] < result["buy_zone_low"]
    assert result["better_buy_low"] <= 36.0


def test_etf_uses_narrower_profile_without_company_fundamentals():
    result = _plan({
        "market": "US", "type": "ETF", "price": 100.0, "atr14": 1.0,
        "fundamental_available": 0, "financial_quality_available": 0,
    })
    assert result["entry_profile"] == "ETF"
    assert result["entry_data_total"] == 4
    assert result["buy_zone_high"] <= 99.2
    assert result["better_buy_high"] < result["buy_zone_low"]


def test_incomplete_company_data_caps_entry_score():
    result = _plan({
        "market": "TW", "price": 2370.0, "atr14": 5.0,
        "institution_available": 0, "fundamental_available": 0,
        "financial_quality_available": 0, "news_data_available": 0,
    })
    assert result["entry_data_coverage"] <= 3
    assert result["entry_score"] <= 75
    assert "資料涵蓋" in result["entry_note"]



def test_short_term_plan_has_trigger_and_bounded_risk():
    row = {
        "market": "TW", "type": "個股", "price": 101.0,
        "ma5": 101.5, "ma10": 100.0, "ma20": 97.0, "atr14": 2.0,
        "rsi": 58.0, "volume_pace": 1.2, "attack_volume": 8.0,
        "entry_score": 82.0, "technical_score": 86.0, "volume_score": 78.0,
        "positioning_score": 70.0, "news_penalty": 0.0,
        "entry_data_coverage": 6, "entry_data_total": 6,
        "buy_zone_low": 98.0, "buy_zone_high": 100.0,
        "support1": 97.0, "resistance1": 105.0, "resistance2": 110.0,
    }
    plan = _short_term_plan(row)
    assert plan["short_term_eligible"] is True
    assert plan["short_term_stop"] < plan["short_term_entry_low"]
    assert plan["short_term_entry_high"] < plan["short_term_target1"] < plan["short_term_target2"]
    assert plan["short_term_rr"] >= 1.5
    assert "15～30分鐘" in plan["short_term_trigger"]


def test_short_term_plan_rejects_incomplete_or_negative_news_rows():
    base = {
        "market": "US", "type": "個股", "price": 100.0,
        "ma5": 101.0, "ma10": 99.0, "ma20": 96.0, "atr14": 2.5,
        "rsi": 56.0, "volume_pace": 1.1,
        "entry_score": 80.0, "technical_score": 84.0, "volume_score": 75.0,
        "positioning_score": 65.0, "buy_zone_low": 97.0, "buy_zone_high": 99.0,
        "support1": 96.0, "resistance1": 105.0, "resistance2": 110.0,
        "entry_data_coverage": 2, "entry_data_total": 6,
    }
    assert _short_term_plan(base)["short_term_status"].startswith("⚪")
    risky = dict(base, entry_data_coverage=6, news_penalty=12.0)
    assert _short_term_plan(risky)["short_term_eligible"] is False



def test_mid_long_plan_uses_fundamentals_and_three_stage_allocation():
    row = {
        "market": "TW", "type": "個股", "price": 105.0,
        "ma20": 102.0, "ma60": 96.0, "atr14": 2.2,
        "score": 80.0, "technical_score": 78.0, "fundamental_score": 82.0,
        "financial_quality_score": 84.0, "growth_score": 76.0,
        "valuation_score": 68.0, "news_penalty": 0.0,
        "fundamental_available": True, "financial_quality_available": True,
        "news_data_available": True, "better_buy_low": 99.0, "better_buy_high": 102.0,
        "support1": 98.0, "support2": 94.0, "resistance1": 110.0,
        "resistance2": 116.0,
    }
    plan = _mid_long_term_plan(row)
    assert plan["mid_long_eligible"] is True
    assert plan["mid_long_stop"] < plan["mid_long_batch1_low"]
    assert plan["mid_long_target1"] < plan["mid_long_target2"]
    assert "40%" in plan["mid_long_allocation"]


def test_mid_long_plan_blocks_incomplete_company_data():
    row = {
        "market": "US", "type": "個股", "price": 100.0,
        "ma20": 98.0, "ma60": 95.0, "atr14": 2.0, "score": 85.0,
        "technical_score": 90.0, "better_buy_low": 96.0, "better_buy_high": 98.0,
        "support1": 95.0, "support2": 92.0,
    }
    plan = _mid_long_term_plan(row)
    assert plan["mid_long_eligible"] is False
    assert plan["mid_long_status"].startswith("⚪")
