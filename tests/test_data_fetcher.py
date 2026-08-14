from data_fetcher import _aggregate_credit_rows, _aggregate_fundamental_rows


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
    assert result["revenue_yoy_pct"] == 20.0
    assert round(result["revenue_mom_pct"], 2) == 9.09
