from data_fetcher import _aggregate_credit_rows


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

