from data_fetcher import (
    _aggregate_credit_rows,
    _aggregate_financial_quality_rows,
    _aggregate_fundamental_rows,
    _aggregate_institutional_rows,
    _dataset_for_ids,
    _parse_finra_short_volume,
    _classify_market_regime_frame,
    _retry_stale_us_daily_history,
)
from datetime import date, datetime
import pandas as pd
from zoneinfo import ZoneInfo


def test_download_history_retries_partial_batch_omissions_individually(monkeypatch):
    from data_fetcher import download_history

    dates = pd.date_range("2026-08-21", periods=2, freq="B")
    calls = []

    def frame(symbol):
        return pd.DataFrame({
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.0, 102.0],
            "volume": [1000, 1200],
        }, index=dates)

    def fake_download(*, tickers, **kwargs):
        calls.append(tickers)
        if isinstance(tickers, list):
            batch = pd.concat({"ANET": frame("ANET")}, axis=1)
            return batch
        assert tickers == "QUBT"
        return frame("QUBT")

    monkeypatch.setattr("data_fetcher.yf.download", fake_download)
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    result = download_history(["ANET", "QUBT"])

    assert set(result) == {"ANET", "QUBT"}
    assert calls == [["ANET", "QUBT"], "QUBT"]


def test_download_history_keeps_coretronic_securities_separate(monkeypatch):
    from data_fetcher import download_history

    current = pd.DataFrame({
        "open": [83.0, 84.0], "high": [85.0, 86.0],
        "low": [82.0, 83.0], "close": [84.0, 85.0],
        "volume": [1300, 1400],
    }, index=pd.to_datetime(["2026-09-03", "2026-09-04"]))
    calls = []

    def fake_download(*, tickers, **kwargs):
        calls.append(tickers)
        assert tickers == ["3718.TWO"]
        return pd.concat({"3718.TWO": current}, axis=1)

    monkeypatch.setattr("data_fetcher.yf.download", fake_download)
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    result = download_history(["3718.TWO"])

    assert calls == [["3718.TWO"]]
    assert set(result) == {"3718.TWO"}
    assert list(result["3718.TWO"].index.strftime("%Y-%m-%d")) == [
        "2026-09-03", "2026-09-04",
    ]
    assert list(result["3718.TWO"]["close"]) == [84.0, 85.0]


def test_3718_uses_only_its_tpex_history_when_yahoo_omits_it(monkeypatch):
    from data_fetcher import download_history

    current = pd.DataFrame({
        "open": [85.0, 80.0], "high": [86.0, 80.5],
        "low": [77.2, 75.2], "close": [78.3, 75.5],
        "adj close": [78.3, 75.5], "volume": [3_493_000, 4_198_000],
    }, index=pd.to_datetime(["2026-09-03", "2026-09-04"]))

    monkeypatch.setattr("data_fetcher.yf.download", lambda **_: pd.DataFrame())
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "data_fetcher._download_tpex_monthly_history",
        lambda symbol: current if symbol == "3718.TWO" else (_ for _ in ()).throw(
            AssertionError("5371 must not be fetched for 3718")
        ),
    )

    result = download_history(["3718.TWO"])

    assert set(result) == {"3718.TWO"}
    assert list(result["3718.TWO"].index.strftime("%Y-%m-%d")) == [
        "2026-09-03", "2026-09-04",
    ]
    assert result["3718.TWO"].iloc[-1]["close"] == 75.5


def test_core_market_does_not_request_excluded_open_market_prices(monkeypatch):
    from data_fetcher import CORE_MARKET, fetch_core_market

    requested = []

    def fake_history(symbols, period="7d"):
        requested.extend(symbols)
        return {}

    monkeypatch.setattr("data_fetcher.download_history", fake_history)
    result = fetch_core_market({"加權指數", "櫃買指數"})

    assert CORE_MARKET["加權指數"] not in requested
    assert CORE_MARKET["櫃買指數"] not in requested
    assert result["加權指數"]["source"] == "after_close_policy"
    assert result["櫃買指數"]["price"] is None


def test_stale_nonempty_us_daily_frames_retry_after_close(monkeypatch):
    target = pd.to_datetime(["2026-08-27", "2026-08-28"])
    stale = pd.to_datetime(["2026-08-26", "2026-08-27"])

    def frame(index):
        return pd.DataFrame({
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.0, 102.0],
            "volume": [1000, 1200],
        }, index=index)

    symbols = [f"US{i:02d}" for i in range(12)]
    result = {
        symbol: frame(target if index < 9 else stale)
        for index, symbol in enumerate(symbols)
    }
    calls = []

    def fake_download(*, tickers, **kwargs):
        calls.append(tickers)
        return frame(target)

    monkeypatch.setattr("data_fetcher.yf.download", fake_download)
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    advanced = _retry_stale_us_daily_history(
        result, symbols,
        now=datetime(
            2026, 8, 29, 0, 30,
            tzinfo=ZoneInfo("America/New_York"),
        ),
    )

    assert advanced == symbols[9:]
    assert calls == symbols[9:]
    assert {_frame.index[-1].date().isoformat() for _frame in result.values()} == {
        "2026-08-28"
    }


def test_stale_us_daily_retry_uses_stock_and_etf_cohorts(monkeypatch):
    current = pd.to_datetime(["2026-08-27", "2026-08-28"])
    stale = pd.to_datetime(["2026-08-26", "2026-08-27"])

    def frame(index):
        return pd.DataFrame({
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.0, 102.0],
            "volume": [1000, 1200],
        }, index=index)

    stocks = [f"STOCK{i:02d}" for i in range(20)]
    etfs = [f"ETF{i:02d}" for i in range(12)]
    symbols = stocks + etfs
    result = {symbol: frame(stale) for symbol in stocks}
    result.update({
        symbol: frame(current if index < 9 else stale)
        for index, symbol in enumerate(etfs)
    })
    calls = []

    def fake_download(*, tickers, **kwargs):
        calls.append(tickers)
        return frame(current)

    monkeypatch.setattr("data_fetcher.yf.download", fake_download)
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    advanced = _retry_stale_us_daily_history(
        result,
        symbols,
        cohorts={
            **{symbol: "US_STOCK" for symbol in stocks},
            **{symbol: "US_ETF" for symbol in etfs},
        },
        now=datetime(
            2026, 8, 29, 0, 30,
            tzinfo=ZoneInfo("America/New_York"),
        ),
    )

    assert advanced == etfs[9:]
    assert calls == etfs[9:]


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
    assert result["trust_buy_days_5"] == 1
    assert result["foreign_buy_days_5"] == 0
    assert result["institution_buy_days_5"] == 0


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


def test_market_regime_classification_is_point_in_time_and_has_three_states():
    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    bull = pd.DataFrame({"open": [100 + i * .5 for i in range(100)], "close": [100 + i * .5 for i in range(100)]}, index=dates)
    bear = pd.DataFrame({"open": [150 - i * .5 for i in range(100)], "close": [150 - i * .5 for i in range(100)]}, index=dates)
    sideways = pd.DataFrame({"open": [100.0] * 100, "close": [100.0] * 100}, index=dates)

    bull_result = _classify_market_regime_frame(bull, market="TW", benchmark="加權指數")
    bear_result = _classify_market_regime_frame(bear, market="TW", benchmark="加權指數")
    sideways_result = _classify_market_regime_frame(sideways, market="TW", benchmark="加權指數")
    last = dates[-1].date().isoformat()
    assert bull_result[last]["regime"] == "bull"
    assert bear_result[last]["regime"] == "bear"
    assert sideways_result[last]["regime"] == "sideways"

    # Future prices may not change a label already calculated for an old date.
    prior = dates[-1].date().isoformat()
    extended_dates = pd.date_range(dates[0], periods=110, freq="B")
    extended = pd.DataFrame({
        "open": [100 + i * .5 for i in range(100)] + [80.0] * 10,
        "close": [100 + i * .5 for i in range(100)] + [80.0] * 10,
    }, index=extended_dates)
    extended_result = _classify_market_regime_frame(extended, market="TW", benchmark="加權指數")
    assert extended_result[prior] == bull_result[prior]
