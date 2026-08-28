import pandas as pd
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy import _apply_tw_accumulation_short_ranking, _assign_group_ranks, _assign_tw_accumulation_ranks, _available_weighted_score, _candlestick_features, _complete_price_plan, _entry_plan, _etf_score_bundle, _market_flow_score, _market_outlook, _next_day_scenario, _positioning_radar, _ranking_sort_key, _short_term_plan, _mid_long_term_plan, _trade_safety_guard, _tw_daily_momentum_features, _tw_head_shoulders_features, _tw_institutional_accumulation, _tw_intraday_momentum_features, apply_tw_buy_candidate_ranking, build_features, market_session_fraction, score_candidates


def test_tw_institutional_accumulation_requires_complete_history_and_is_shadow_only():
    row = {
        "market": "TW", "type": "個股", "institution_available": True,
        "institution_multiday_available": True, "avg_volume20": 1_000_000,
        "trust_5d": 150_000, "foreign_5d": 250_000, "institution_5d": 400_000,
        "trust_buy_days_5": 4, "institution_buy_days_5": 5,
        "price": 100, "ma20": 98, "atr14": 2, "ma20_slope5_pct": 1,
        "recent_low5": 99, "tw_return_5d_pct": 2, "tw_volume_contraction_ratio": .7,
        "breakdown20": False, "kline_pattern": "多方實體",
        "daily_volume_ratio": 1.4, "breakout20": True,
        "tw_breakout_quality_score": 80, "ma20_distance_pct": 2,
    }
    result = _tw_institutional_accumulation(row)
    assert result["tw_accumulation_available"] is True
    assert result["tw_accumulation_score"] >= 80
    assert result["tw_accumulation_strong"] is True
    assert result["tw_accumulation_launch_confirmed"] is True
    assert result["tw_accumulation_affects_score"] is False

    missing = dict(row, trust_buy_days_5=None)
    missing_result = _tw_institutional_accumulation(missing)
    assert missing_result["tw_accumulation_available"] is False
    assert missing_result["tw_accumulation_candidate"] is False

    selling = dict(row, institution_5d=-400_000, institution_buy_days_5=2)
    selling_result = _tw_institutional_accumulation(selling)
    assert selling_result["tw_accumulation_candidate"] is False
    assert "連買未確認" in selling_result["tw_accumulation_status"] or selling_result["tw_accumulation_score"] < 70

    us = _tw_institutional_accumulation({**row, "market": "US"})
    assert us["tw_accumulation_available"] is False


def test_tw_accumulation_changes_only_complete_tw_stock_short_ranking():
    high = {
        "symbol": "HIGH.TW", "market": "TW", "type": "個股",
        "short_term_score": 70, "tw_accumulation_available": True,
        "tw_accumulation_score": 90,
    }
    low = {
        "symbol": "LOW.TW", "market": "TW", "type": "個股",
        "short_term_score": 70, "tw_accumulation_available": True,
        "tw_accumulation_score": 50,
    }
    missing = {
        "symbol": "MISS.TW", "market": "TW", "type": "個股",
        "short_term_score": 70, "tw_accumulation_available": False,
        "tw_accumulation_score": None,
    }
    us = {
        "symbol": "US", "market": "US", "type": "個股",
        "short_term_score": 70, "tw_accumulation_available": True,
        "tw_accumulation_score": 100,
    }
    etf = {
        "symbol": "0050.TW", "market": "TW", "type": "ETF",
        "short_term_score": 70, "tw_accumulation_available": True,
        "tw_accumulation_score": 100,
    }
    for row in (high, low, missing, us, etf):
        _apply_tw_accumulation_short_ranking(row, 1, 1, 1)

    assert high["short_term_ranking_score"] == 74
    assert low["short_term_ranking_score"] == 66
    assert high["short_term_base_score"] == high["short_term_score"] == 70
    assert missing["short_term_ranking_score"] == 70
    assert missing["short_term_accumulation_weight"] == 0
    assert us["short_term_ranking_score"] == 70
    assert etf["short_term_ranking_score"] == 70
    assert not us["short_term_accumulation_affects_ranking"]
    assert not etf["short_term_accumulation_affects_ranking"]


def test_tw_accumulation_threshold_uses_the_same_rounded_score_as_display():
    row = {
        "market": "TW", "type": "個股", "institution_available": True,
        "institution_multiday_available": True, "avg_volume20": 1_000_000,
        "trust_5d": 57_000, "foreign_5d": 57_000, "institution_5d": 171_000,
        "trust_buy_days_5": 3, "institution_buy_days_5": 3,
        "price": 100, "ma20": 99, "atr14": 2, "ma20_slope5_pct": .1,
        "recent_low5": 99, "tw_return_5d_pct": 2,
        "tw_volume_contraction_ratio": .9, "breakdown20": False,
        "kline_pattern": "十字", "daily_volume_ratio": 1,
        "breakout20": False, "tw_breakout_quality_score": 50,
        "ma20_distance_pct": 1,
    }
    result = _tw_institutional_accumulation(row)
    assert result["tw_accumulation_score"] == 70.0
    assert result["tw_accumulation_candidate"] is True


def test_separate_tw_accumulation_ranking_prioritizes_candidate_and_safety():
    candidate = {
        "symbol": "GOOD.TW", "market": "TW", "type": "個股",
        "tw_accumulation_available": True, "tw_accumulation_candidate": True,
        "tw_accumulation_score": 72, "market_contract_valid": True,
    }
    observation = {
        "symbol": "WATCH.TW", "market": "TW", "type": "個股",
        "tw_accumulation_available": True, "tw_accumulation_candidate": False,
        "tw_accumulation_score": 90, "market_contract_valid": True,
    }
    blocked = {
        "symbol": "BLOCK.TW", "market": "TW", "type": "個股",
        "tw_accumulation_available": True, "tw_accumulation_candidate": True,
        "tw_accumulation_score": 99, "trade_guard_blocked": True,
    }
    missing = {
        "symbol": "MISS.TW", "market": "TW", "type": "個股",
        "tw_accumulation_available": False, "tw_accumulation_candidate": False,
    }
    _assign_tw_accumulation_ranks([observation, blocked, missing, candidate])

    assert candidate["tw_accumulation_rank"] == 1
    assert candidate["tw_accumulation_display_rank"] == 1
    assert observation["tw_accumulation_rank"] is None
    assert observation["tw_accumulation_display_rank"] == 2
    assert blocked["tw_accumulation_display_rank"] == 3
    assert missing["tw_accumulation_display_rank"] is None
    assert candidate["tw_accumulation_group_count"] == 3


def test_market_session_fraction_uses_each_markets_clock():
    tw = ZoneInfo("Asia/Taipei")
    ny = ZoneInfo("America/New_York")
    assert market_session_fraction("TW", datetime(2026, 8, 17, 8, 30, tzinfo=tw)) == 0
    assert market_session_fraction("TW", datetime(2026, 8, 17, 13, 30, tzinfo=tw)) == 1
    assert market_session_fraction("US", datetime(2026, 8, 17, 9, 0, tzinfo=ny)) == 0
    assert market_session_fraction("US", datetime(2026, 8, 17, 16, 0, tzinfo=ny)) == 1
    assert 0.49 < market_session_fraction("US", datetime(2026, 8, 17, 12, 45, tzinfo=ny)) < 0.51


def test_us_intraday_volume_is_not_scaled_by_taiwan_session():
    dates = pd.date_range("2026-05-01", periods=30, freq="B")
    daily = pd.DataFrame({
        "close": [100.0] * 30, "open": [99.0] * 30,
        "high": [101.0] * 30, "low": [98.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=dates)
    intraday_index = pd.date_range(
        "2026-06-15 09:30", periods=40, freq="5min", tz="America/New_York"
    )
    intraday = pd.DataFrame({
        "open": [100.0] * 40, "close": [100.1] * 40,
        "volume": [12_500] * 40,
    }, index=intraday_index)
    result = build_features(
        {"symbol": "TEST", "market": "US", "name": "Test", "type": "個股", "theme": "Test"},
        daily, intraday, None,
    )
    assert result is not None
    assert 0.9 <= result["volume_pace"] <= 1.1


def test_build_features_exposes_split_adjusted_official_open_and_close():
    dates = pd.date_range("2026-05-01", periods=30, freq="B")
    daily = pd.DataFrame({
        "close": [100.0] * 30,
        "adj close": [50.0] * 30,
        "open": [99.0] * 30,
        "high": [101.0] * 30,
        "low": [98.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=dates)
    result = build_features(
        {"symbol": "TEST", "market": "US", "name": "Test", "type": "個股", "theme": "Test"},
        daily, None, None,
    )
    assert result is not None
    assert result["official_open_price"] == 99.0
    assert result["official_close_price"] == 100.0
    assert result["official_adjusted_open_price"] == 49.5
    assert result["official_adjusted_close_price"] == 50.0


def test_build_features_never_turns_missing_multiday_institution_history_into_zero():
    dates = pd.date_range("2026-05-01", periods=65, freq="B")
    daily = pd.DataFrame({
        "close": [100.0] * 65, "open": [99.0] * 65,
        "high": [101.0] * 65, "low": [98.0] * 65,
        "volume": [1_000_000] * 65,
    }, index=dates)
    result = build_features(
        {"symbol": "2330.TW", "market": "TW", "name": "測試", "type": "個股", "theme": "半導體"},
        daily, None,
        {"available": 1, "institution_1d": 100_000, "foreign": 80_000, "trust": 20_000},
    )
    assert result is not None
    assert result["institution_5d"] is None
    assert result["institution_multiday_available"] is False


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


def test_tw_volume_contraction_breakout_rewards_only_confirmed_expansion():
    close = pd.Series([100.0+i*.1 for i in range(21)])
    close.iloc[-1] = 105.0
    high, low = close+.1, close-.6
    volume = pd.Series([1_000_000.0]*15+[600_000.0]*5+[1_600_000.0])
    result = _tw_daily_momentum_features(high, low, close, volume)
    assert result["tw_volume_contraction_breakout"] is True
    assert result["tw_breakout_quality_score"] >= 70
    assert "放量" in result["tw_breakout_quality_status"]


def test_tw_false_breakout_is_warning_not_a_trade_guard():
    close = pd.Series([100.0]*20+[105.0, 103.0, 100.0])
    high = pd.Series([101.0]*20+[105.2, 104.0, 101.0])
    low = close-.8
    volume = pd.Series([1_000_000.0]*23)
    result = _tw_daily_momentum_features(high, low, close, volume)
    assert result["tw_false_breakout"] is True
    assert "假突破" in result["tw_breakout_quality_status"]
    guard = _trade_safety_guard({"market": "TW", **result})
    assert guard["trade_guard_blocked"] is False


def test_tw_intraday_vwap_and_opening_range_are_calculated_from_bars():
    frame = pd.DataFrame({
        "high": [100, 101, 102, 102.5, 103, 104, 105],
        "low": [99, 100, 101, 101.5, 102, 103, 104],
        "close": [99.8, 100.8, 101.8, 102.3, 102.8, 103.8, 104.8],
        "volume": [100_000]*7,
    })
    result = _tw_intraday_momentum_features(frame)
    assert result["tw_intraday_momentum_available"] is True
    assert result["tw_above_vwap"] is True
    assert result["tw_breakout_opening_15m"] is True
    assert result["tw_breakout_opening_30m"] is True
    assert result["tw_intraday_momentum_score"] > 70


def test_us_build_features_does_not_receive_taiwan_momentum_fields():
    dates = pd.date_range("2026-05-01", periods=30, freq="B")
    daily = pd.DataFrame({
        "close": [100.0+i*.1 for i in range(30)],
        "open": [99.8+i*.1 for i in range(30)],
        "high": [100.5+i*.1 for i in range(30)],
        "low": [99.5+i*.1 for i in range(30)],
        "volume": [1_000_000]*30,
    }, index=dates)
    result = build_features(
        {"symbol": "TEST", "market": "US", "name": "Test", "type": "個股", "theme": "Test"},
        daily, None, None,
    )
    assert result is not None
    assert "tw_breakout_quality_score" not in result
    assert "tw_intraday_vwap" not in result


def _pattern_series(anchors, length=50):
    values = pd.Series(index=range(length), dtype=float)
    for index, value in anchors:
        values.iloc[index] = value
    return values.interpolate().bfill().ffill()


def test_tw_head_shoulders_requires_neckline_break_and_confirms_volume_and_two_days():
    close = _pattern_series([
        (0, 80), (8, 91), (14, 103), (19, 94), (25, 114),
        (31, 95), (37, 102), (43, 97), (47, 93), (49, 92),
    ])
    high, low = close+1, close-1
    volume = pd.Series([1_000_000.0]*50)
    volume.iloc[47] = 1_600_000
    result = _tw_head_shoulders_features(high, low, close, volume)
    assert result["tw_head_shoulders_pattern"] == "頭肩頂強確認"
    assert result["tw_head_shoulders_top_confirmed"] is True
    assert result["tw_head_shoulders_volume_confirmed"] is True
    assert result["tw_head_shoulders_two_day_confirmed"] is True
    assert result["tw_head_shoulders_top_strong"] is True


def test_tw_head_shoulders_stays_warning_before_neckline_break():
    close = _pattern_series([
        (0, 80), (8, 91), (14, 103), (19, 94), (25, 114),
        (31, 95), (37, 102), (43, 99), (49, 98),
    ])
    result = _tw_head_shoulders_features(close+1, close-1, close, pd.Series([1_000_000.0]*50))
    assert result["tw_head_shoulders_pattern"] == "疑似頭肩頂"
    assert result["tw_head_shoulders_top_suspected"] is True
    assert result["tw_head_shoulders_top_confirmed"] is False


def test_tw_head_shoulders_releases_after_fast_reclaim_holds_two_days():
    close = _pattern_series([
        (0, 80), (8, 91), (14, 103), (19, 94), (25, 114),
        (31, 95), (37, 102), (43, 93), (47, 98), (49, 99),
    ])
    result = _tw_head_shoulders_features(close+1, close-1, close, pd.Series([1_000_000.0]*50))
    assert result["tw_head_shoulders_pattern"] == "頭肩頂假跌破解除"
    assert result["tw_head_shoulders_top_recovered"] is True
    assert result["tw_head_shoulders_top_confirmed"] is False


def test_tw_inverse_head_shoulders_confirms_only_after_close_above_neckline():
    close = _pattern_series([
        (0, 120), (8, 108), (14, 96), (19, 105), (25, 84),
        (31, 104), (37, 97), (43, 105), (47, 108), (49, 110),
    ])
    volume = pd.Series([1_000_000.0]*50)
    volume.iloc[43] = 1_600_000
    result = _tw_head_shoulders_features(close+1, close-1, close, volume)
    assert result["tw_head_shoulders_pattern"] == "頭肩底確認"
    assert result["tw_inverse_head_shoulders_confirmed"] is True


def test_head_shoulders_strong_guard_is_taiwan_only():
    tw = _trade_safety_guard({"market": "TW", "tw_head_shoulders_top_strong": True})
    us = _trade_safety_guard({"market": "US", "tw_head_shoulders_top_strong": True})
    assert tw["trade_guard_blocked"] is True
    assert tw["trade_guard_severe"] is True
    assert "頭肩頂" in tw["trade_guard_reason"]
    assert us["trade_guard_blocked"] is False


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
    assert "待台股09:00開盤後15～30分鐘資料" in result["scenario_continuation"]
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
        "extended_hours_available": True,
        "extended_session": "盤後",
        "extended_change_pct": 0.2,
    }
    result = _next_day_scenario(row)
    assert result["scenario_title"] == "🇺🇸 美股下個交易日劇本"
    assert "美股不套用台股三大法人" in result["scenario_basis"]
    assert "美股正式開盤後15～30分鐘" in result["scenario_continuation"]
    assert result["scenario_data_quality"] == "完整"


def test_gap_up_scenario_uses_dynamic_zone_instead_of_distant_rolling_low():
    row = {
        "market": "US",
        "price": 464.98,
        "support1": 388.11,
        "support2": 375.51,
        "resistance1": 478.67,
        "resistance2": 492.36,
        "buy_zone_low": 450.61,
        "buy_zone_high": 458.01,
        "better_buy_low": 437.08,
        "price_plan_quality": "完整",
        "daily_volume_ratio": 2.26,
        "intraday_available": True,
        "extended_hours_available": True,
        "extended_session": "盤後",
        "extended_change_pct": -0.75,
    }

    result = _next_day_scenario(row)

    assert result["scenario_defense_low"] == 450.61
    assert result["scenario_defense_high"] == 458.01
    assert result["scenario_breakdown"] == 437.08
    assert result["scenario_structural_support1"] == 388.11
    assert result["scenario_level_source"] == "ATR動態短線區"
    assert "守住 450.61～458.01" in result["scenario_continuation"]
    assert "跌破 437.08 視為轉弱" in result["scenario_breakdown_text"]
    assert "防守採ATR動態短線區" in result["scenario_basis"]
    assert result["scenario_data_quality"] == "完整"


def test_us_scenario_without_extended_hours_is_not_labeled_complete():
    result = _next_day_scenario({
        "market": "US", "price": 100,
        "support1": 96, "support2": 92,
        "resistance1": 104, "resistance2": 108,
        "daily_volume_ratio": 1.1,
        "intraday_available": True,
    })

    assert result["scenario_data_quality"] == "部分資料"



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


def test_tw_margin_and_exchange_short_lots_are_converted_before_volume_comparison():
    result = _positioning_radar({
        "market": "TW", "change_pct": -1.0, "daily_volume_ratio": 1.0,
        "credit_available": 1, "avg_volume20": 1_000_000,
        "short_5d_change": 3_000,  # lots = 3,000,000 shares
        "sbl_5d_change": 0, "margin_5d_change": 0,
    })
    assert result["positioning_score"] <= 42
    assert any("3,000,000 股" in text for text in result["positioning_evidence"])


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
        "attack_volume": 15.0, "institution_available": True,
        "news_data_available": True,
        "entry_score": 88.0, "technical_score": 92.0, "volume_score": 88.0,
        "positioning_score": 82.0, "news_penalty": 0.0,
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


def test_short_term_target_rounding_cannot_loop_forever(monkeypatch):
    import strategy

    row = {
        "market": "US", "type": "個股", "price": 101.0,
        "ma5": 101.5, "ma10": 100.0, "ma20": 97.0, "atr14": 2.0,
        "rsi": 58.0, "volume_pace": 1.2, "avg_volume20": 1_000_000,
        "attack_volume": 15.0, "intraday_available": True,
        "news_data_available": True,
        "entry_score": 88.0, "technical_score": 92.0, "volume_score": 88.0,
        "positioning_score": 82.0, "news_penalty": 0.0,
        "entry_data_coverage": 6, "entry_data_total": 6,
        "buy_zone_low": 98.0, "buy_zone_high": 100.0,
        "support1": 97.0, "resistance1": 100.0, "resistance2": 100.0,
    }
    # Simulate a price formatter that cannot advance by another tick.  The
    # previous unbounded while-loop would never return in this situation.
    monkeypatch.setattr(strategy, "_market_price", lambda _row, _value: 100.0)
    plan = strategy._short_term_plan(row)
    assert plan["short_term_target1"] == 100.0


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


def test_tw_material_institutional_selling_blocks_short_term_green_label():
    row = {
        "market": "TW", "type": "個股", "avg_volume20": 34_597_099,
        "institution_available": True, "institution_1d": -2_630_862,
        "institution_3d": -4_000_000, "institution_5d": -10_766_742,
        "margin_1d_change": -387,
    }
    guard = _trade_safety_guard(row)
    assert guard["trade_guard_blocked"] is True
    assert guard["institution_flow_ratio_1d_pct"] < -5
    assert "法人單日賣超" in guard["trade_guard_reason"]
    assert "融資明顯增加" not in guard["trade_guard_reason"]


def test_us_bearish_live_flow_or_risk_blocks_positive_trade_label():
    guard = _trade_safety_guard({
        "market": "US", "type": "ETF", "avg_volume20": 10_000_000,
        "market_flow_available": True, "market_flow_score": 32,
        "us_live_data_available": True,
        "us_live_quote_imbalance_pct": -28,
        "us_live_vwap_distance_pct": -1.2,
        "us_market_risk_score": 38,
    })
    assert guard["trade_guard_blocked"] is True
    assert "VWAP下方" in guard["trade_guard_reason"]


def test_short_term_green_requires_complete_market_appropriate_inputs():
    row = {
        "market": "TW", "type": "ETF", "price": 101.0,
        "ma5": 101.5, "ma10": 100.0, "ma20": 97.0, "atr14": 2.0,
        "rsi": 58.0, "volume_pace": 1.2, "avg_volume20": 1_000_000,
        "attack_volume": 8.0, "institution_available": True,
        "entry_score": 90.0, "technical_score": 90.0, "volume_score": 90.0,
        "positioning_score": 80.0, "news_penalty": 0.0,
        "entry_data_coverage": 3, "entry_data_total": 4,
        "buy_zone_low": 98.0, "buy_zone_high": 100.0,
        "support1": 97.0, "resistance1": 105.0, "resistance2": 110.0,
        "premium_discount_pct": 0.1,
    }
    plan = _short_term_plan(row)
    assert plan["short_term_eligible"] is False
    assert plan["short_term_status"].startswith("⚪")
    assert plan["short_term_score"] < 75



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


def test_all_rankings_put_qualified_rows_before_high_scoring_blocked_rows():
    safe = {
        "symbol": "SAFE.TW", "market": "TW", "type": "個股",
        "overall_rank_tier": 2, "overall_ranking_score": 65,
        "short_term_rank_tier": 2, "short_term_ranking_score": 62,
        "mid_long_rank_tier": 2, "mid_long_ranking_score": 64,
        "score": 65, "entry_score": 65,
        "short_term_score": 62, "mid_long_score": 64,
    }
    blocked = {
        "symbol": "BLOCK.TW", "market": "TW", "type": "個股",
        "overall_rank_tier": 0, "overall_ranking_score": 99,
        "short_term_rank_tier": 0, "short_term_ranking_score": 99,
        "mid_long_rank_tier": 0, "mid_long_ranking_score": 99,
        "score": 99, "entry_score": 99,
        "short_term_score": 99, "mid_long_score": 99,
    }
    for horizon in ("overall", "short", "long"):
        ordered = sorted([blocked, safe], key=lambda row: _ranking_sort_key(row, horizon), reverse=True)
        assert ordered[0]["symbol"] == "SAFE.TW"


def test_rank_numbers_restart_inside_tw_us_and_combined_etf_groups():
    rows = [
        {"symbol": "2330.TW", "market": "TW", "type": "個股"},
        {"symbol": "NVDA", "market": "US", "type": "個股"},
        {"symbol": "0050.TW", "market": "TW", "type": "ETF"},
        {"symbol": "VOO", "market": "US", "type": "ETF"},
    ]
    for index, row in enumerate(rows):
        row.update({
            "overall_rank_tier": 2, "overall_ranking_score": 80-index,
            "short_term_rank_tier": 2, "short_term_ranking_score": 80-index,
            "mid_long_rank_tier": 2, "mid_long_ranking_score": 80-index,
        })
    _assign_group_ranks(rows)
    assert rows[0]["overall_rank"] == rows[1]["overall_rank"] == 1
    assert rows[2]["overall_rank"] == 1
    assert rows[3]["overall_rank"] == 2


def test_observation_and_blocked_rows_have_no_numeric_rank():
    rows = [
        {
            "symbol": "SAFE.TW", "market": "TW", "type": "個股",
            "overall_rank_tier": 2, "overall_ranking_score": 60,
            "short_term_rank_tier": 2, "short_term_ranking_score": 60,
            "mid_long_rank_tier": 2, "mid_long_ranking_score": 60,
        },
        {
            "symbol": "WATCH.TW", "market": "TW", "type": "個股",
            "overall_rank_tier": 1, "overall_ranking_score": 99,
            "short_term_rank_tier": 1, "short_term_ranking_score": 99,
            "mid_long_rank_tier": 1, "mid_long_ranking_score": 99,
        },
        {
            "symbol": "BLOCK.TW", "market": "TW", "type": "個股",
            "overall_rank_tier": 0, "overall_ranking_score": 100,
            "short_term_rank_tier": 0, "short_term_ranking_score": 100,
            "mid_long_rank_tier": 0, "mid_long_ranking_score": 100,
        },
    ]

    _assign_group_ranks(rows)

    assert rows[0]["overall_rank"] == 1
    assert [row["overall_display_rank"] for row in rows] == [1, 2, 3]
    assert [row["short_term_display_rank"] for row in rows] == [1, 2, 3]
    assert [row["mid_long_display_rank"] for row in rows] == [1, 2, 3]
    assert all(row["ranking_group_count"] == 3 for row in rows)
    for row in rows[1:]:
        assert row["overall_rank"] is None
        assert row["short_term_rank"] is None
        assert row["mid_long_rank"] is None


def test_tw_buy_ranking_separates_setup_trigger_and_live_confirmation():
    ready = {
        "symbol": "READY.TW", "market": "TW", "type": "個股",
        "price": 100, "short_term_entry_low": 99, "short_term_entry_high": 101,
        "short_term_stop": 96, "short_term_target1": 108, "short_term_rr": 2,
        "short_term_eligible": True, "short_term_score": 72,
        "entry_score": 65, "technical_score": 68, "score": 66,
        "market_data_quality_score": 90,
        "entry_data_coverage": 6, "entry_data_total": 6,
        "daily_volume_ratio": .9, "rsi": 58, "breakdown20": False,
        "news_penalty": 0,
        "next_session_direction": "📈 看漲", "next_session_confidence": 70,
        "next_session_data_quality": 90, "next_session_signal_level": "RESEARCH",
        "market_contract_valid": True, "trade_guard_blocked": False,
        "overall_eligible": True,
        "short_term_rank_tier": 2, "short_term_ranking_score": 82,
        "mid_long_rank_tier": 1, "mid_long_ranking_score": 70,
    }
    outside_zone = dict(ready, symbol="WAIT.TW", price=103, score=90)
    abstain = dict(
        ready, symbol="ABSTAIN.TW", score=95,
        next_session_direction="⚪ 棄權", next_session_signal_level="ABSTAIN",
    )

    ranked = apply_tw_buy_candidate_ranking([outside_zone, abstain, ready])

    assert ranked[0]["symbol"] == "WAIT.TW"
    assert ready["buy_candidate_eligible"] is True
    assert ready["buy_setup_eligible"] is True
    assert ready["buy_trigger_ready"] is True
    assert ready["live_buy_required"] is True
    assert outside_zone["buy_setup_eligible"] is True
    assert outside_zone["buy_trigger_ready"] is False
    assert outside_zone["overall_rank"] is not None
    assert abstain["buy_setup_eligible"] is True
    assert abstain["buy_trigger_ready"] is False
    assert abstain["overall_rank"] is not None
    assert "等待回測買進區" in outside_zone["buy_candidate_status"]
    assert "等待隔日方向確認" in abstain["buy_candidate_status"]


def test_tw_intraday_momentum_delays_trigger_but_never_removes_setup_candidate():
    base = {
        "symbol": "MOMENTUM.TW", "market": "TW", "type": "個股",
        "price": 100, "short_term_entry_low": 99, "short_term_entry_high": 101,
        "short_term_stop": 96, "short_term_target1": 108, "short_term_rr": 2,
        "short_term_score": 72, "entry_score": 65, "technical_score": 68, "score": 66,
        "market_data_quality_score": 90, "entry_data_coverage": 6, "entry_data_total": 6,
        "daily_volume_ratio": .9, "rsi": 58, "breakdown20": False, "news_penalty": 0,
        "next_session_direction": "📈 看漲", "next_session_confidence": 70,
        "next_session_data_quality": 90, "next_session_signal_level": "RESEARCH",
        "market_contract_valid": True, "trade_guard_blocked": False,
        "overall_eligible": True, "short_term_rank_tier": 2, "short_term_ranking_score": 82,
        "mid_long_rank_tier": 1, "mid_long_ranking_score": 70,
        "tw_intraday_momentum_available": True, "tw_intraday_is_current_session": True,
        "tw_above_vwap": True,
        "tw_breakout_opening_15m": False, "tw_breakout_opening_30m": False,
    }
    waiting = dict(base)
    confirmed = dict(base, symbol="CONFIRMED.TW", tw_breakout_opening_15m=True)
    apply_tw_buy_candidate_ranking([waiting, confirmed])
    assert waiting["buy_setup_eligible"] is True
    assert waiting["buy_trigger_ready"] is False
    assert "等待站上VWAP" in waiting["buy_candidate_status"]
    assert confirmed["buy_trigger_ready"] is True


def test_tw_strong_bearish_forecast_vetoes_setup_candidate():
    row = {
        "symbol": "DOWN.TW", "market": "TW", "type": "個股",
        "price": 100, "short_term_entry_low": 99, "short_term_entry_high": 101,
        "short_term_stop": 96, "short_term_target1": 108, "short_term_rr": 2,
        "short_term_score": 72, "entry_score": 65, "technical_score": 68,
        "score": 66, "market_data_quality_score": 90,
        "entry_data_coverage": 6, "entry_data_total": 6,
        "daily_volume_ratio": .9, "rsi": 58, "news_penalty": 0,
        "next_session_direction": "📉 看跌", "next_session_confidence": 72,
        "next_session_signal_level": "RESEARCH", "next_session_data_quality": 90,
        "market_contract_valid": True, "trade_guard_blocked": False,
        "overall_eligible": True, "short_term_rank_tier": 2,
        "short_term_ranking_score": 72, "mid_long_rank_tier": 1,
        "mid_long_ranking_score": 60,
    }
    apply_tw_buy_candidate_ranking([row])
    assert row["buy_setup_eligible"] is False
    assert row["buy_trigger_ready"] is False
    assert row["overall_rank"] is None
    assert "隔日模型高信心看跌" in row["buy_candidate_reasons"]


def test_tw_confirmed_head_shoulders_vetoes_buy_candidate_without_touching_us():
    base = {
        "type": "個股", "price": 100,
        "short_term_entry_low": 99, "short_term_entry_high": 101,
        "short_term_stop": 96, "short_term_target1": 108, "short_term_rr": 2,
        "short_term_score": 72, "entry_score": 65, "technical_score": 68,
        "score": 66, "market_data_quality_score": 90,
        "entry_data_coverage": 6, "entry_data_total": 6,
        "daily_volume_ratio": .9, "rsi": 58, "news_penalty": 0,
        "next_session_direction": "📈 看漲", "next_session_confidence": 72,
        "next_session_signal_level": "RESEARCH", "next_session_data_quality": 90,
        "market_contract_valid": True, "trade_guard_blocked": False,
        "overall_eligible": True, "overall_rank_tier": 2,
        "overall_ranking_score": 80, "short_term_rank_tier": 2,
        "short_term_ranking_score": 72, "mid_long_rank_tier": 1,
        "mid_long_ranking_score": 60,
        "tw_head_shoulders_top_confirmed": True,
    }
    tw = dict(base, symbol="TOP.TW", market="TW")
    us = dict(base, symbol="TEST", market="US")
    apply_tw_buy_candidate_ranking([tw, us])
    assert tw["buy_setup_eligible"] is False
    assert tw["overall_rank"] is None
    assert "頭肩頂已收盤跌破頸線" in tw["buy_candidate_reasons"]
    assert us["overall_rank_tier"] == 2
    assert us["buy_candidate_eligible"] is None


def test_tw_buy_filter_does_not_change_us_existing_ranking_fields():
    us = {
        "symbol": "NVDA", "market": "US", "type": "個股",
        "overall_eligible": True, "overall_rank_tier": 2,
        "overall_ranking_score": 88, "overall_rank": 1,
        "short_term_rank_tier": 1, "short_term_ranking_score": 70,
        "mid_long_rank_tier": 2, "mid_long_ranking_score": 80,
    }
    apply_tw_buy_candidate_ranking([us])
    assert us["overall_rank_tier"] == 2
    assert us["overall_ranking_score"] == 88
    assert us["buy_candidate_eligible"] is None
    assert us["buy_setup_eligible"] is None
    assert us["buy_trigger_ready"] is None


def test_complete_published_universe_obeys_ranking_safety_invariants():
    payload = json.loads(Path("reports/all_analysis.json").read_text(encoding="utf-8"))
    ranked = score_candidates(payload["data"])
    assert len(ranked) >= 300

    for row in ranked:
        if row.get("trade_guard_blocked") or row.get("market_contract_valid") is False:
            assert row["overall_rank_tier"] == 0
            assert row["short_term_rank_tier"] == 0

    for group in ("TW", "US", "ETF"):
        group_rows = [row for row in ranked if (
            ("ETF" if "ETF" in str(row.get("type", "")).upper()
             else "TW" if row.get("market") == "TW" else "US") == group
        )]
        for horizon, tier_field, rank_field in (
            ("overall", "overall_rank_tier", "overall_rank"),
            ("short", "short_term_rank_tier", "short_term_rank"),
            ("long", "mid_long_rank_tier", "mid_long_rank"),
        ):
            ordered = sorted(
                group_rows,
                key=lambda row: _ranking_sort_key(row, horizon),
                reverse=True,
            )
            assert [row[tier_field] for row in ordered] == sorted(
                (row[tier_field] for row in ordered), reverse=True
            )
            qualified = [row for row in ordered if row[tier_field] == 2]
            unqualified = [row for row in ordered if row[tier_field] != 2]
            assert [row[rank_field] for row in qualified] == list(
                range(1, len(qualified) + 1)
            )
            assert all(row[rank_field] is None for row in unqualified)
            display_field = {
                "overall": "overall_display_rank",
                "short": "short_term_display_rank",
                "long": "mid_long_display_rank",
            }[horizon]
            assert [row[display_field] for row in ordered] == list(
                range(1, len(ordered) + 1)
            )
            assert all(row["ranking_group_count"] == len(ordered) for row in ordered)


def test_equal_scores_use_symbol_as_stable_ranking_tie_breaker():
    rows = [
        {
            "symbol": symbol, "market": "US", "type": "個股",
            "overall_rank_tier": 2, "overall_ranking_score": 80,
            "entry_score": 70, "score": 75,
            "short_term_rank_tier": 2, "short_term_ranking_score": 80,
            "short_term_score": 75,
            "mid_long_rank_tier": 2, "mid_long_ranking_score": 80,
            "mid_long_score": 75,
        }
        for symbol in ("AAA", "BBB")
    ]
    _assign_group_ranks(rows)
    first = {
        row["symbol"]: (
            row["overall_display_rank"],
            row["short_term_display_rank"],
            row["mid_long_display_rank"],
        )
        for row in rows
    }
    _assign_group_ranks(list(reversed(rows)))
    second = {
        row["symbol"]: (
            row["overall_display_rank"],
            row["short_term_display_rank"],
            row["mid_long_display_rank"],
        )
        for row in rows
    }
    assert first == second
    assert first["BBB"] == (1, 1, 1)



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
