import json

from tw_financial_official import (
    aggregate_official_financial_rows,
    fetch_tw_official_financials,
)


def test_aggregate_official_statements_builds_financial_quality():
    rows = {
        "TWSE_income_ci": [{
            "年度": "115", "季別": "2", "公司代號": "2330",
            "營業收入": "1000000", "營業毛利（毛損）淨額": "550000",
            "營業利益（損失）": "450000", "淨利（淨損）歸屬於母公司業主": "400000",
            "基本每股盈餘（元）": "20",
        }],
        "TWSE_balance_ci": [{
            "年度": "115", "季別": "2", "公司代號": "2330",
            "資產總計": "5000000", "負債總計": "2000000",
            "歸屬於母公司業主之權益合計": "3000000", "每股參考淨值": "120",
        }],
    }
    item = aggregate_official_financial_rows(rows, {"2330"})["2330"]
    assert item["financial_report_date"] == "2026-06-30"
    assert item["statement_revenue_ytd"] == 1_000_000
    assert item["statement_net_income_ytd"] == 400_000
    assert item["gross_margin_pct"] == 55
    assert item["operating_margin_pct"] == 45
    assert item["debt_ratio_pct"] == 40
    assert item["roe_pct"] > 0
    assert item["financial_quality_official"] is True


def test_fetch_preserves_cached_symbol_when_one_endpoint_fails(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"symbols": {"9999": {
        "financial_report_date": "2026-03-31", "financial_quality_official": True,
    }}}))
    def fetcher(url):
        if "t187ap06_L_ci" in url:
            return [{"年度": "115", "季別": "2", "公司代號": "2330", "營業收入": "10"}]
        raise RuntimeError("temporary")
    result = fetch_tw_official_financials(
        {"2330", "9999"}, cache_path=cache, fetcher=fetcher,
    )
    assert result["2330"]["statement_revenue_ytd"] == 10
    assert result["9999"]["financial_report_date"] == "2026-03-31"
    payload = json.loads(cache.read_text())
    assert payload["endpoint_error_count"] > 0
    assert payload["available_count"] == 2
