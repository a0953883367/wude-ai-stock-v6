from pathlib import Path
import json

import pandas as pd

import tw_official_data as official


def test_number_and_roc_date_normalization():
    assert official._number("-2,292,868") == -2_292_868
    assert official._number("--") is None
    assert official._iso_date("1150821") == "2026-08-21"
    assert official._iso_date("20260821") == "2026-08-21"


def test_twse_parser_preserves_official_units_and_negative_flows(monkeypatch):
    def fake_get(url, *, params=None):
        if url.endswith("STOCK_DAY_ALL"):
            return [{
                "Code": "2330", "Date": "1150821", "OpeningPrice": "1,180",
                "HighestPrice": "1,200", "LowestPrice": "1,170",
                "ClosingPrice": "1,195", "TradeVolume": "30,000,000",
            }]
        if url.endswith("MI_MARGN"):
            return [{
                "股票代號": "2330", "融資前日餘額": "10,100",
                "融資今日餘額": "10,000", "融券前日餘額": "250",
                "融券今日餘額": "230",
            }]
        if url.endswith("BWIBBU_ALL"):
            return [{
                "Code": "2330", "Date": "1150821", "PEratio": "21.5",
                "PBratio": "7.2", "DividendYield": "1.8",
            }]
        if url.endswith("t187ap05_L"):
            return [{
                "出表日期": "1150817", "資料年月": "11507", "公司代號": "2330",
                "營業收入-當月營收": "300000000", "營業收入-上月比較增減(%)": "2.5",
                "營業收入-去年同月增減(%)": "18.0",
            }]
        if url.endswith("t187ap04_L"):
            return [{
                "發言日期": "1150821", "公司代號": "2330",
                "主旨 ": "公告董事會決議事項",
            }]
        if url == official.TWSE_T86:
            return {
                "stat": "OK", "date": "20260821",
                "fields": [
                    "證券代號", "外陸資買賣超股數(不含外資自營商)",
                    "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數",
                ],
                "data": [["2330", "-2,000,000", "100,000", "-50,000", "-1,950,000"]],
            }
        raise AssertionError(url)

    monkeypatch.setattr(official, "_get_json", fake_get)
    prices, institutions, credit, fundamentals, announcements = official._parse_twse({"2330"})

    assert prices["2330"]["volume"] == 30_000_000
    assert prices["2330"]["tw_price_unit"] == "TWD/shares"
    assert institutions["2330"]["foreign"] == -2_000_000
    assert institutions["2330"]["institution_unit"] == "shares"
    assert credit["2330"]["margin_1d_change"] == -100
    assert credit["2330"]["credit_unit"] == "lots"
    assert fundamentals["2330"]["per"] == 21.5
    assert fundamentals["2330"]["revenue_yoy_pct"] == 18
    assert fundamentals["2330"]["revenue_source"] == "MOPS via TWSE OpenAPI"
    assert announcements["2330"][0]["official"] is True


def test_tpex_parser_uses_otc_endpoints_and_units(monkeypatch):
    def fake_get(url, *, params=None):
        if url.endswith("daily_close_quotes"):
            return [{
                "SecuritiesCompanyCode": "6488", "Date": "1150821",
                "Open": "500", "High": "510", "Low": "495", "Close": "505",
                "TradingShares": "1,200,000",
            }]
        if url.endswith("3insti_daily_trading"):
            return [{
                "SecuritiesCompanyCode": "6488", "Date": "1150821",
                "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "-20,000",
                "SecuritiesInvestmentTrustCompanies-Difference": "10,000",
                "Dealers-Difference": "-1,000", "TotalDifference": "-11,000",
            }]
        if url.endswith("mainboard_margin_balance"):
            return [{
                "SecuritiesCompanyCode": "6488", "Date": "1150821",
                "MarginPurchaseBalancePreviousDay": "120", "MarginPurchaseBalance": "118",
                "ShortSaleBalancePreviousDay": "8", "ShortSaleBalance": "9",
            }]
        if url.endswith("tpex_margin_sbl"):
            return [{
                "SecuritiesCompanyCode": "6488", "Date": "1150821",
                "SecuritiesBorrowingBalancePreviousDay": "2,000",
                "SecuritiesBorrowingBalanceOfTheMarketDay": "2,500",
            }]
        if url.endswith("peratio_analysis"):
            return [{
                "SecuritiesCompanyCode": "6488", "Date": "1150821",
                "PriceEarningRatio": "30", "PriceBookRatio": "5",
                "YieldRatio": "0.5",
            }]
        if url.endswith("mopsfin_t187ap05_O"):
            return [{
                "出表日期": "1150817", "資料年月": "11507", "公司代號": "6488",
                "營業收入-當月營收": "1000000", "營業收入-上月比較增減(%)": "-1",
                "營業收入-去年同月增減(%)": "6",
            }]
        if url.endswith("mopsfin_t187ap04_O"):
            return [{
                "發言日期": "1150821", "SecuritiesCompanyCode": "6488",
                "主旨": "公告重要營運事項",
            }]
        raise AssertionError(url)

    monkeypatch.setattr(official, "_get_json", fake_get)
    prices, institutions, credit, fundamentals, announcements = official._parse_tpex({"6488"})

    assert prices["6488"]["tw_price_source"] == "TPEx OpenAPI"
    assert institutions["6488"]["institution_1d"] == -11_000
    assert credit["6488"]["credit_unit"] == "lots"
    assert credit["6488"]["sbl_unit"] == "shares"
    assert fundamentals["6488"]["pbr"] == 5
    assert fundamentals["6488"]["revenue_unit"] == "TWD thousands"
    assert announcements["6488"][0]["source"] == "MOPS via TPEx OpenAPI"


def test_institution_merge_does_not_relabel_stale_history_as_current():
    fallback = {"2330": {
        "institution_date": "2026-08-20", "institution_1d": 100,
        "institution_3d": 300, "institution_5d": 500,
    }}
    current = {"2330": {
        "institution_date": "2026-08-21", "institution_1d": -900,
        "institution_source": "TWSE T86", "institution_unit": "shares",
    }}
    result = official.merge_official_with_fallback(
        fallback, current, kind="institution"
    )["2330"]
    assert result["institution_1d"] == -900
    assert result["institution_5d"] is None
    assert result["institution_multiday_available"] is False
    assert result["trust_buy_days_5"] is None


def test_same_day_official_institution_merge_updates_group_windows_and_buy_days():
    fallback = {"2330": {
        "institution_date": "2026-08-21", "institution_1d": 100,
        "institution_3d": 300, "institution_5d": 500,
        "foreign": 80, "foreign_5d": 300, "foreign_buy_days_5": 4,
        "trust": 20, "trust_5d": 100, "trust_buy_days_5": 3,
        "institution_buy_days_5": 4, "institution_multiday_available": True,
    }}
    current = {"2330": {
        "institution_date": "2026-08-21", "institution_1d": -50,
        "foreign": -60, "trust": 10, "dealer": 0,
        "institution_source": "TWSE T86", "institution_unit": "shares",
    }}
    result = official.merge_official_with_fallback(
        fallback, current, kind="institution"
    )["2330"]
    assert result["institution_5d"] == 350
    assert result["foreign_5d"] == 160
    assert result["trust_5d"] == 90
    assert result["foreign_buy_days_5"] == 3
    assert result["trust_buy_days_5"] == 3
    assert result["institution_buy_days_5"] == 3


def test_credit_merge_drops_stale_multiday_delta():
    fallback = {"2330": {
        "credit_date": "2026-08-20", "margin_1d_change": 10,
        "margin_5d_change": 100, "credit_unit": "lots",
    }}
    current = {"2330": {
        "credit_date": "2026-08-21", "margin_1d_change": -20,
        "credit_unit": "lots", "credit_official": True,
    }}
    result = official.merge_official_with_fallback(
        fallback, current, kind="credit"
    )["2330"]
    assert result["margin_1d_change"] == -20
    assert result["margin_5d_change"] is None
    assert result["credit_multiday_available"] is False


def test_official_daily_bar_overwrites_same_session():
    frame = pd.DataFrame(
        {"Open": [100], "High": [102], "Low": [99], "Close": [101], "Volume": [10]},
        index=pd.to_datetime(["2026-08-21"]),
    )
    result = official.overlay_official_daily(frame, {
        "date": "2026-08-21", "open": 110, "high": 112,
        "low": 109, "close": 111, "volume": 1_000,
    })
    assert result.loc[pd.Timestamp("2026-08-21"), "close"] == 111
    assert result.loc[pd.Timestamp("2026-08-21"), "volume"] == 1_000


def test_fetch_splits_listed_and_otc_and_writes_cache(monkeypatch, tmp_path: Path):
    called = {}

    def fake_twse(ids):
        called["twse"] = ids
        return ({"2330": {"date": "2026-08-21"}}, {}, {}, {}, {})

    def fake_tpex(ids):
        called["tpex"] = ids
        return ({"6488": {"date": "2026-08-21"}}, {}, {}, {}, {})

    monkeypatch.setattr(official, "_parse_twse", fake_twse)
    monkeypatch.setattr(official, "_parse_tpex", fake_tpex)
    result = official.fetch_taiwan_official_data([
        {"symbol": "2330.TW", "market": "TW"},
        {"symbol": "6488.TWO", "market": "TW"},
        {"symbol": "NVDA", "market": "US"},
    ], tmp_path / "official.json")

    assert called == {"twse": {"2330"}, "tpex": {"6488"}}
    assert set(result["prices"]) == {"2330", "6488"}
    assert (tmp_path / "official.json").exists()


def test_partial_refresh_preserves_dated_fundamental_fields(monkeypatch, tmp_path: Path):
    cache = tmp_path / "official.json"
    cache.write_text(json.dumps({
        "data": {"fundamentals": {"6488": {
            "per": 30, "pbr": 5, "valuation_date": "2026-08-21",
            "revenue_yoy_pct": 6,
        }}},
    }), encoding="utf-8")
    monkeypatch.setattr(official, "_parse_tpex", lambda _ids: (
        {"6488": {"date": "2026-08-22"}}, {}, {},
        {"6488": {"revenue_yoy_pct": 8, "revenue_date": "2026-08-22"}}, {},
    ))
    result = official.fetch_taiwan_official_data(
        [{"symbol": "6488.TWO", "market": "TW"}], cache
    )
    assert result["fundamentals"]["6488"]["per"] == 30
    assert result["fundamentals"]["6488"]["pbr"] == 5
    assert result["fundamentals"]["6488"]["revenue_yoy_pct"] == 8


def test_stale_cache_never_reuses_daily_price_or_flow(monkeypatch, tmp_path: Path):
    cache = tmp_path / "official.json"
    cache.write_text(json.dumps({
        "updated_at": "2026-01-01T00:00:00+08:00",
        "data": {
            "prices": {"2330": {"close": 1}},
            "institutions": {"2330": {"institution_1d": 999}},
            "credit": {"2330": {"margin_1d_change": 999}},
            "fundamentals": {"2330": {"per": 20}},
            "announcements": {"2330": [{"title": "old"}]},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(official, "_parse_twse", lambda _ids: (_ for _ in ()).throw(RuntimeError("offline")))
    result = official.fetch_taiwan_official_data(
        [{"symbol": "2330.TW", "market": "TW"}], cache
    )
    assert result["prices"] == {}
    assert result["institutions"] == {}
    assert result["credit"] == {}
    assert result["announcements"] == {}
    assert result["fundamentals"]["2330"]["per"] == 20
