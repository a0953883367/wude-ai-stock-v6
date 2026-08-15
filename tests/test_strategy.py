import pandas as pd

from strategy import _available_weighted_score, _candlestick_features, _complete_price_plan, _entry_plan, _etf_score_bundle, _market_flow_score, _market_outlook, _next_day_scenario, _positioning_radar, _short_term_plan, _mid_long_term_plan, build_features, score_candidates


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
    assert result["entry_profile"] == "美股ETF"
    assert result["entry_data_total"] == 4
    assert result["buy_zone_high"] <= 99.2
    assert result["better_buy_high"] < result["buy_zone_low"]


def test_us_flow_uses_finra_and_relative_us_volume_not_tw_institutions():
    score, available, model = _market_flow_score({
        "market": "US", "market_relative_volume": 1.4,
        "change_pct": 2.0, "avg_volume20": 1_000_000,
        "us_short_volume_available": 1, "us_short_volume_ratio_pct": 34,
        "institution_available": 0,
    })
    assert available is True
    assert score > 50
    assert "FINRA" in model


def test_us_complete_plan_declares_us_specific_model():
    plan = _complete_price_plan({
        "market": "US", "type": "個股", "price": 100,
        "atr14": 5, "ma20": 96, "ma60": 90, "avg_volume20": 1_000_000,
    })
    assert plan["price_plan_market_model"] == "美股高波動模型"
    assert plan["buy_zone_high"] < 100


def test_incomplete_company_data_caps_entry_score():
    result = _plan({
        "market": "TW", "price": 2370.0, "atr14": 5.0,
        "institution_available": 0, "fundamental_available": 0,
        "financial_quality_available": 0, "news_data_available": 0,
    })
    assert result["entry_data_coverage"] <= 3
    assert result["entry_score"] <= 75
    assert "資料涵蓋" in result["entry_note"]


def test_price_plan_always_has_all_levels_and_demotes_derived_values():
    row = {
        "market": "TW", "type": "ETF", "price": 106.40,
        "atr14": 1.2, "ma20": 101.59, "ma60": 102.58,
        "avg_volume20": 100_000,
        # Simulate a stale, unreasonable legacy plan.
        "buy_zone_low": 93.50, "buy_zone_high": 93.55,
        "better_buy_low": 0, "better_buy_high": 0,
        "support1": 93.50, "support2": 0,
        "resistance1": 105.76, "resistance2": 0, "stop_price": 0,
    }
    plan = _complete_price_plan(row)
    assert plan["price_plan_complete"] is True
    assert plan["price_plan_quality"] == "部分推估"
    assert plan["price_plan_rank_factor"] < 1
    assert 0 < plan["stop_price"] < plan["better_buy_low"]
    assert plan["better_buy_low"] < plan["better_buy_high"] < plan["buy_zone_low"] < plan["buy_zone_high"] < row["price"]
    assert plan["buy_zone_low"] >= row["price"] * 0.92
    assert plan["support2"] < plan["support1"]
    assert row["price"] < plan["resistance1"] < plan["resistance2"]



def test_short_term_plan_has_trigger_and_bounded_risk():
    row = {
        "market": "TW", "type": "個股", "price": 101.0,
        "ma5": 101.5, "ma10": 100.0, "ma20": 97.0, "atr14": 2.0,
        "rsi": 58.0, "volume_pace": 1.2, "avg_volume20": 1_000_000,
        "attack_volume": 8.0, "institution_available": True,
        "news_data_available": True,
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
        "revenue_yoy_pct": 18.0, "per": 20.0, "institution_available": True,
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


def test_available_weighted_score_penalizes_missing_dimensions_without_neutral_fill():
    complete = _available_weighted_score([(90, 50, True), (70, 50, True)])
    incomplete = _available_weighted_score([(90, 50, True), (70, 50, False)])
    assert complete[0] == 80.0
    assert incomplete[0] < complete[0]
    assert incomplete[1:] == (1, 2, 50.0)


def test_three_rank_fields_are_independent():
    rows = [
        {"score": 90, "short_term_score": 60, "mid_long_score": 70},
        {"score": 70, "short_term_score": 95, "mid_long_score": 80},
    ]
    overall = sorted(rows, key=lambda row: row["score"], reverse=True)
    short = sorted(rows, key=lambda row: row["short_term_score"], reverse=True)
    long = sorted(rows, key=lambda row: row["mid_long_score"], reverse=True)
    assert overall[0] is not short[0]
    assert short[0] is long[0]



def test_etf_uses_independent_score_model_and_blocks_large_premium():
    dates = pd.date_range("2026-01-01", periods=65, freq="B")
    daily = pd.DataFrame({
        "close": [100 + i * 0.1 for i in range(65)],
        "open": [99.8 + i * 0.1 for i in range(65)],
        "high": [100.5 + i * 0.1 for i in range(65)],
        "low": [99.5 + i * 0.1 for i in range(65)],
        "volume": [1_000_000] * 65,
    }, index=dates)
    row = build_features(
        {"symbol": "TEST", "name": "測試ETF", "type": "ETF", "theme": "大盤", "industry": "ETF", "market": "US"},
        daily, None, None,
    )
    row.update({
        "nav_price": row["price"] * 0.96,
        "premium_discount_pct": 4.17,
        "bid_ask_spread_pct": 0.08,
        "expense_ratio_pct": 0.25,
        "aum": 10_000_000_000,
        "etf_return_3y_pct": 12.0,
        "etf_return_5y_pct": 10.0,
        "beta_3y": 1.0,
    })
    result = score_candidates([row])[0]
    assert result["score_model"] == "美股ETF獨立模型"
    assert result["etf_kind"] == "被動ETF"
    assert result["etf_premium_blocked"] is True
    assert result["entry_score"] <= 55
    assert result["short_term_eligible"] is False


def test_tw_etf_uses_institution_participation_as_market_flow():
    row = {
        "market": "TW", "type": "ETF", "avg_volume20": 1_000_000,
        "institution_available": 1, "institution_1d": 100_000,
        "institution_3d": 250_000, "institution_5d": 500_000,
    }
    bundle = _etf_score_bundle(row, technical=70, volume_score=75, news_score=80)
    assert bundle["market"] == "TW"
    assert bundle["flow_available"] is True
    assert bundle["flow_label"] == "法人參與"
    assert bundle["flow_score"] > 50


def test_us_etf_does_not_require_tw_institution_data_for_flow():
    row = {
        "market": "US", "type": "ETF", "avg_volume20": 2_000_000,
        "market_flow_available": True, "market_flow_score": 68,
    }
    bundle = _etf_score_bundle(row, technical=70, volume_score=75, news_score=80)
    assert bundle["market"] == "US"
    assert bundle["flow_available"] is True
    assert bundle["flow_label"] == "美股市場相對資金流"
    assert bundle["flow_score"] == 68


def test_leveraged_etf_is_excluded_from_standard_long_term_plan():
    row = {
        "symbol": "00631L.TW", "name": "元大台灣50正2", "type": "ETF", "market": "TW",
        "price": 100.0, "ma20": 98.0, "ma60": 95.0, "atr14": 2.0,
        "score": 80.0, "technical_score": 80.0, "volume_score": 75.0,
        "better_buy_low": 92.0, "better_buy_high": 96.0,
        "support1": 92.0, "support2": 88.0, "resistance1": 105.0, "resistance2": 112.0,
        "avg_volume20": 1_000_000, "news_penalty": 0,
        "etf_return_3y_pct": 20.0, "etf_return_5y_pct": 15.0,
        "bid_ask_spread_pct": 0.1, "aum": 1_000_000_000,
    }
    result = _mid_long_term_plan(row)
    assert result["mid_long_eligible"] is False
    assert result["mid_long_status"] == "🔴 不列入一般中長線"


def test_market_outlook_separates_bull_bear_and_flat():
    bullish = _market_outlook({
        "market": "TW", "type": "個股", "price": 110, "ma5": 108,
        "ma10": 105, "ma20": 100, "ma60": 95, "rsi": 62,
        "avg_volume20": 1_000_000, "daily_volume_ratio": 1.5,
        "change_pct": 2.0, "attack_volume": 15, "breakout20": True,
        "institution_available": 1, "institution_score": 70,
        "news_data_available": True, "news_penalty": 0,
    })
    bearish = _market_outlook({
        "market": "US", "type": "個股", "price": 90, "ma5": 92,
        "ma10": 95, "ma20": 100, "ma60": 105, "rsi": 38,
        "avg_volume20": 1_000_000, "daily_volume_ratio": 1.6,
        "change_pct": -3.0, "attack_volume": -15, "breakdown20": True,
        "market_flow_available": True, "market_flow_score": 30,
        "news_data_available": True, "news_penalty": 4,
    })
    flat = _market_outlook({"market": "US", "price": 100, "rsi": 50})
    assert bullish["outlook_direction"] == "📈 看漲"
    assert bearish["outlook_direction"] == "📉 看跌"
    assert flat["outlook_direction"] == "↔️ 震盪平盤"
    assert flat["outlook_confidence"] <= 55
