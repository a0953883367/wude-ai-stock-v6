from data_fetcher import (
    _aggregate_credit_rows,
    _aggregate_financial_quality_rows,
    _aggregate_fundamental_rows,
    _aggregate_institutional_rows,
    _dataset_for_ids,
    _parse_finra_short_volume,
)
from datetime import date


def test_credit_rows_have_latest_and_five_day_changes():
    margin_rows = [
        {
            "date": "2026-08-14", "stock_id": "2330",
            "MarginPurchaseTodayBalance": 1200, "MarginPurchaseYesterdayBalance": 1100,
            "ShortSaleTodayBalance": 300, "ShortSaleYesterdayBalance": 320,
        },
        {
            "date": "2026-08-08", "stock_id": "2330",
            "MarginPurchaseTodayBalance": 1000, "MarginPurchaseYesterdayBalance": 900,
            "ShortSaleTodayBalance": 350, "ShortSaleYesterdayBalance": 360,
        },
    ]
    short_rows = [
        {
            "date": "2026-08-14", "stock_id": "2330",
            "SBLShortSalesCurrentDayBalance": 500,
            "SBLShortSalesPreviousDayBalance": 450,
        },
        {
            "date": "2026-08-08", "stock_id": "2330",
            "SBLShortSalesCurrentDayBalance": 420,
            "SBLShortSalesPreviousDayBalance": 400,
        },
    ]
    result = _aggregate_credit_rows(margin_rows, short_rows, {"2330"})["2330"]
    assert result["margin_1d_change"] == 100
    assert result["margin_5d_change"] == 300
    assert result["short_5d_change"] == -60
    assert result["sbl_5d_change"] == 100
    assert result["credit_date"] == "2026-08-14"
    assert result["credit_unit"] == "lots"
    assert result["sbl_unit"] == "shares"


def test_institution_rows_include_date_source_and_share_unit():
    result = _aggregate_institutional_rows([
        {"date": "2026-08-21", "stock_id": "2330", "name": "Foreign_Investor", "buy": 10, "sell": 30},
        {"date": "2026-08-21", "stock_id": "2330", "name": "Investment_Trust", "buy": 5, "sell": 0},
    ], {"2330"})["2330"]
    assert result["institution_1d"] == -15
    assert result["institution_date"] == "2026-08-21"
    assert result["institution_source"] == "FinMind"
    assert result["institution_unit"] == "shares"


def test_fundamental_rows_have_valuation_and_revenue_growth():
    per_rows = [{
        "date": "2026-08-14", "stock_id": "2330", "PER": 18.5,
        "PBR": 4.2, "dividend_yield": 2.5,
    }]
    revenue_rows = [
        {"stock_id": "2330", "revenue_year": 2025, "revenue_month": 7, "revenue": 1000},
        {"stock_id": "2330", "revenue_year": 2026, "revenue_month": 6, "revenue": 1100},
        {"stock_id": "2330", "revenue_year": 2026, "revenue_month": 7, "revenue": 1200},
    ]
    result = _aggregate_fundamental_rows(per_rows, revenue_rows, {"2330"})["2330"]
    assert result["per"] == 18.5
    assert result["pbr"] == 4.2
    assert round(result["revenue_yoy_pct"], 2) == 20.0
    assert round(result["revenue_mom_pct"], 2) == 9.09


def test_financial_quality_rows_have_profitability_cash_and_debt():
    income_rows = [
        {"date": "2025-06-30", "stock_id": "2330", "type": "EPS", "value": 1.5},
        {"date": "2026-06-30", "stock_id": "2330", "type": "Revenue", "value": 1000},
        {"date": "2026-06-30", "stock_id": "2330", "type": "GrossProfit", "value": 400},
        {"date": "2026-06-30", "stock_id": "2330", "type": "OperatingIncome", "value": 200},
        {"date": "2026-06-30", "stock_id": "2330", "type": "IncomeAfterTaxes", "value": 100},
        {"date": "2026-06-30", "stock_id": "2330", "type": "EPS", "value": 2.0},
    ]
    balance_rows = [
        {"date": "2026-06-30", "stock_id": "2330", "type": "Assets", "value": 2000},
        {"date": "2026-06-30", "stock_id": "2330", "type": "Liabilities", "value": 800},
        {"date": "2026-06-30", "stock_id": "2330", "type": "Equity", "value": 1200},
    ]
    cash_rows = [{
        "date": "2026-06-30", "stock_id": "2330",
        "type": "CashFlowsFromOperatingActivities", "value": 150,
    }]
    result = _aggregate_financial_quality_rows(
        income_rows, balance_rows, cash_rows, {"2330"}
    )["2330"]
    assert result["eps"] == 2.0
    assert round(result["eps_yoy_pct"], 2) == 33.33
    assert result["gross_margin_pct"] == 40.0
    assert result["operating_margin_pct"] == 20.0
    assert result["debt_ratio_pct"] == 40.0
    assert round(result["roe_pct"], 2) == 8.33
    assert result["operating_cash_flow_positive"] == 1.0



def test_parse_finra_short_volume_aggregates_markets_and_labels_limit():
    payload = "\n".join([
        "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market",
        "20260813|NVDA|100|5|300|Q",
        "20260813|NVDA|50|0|200|N",
        "20260813|AAPL|90|0|100|Q",
    ])
    result = _parse_finra_short_volume(payload, {"NVDA"}, "2026-08-13")
    assert result["NVDA"]["us_short_volume"] == 150
    assert result["NVDA"]["us_short_exempt_volume"] == 5
    assert result["NVDA"]["us_total_reported_volume"] == 500
    assert result["NVDA"]["us_short_volume_ratio_pct"] == 31
    assert "不等於未回補空單" in result["NVDA"]["us_short_volume_note"]



def test_normalize_etf_info_calculates_premium_spread_and_ratios():
    from data_fetcher import _normalize_etf_info
    result = _normalize_etf_info({
        "navPrice": 100,
        "regularMarketPrice": 102,
        "bid": 101.9,
        "ask": 102.1,
        "annualReportExpenseRatio": 0.0025,
        "totalAssets": 10_000_000_000,
        "threeYearAverageReturn": 0.12,
    })
    assert round(result["premium_discount_pct"], 2) == 2.0
    assert 0 < result["bid_ask_spread_pct"] < 1
    assert result["expense_ratio_pct"] == 0.25
    assert result["etf_return_3y_pct"] == 12.0
    assert result["etf_metadata_available"] is True



def test_normalize_us_equity_info_maps_company_fundamentals():
    from data_fetcher import _normalize_us_equity_info
    result = _normalize_us_equity_info({"trailingPE":25,"priceToBook":8,"dividendYield":0.005,"revenueGrowth":0.18,"earningsGrowth":0.22,"grossMargins":0.55,"operatingMargins":0.31,"returnOnEquity":0.42,"debtToEquity":45,"operatingCashflow":123456,"trailingEps":4.2,"marketCap":1_000_000_000})
    assert result["per"] == 25
    assert round(result["revenue_yoy_pct"], 6) == 18
    assert round(result["eps_yoy_pct"], 6) == 22
    assert round(result["gross_margin_pct"], 6) == 55
    assert result["operating_cash_flow_positive"] == 1.0
    assert result["fundamental_available"] == 1.0
    assert result["financial_quality_available"] == 1.0
    assert result["us_company_data_fields"] >= 10


def test_finmind_per_stock_fallback_uses_bounded_timeout(monkeypatch):
    calls = []

    def fake_rows(dataset, start, end, stock_id=None, timeout=None):
        calls.append((stock_id, timeout))
        return [{"stock_id": stock_id, "date": "2026-08-19"}]

    monkeypatch.setattr("data_fetcher._finmind_rows", fake_rows)
    rows = _dataset_for_ids(
        "TaiwanStockPER", {"2330", "2317"}, date(2026, 8, 1), date(2026, 8, 19)
    )
    assert {row["stock_id"] for row in rows} == {"2330", "2317"}
    assert all(stock_id in {"2330", "2317"} for stock_id, _ in calls)
    assert len(calls) == 2
    assert all(timeout is not None and timeout <= 8 for _, timeout in calls)
