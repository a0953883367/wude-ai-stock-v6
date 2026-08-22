import pytest

from market_models import (
    assess_market_data_quality,
    enforce_market_contract,
    validate_taiwan_data,
)


def test_us_contract_removes_tw_only_fields():
    row = {
        "market": "US", "foreign_net": 99, "institution_available": True,
        "margin_5d_change": 100, "broker_available": True,
        "us_short_volume_available": True,
    }
    result = enforce_market_contract(row)
    assert "foreign_net" not in result
    assert "margin_5d_change" not in result
    assert result["institution_available"] is False
    assert result["market_model_version"] == "US-V3"


def test_tw_contract_removes_us_only_fields():
    row = {"market": "TW", "us_short_volume_ratio_pct": 60, "us_live_price": 10, "us_option_iv_pct": 40, "extended_price": 10}
    result = enforce_market_contract(row)
    assert "us_short_volume_ratio_pct" not in result
    assert "extended_price" not in result
    assert "us_live_price" not in result
    assert "us_option_iv_pct" not in result
    assert result["market_model_version"] == "TW-STOCK-V4"


def test_tw_etf_has_separate_contract_and_us_version_is_unchanged():
    tw = enforce_market_contract({"market": "TW", "type": "ETF"})
    us = enforce_market_contract({"market": "US", "type": "ETF"})
    assert tw["market_model_version"] == "TW-ETF-V4"
    assert us["market_model_version"] == "US-V3"


def test_tw_validation_blocks_wrong_units_or_stale_dates():
    result = validate_taiwan_data({
        "market": "TW", "tw_official_session_date": "2026-08-21",
        "tw_official_price_available": True, "tw_price_unit": "TWD/shares",
        "institution_available": True, "institution_date": "2026-08-21",
        "institution_unit": "lots",
        "credit_available": True, "credit_date": "2026-07-01",
        "credit_unit": "lots",
    })
    assert result["institution_available"] is False
    assert result["credit_available"] is False
    assert result["tw_official_session_available"] is True
    assert result["tw_data_valid"] is False


def test_tw_validation_rejects_even_one_session_date_mismatch():
    result = validate_taiwan_data({
        "market": "TW", "tw_official_session_date": "2026-08-21",
        "tw_official_price_available": True, "tw_price_unit": "TWD/shares",
        "institution_available": True, "institution_date": "2026-08-20",
        "institution_unit": "shares",
    })
    assert result["institution_available"] is False
    assert "法人資料交易日不一致" in result["tw_data_validation_issues"]


def test_tw_flow_without_official_session_is_not_ranked_as_current():
    result = validate_taiwan_data({
        "market": "TW", "institution_available": True,
        "institution_date": "2026-08-21", "institution_unit": "shares",
    })
    assert result["institution_available"] is False
    assert "法人資料缺官方交易日基準" in result["tw_data_validation_issues"]


def test_quality_uses_different_market_checklists():
    common = {"price": 100, "avg_volume20": 1000, "intraday_available": True, "news_data_available": True}
    tw = assess_market_data_quality({**common, "market": "TW", "institution_available": True})
    us = assess_market_data_quality({**common, "market": "US", "extended_hours_available": True, "us_short_volume_available": True, "us_company_data_available": True, "us_live_data_available": True, "us_option_data_available": True})
    assert "三大法人" in tw["market_data_available"]
    assert "盤前／盤後" in us["market_data_available"]
    assert "SIP全市場行情" in us["market_data_available"]
    assert "OPRA選擇權" in us["market_data_available"]
    assert "三大法人" not in us["market_data_available"]


def test_us_quality_contract_remains_unchanged_at_full_coverage():
    result = assess_market_data_quality({
        "market": "US", "type": "個股", "price": 100, "avg_volume20": 1000,
        "intraday_available": True, "news_data_available": True,
        "us_live_data_available": True, "extended_hours_available": True,
        "us_short_volume_available": True, "us_company_data_available": True,
        "us_option_data_available": True, "macro_data_available": True,
    })
    assert result["market_data_quality_score"] == 100
    assert result["market_data_missing"] == []


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError):
        enforce_market_contract({"market": "EU"})
