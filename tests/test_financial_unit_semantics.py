"""Financial unit regressions use synthetic inputs; no live provider calls."""
import json
from datetime import date

import pandas as pd
import pytest

import data_fetcher as df
from strategy import build_features, score_candidates
from tw_financial_official import aggregate_official_financial_rows, fetch_tw_official_financials
from valuation_risk_shadow import _company_values

RATIOS = dict(revenueGrowth="revenue_yoy_pct", earningsGrowth="eps_yoy_pct",
              grossMargins="gross_margin_pct", operatingMargins="operating_margin_pct",
              returnOnEquity="roe_pct")


@pytest.mark.parametrize("raw", [0, .99, 1, 1.01, 1.059, -1, -1.01, -2, "1.01"])
def test_ratio_contract_has_no_magnitude_boundary(raw):
    result = df._normalize_us_equity_info(dict.fromkeys(RATIOS, raw))
    for field in RATIOS.values():
        assert result[field] == pytest.approx(float(raw) * 100)


@pytest.mark.parametrize("raw", [None, "", "unknown", True, False, float("nan"),
                               float("inf"), float("-inf"), 1e308, {}])
def test_invalid_ratio_does_not_claim_available(raw):
    result = df._normalize_us_equity_info(dict.fromkeys(RATIOS, raw))
    assert all(result[field] is None for field in RATIOS.values())
    assert result["us_company_data_fields"] == 0
    assert result["fundamental_available"] == result["financial_quality_available"] == 0


@pytest.mark.parametrize("raw,expected", [(1.01, 100), (-1.01, 0)])
def test_correct_units_reach_existing_growth_score(raw, expected):
    prices = pd.DataFrame({
        "open": [100.] * 70, "high": [102.] * 70, "low": [98.] * 70,
        "close": [100.] * 70, "volume": [1000000.] * 70,
    }, index=pd.date_range("2026-01-01", periods=70, freq="B"))
    row = build_features({"symbol": "TEST", "name": "Test", "market": "US",
                          "type": "個股", "industry": "Test", "theme": "Test"}, prices, pd.DataFrame(), {})
    row.update(df._normalize_us_equity_info({"revenueGrowth": raw}))
    assert score_candidates([row])[0]["growth_score"] == expected


@pytest.mark.parametrize("refresh", [True, False])
def test_legacy_us_cache_refresh_or_safe_fallback(tmp_path, monkeypatch, refresh):
    path = tmp_path / "cache.json"
    legacy = {"cached_at": date.today().isoformat(), "valuation_shadow_schema_version": 1,
              "per": 20, "revenue_yoy_pct": 1.059, "roe_pct": 12,
              "us_sec_fallback_field_names": ["roe_pct"],
              "us_sec_fallback_used": True, "us_company_data_fields": 3}
    path.write_text(json.dumps({"companies": {"TEST": legacy}}))
    calls = []
    class Ticker:
        def __init__(self, symbol):
            calls.append(symbol)
        def get_info(self):
            if refresh:
                return {"trailingPE": 20, "revenueGrowth": 1.059}
            raise RuntimeError("offline")
    monkeypatch.setattr(df.yf, "Ticker", Ticker)
    monkeypatch.setattr(df, "fetch_sec_company_fundamentals", lambda *a, **kw: {})
    universe = [{"symbol": "TEST", "market": "US", "type": "個股"}]
    result = df.fetch_us_company_metadata(universe, path)["TEST"]
    assert calls == ["TEST"]
    assert result["per"] == 20
    assert result["revenue_yoy_pct"] == (pytest.approx(105.9) if refresh else None)
    if not refresh:
        assert result["roe_pct"] == 12  # Explicit SEC provenance remains usable.
        assert result["us_company_data_fields"] == 2
        assert result.get("us_fundamentals_ratio_schema_version") != 2
    assert json.loads(path.read_text())["companies"]["TEST"]["revenue_yoy_pct"] == result["revenue_yoy_pct"]
    df.fetch_us_company_metadata(universe, path)
    assert len(calls) == (1 if refresh else 2)


def test_official_liabilities_keep_units_and_ratio_but_cannot_derive_ev():
    row = aggregate_official_financial_rows({"TWSE_balance_ci": [{
        "年度": "115", "季別": "2", "公司代號": "2330",
        "資產總計": "5000", "負債總計": "2000",
    }]}, {"2330"})["2330"]
    assert row["total_liabilities"] == 2000
    assert "total_debt" not in row
    assert row["debt_ratio_pct"] == 40
    assert row["financial_statement_unit"] == "TWD_thousands_as_reported"
    row.update(market_cap=10000000, total_cash=1000000, total_revenue_ttm=5000000)
    assert _company_values(row)["enterprise_value"] is None
    row["total_debt"] = 2000  # Legacy archived alias is also rejected.
    assert _company_values(row)["enterprise_value_source"] == "unavailable"


def test_us_real_debt_still_derives_ev_and_reported_ev_is_preserved():
    row = dict(market="US", total_debt=500000, market_cap=10000000,
               total_cash=1000000, total_revenue_ttm=5000000)
    assert _company_values(row)["enterprise_value"] == 9500000
    row.update(enterprise_value=12000000, financial_quality_source="MOPS via TWSE/TPEx OpenAPI")
    assert _company_values(row)["enterprise_value"] == 12000000


@pytest.mark.parametrize("same_day", [True, False])
def test_old_tw_cache_migrates_even_when_refresh_fails(tmp_path, same_day):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({
        "schema_version": 1, "updated_at": date.today().isoformat() if same_day else "2020-01-01",
        "requested_symbols": ["2330"], "symbols": {"2330": {
            "total_debt": 2000, "total_assets": 5000, "debt_ratio_pct": 40,
            "financial_statement_unit": "TWD_thousands_as_reported",
            "financial_quality_fields": ["total_debt", "total_assets", "debt_ratio_pct"],
            "financial_quality_field_count": 3,
        }},
    }))
    calls = []
    def unavailable(url):
        calls.append(url)
        raise RuntimeError("offline")
    result = fetch_tw_official_financials({"2330"}, cache_path=path, fetcher=unavailable)["2330"]
    assert result["total_liabilities"] == 2000 and "total_debt" not in result
    assert result["debt_ratio_pct"] == 40
    assert "total_debt" not in result["financial_quality_fields"]
    assert result["financial_quality_field_count"] == 3
    stored = json.loads(path.read_text())
    assert stored["schema_version"] == 2 and stored["symbols"]["2330"] == result
    assert bool(calls) is not same_day


def test_unrelated_yield_and_etf_contracts_are_unchanged():
    assert df._normalize_us_equity_info({"dividendYield": 1.5})["dividend_yield"] == 1.5
    assert df._normalize_etf_info({"threeYearAverageReturn": 1.5})["etf_return_3y_pct"] == 1.5
