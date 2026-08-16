import pytest

from market_models import assess_market_data_quality, enforce_market_contract


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
    row = {"market": "TW", "us_short_volume_ratio_pct": 60, "extended_price": 10}
    result = enforce_market_contract(row)
    assert "us_short_volume_ratio_pct" not in result
    assert "extended_price" not in result
    assert result["market_model_version"] == "TW-V3"


def test_quality_uses_different_market_checklists():
    common = {"price": 100, "avg_volume20": 1000, "intraday_available": True, "news_data_available": True}
    tw = assess_market_data_quality({**common, "market": "TW", "institution_available": True})
    us = assess_market_data_quality({**common, "market": "US", "extended_hours_available": True, "us_short_volume_available": True, "us_company_data_available": True})
    assert "三大法人" in tw["market_data_available"]
    assert "盤前／盤後" in us["market_data_available"]
    assert "三大法人" not in us["market_data_available"]


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError):
        enforce_market_contract({"market": "EU"})
