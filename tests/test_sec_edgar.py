from datetime import date

from sec_edgar import (
    fetch_sec_company_fundamentals,
    merge_sec_fallback,
    normalize_company_facts,
    normalize_ticker_map,
)


def _fact(unit, values):
    return {"units": {unit: values}}


def _annual(value, end, filed, fy=2025):
    return {
        "val": value, "end": end, "filed": filed,
        "form": "10-K", "fp": "FY", "fy": fy,
    }


def _companyfacts(currency="USD"):
    return {
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _fact(currency, [
                _annual(1000, "2024-12-31", "2025-02-01", 2024),
                _annual(1200, "2025-12-31", "2026-02-01", 2025),
            ]),
            "NetIncomeLoss": _fact(currency, [
                _annual(100, "2024-12-31", "2025-02-01", 2024),
                _annual(150, "2025-12-31", "2026-02-01", 2025),
            ]),
            "GrossProfit": _fact(currency, [
                _annual(600, "2025-12-31", "2026-02-01"),
            ]),
            "OperatingIncomeLoss": _fact(currency, [
                _annual(240, "2025-12-31", "2026-02-01"),
            ]),
            "NetCashProvidedByUsedInOperatingActivities": _fact(currency, [
                _annual(300, "2025-12-31", "2026-02-01"),
            ]),
            "PaymentsToAcquirePropertyPlantAndEquipment": _fact(currency, [
                _annual(80, "2025-12-31", "2026-02-01"),
            ]),
            "Assets": _fact(currency, [
                _annual(2000, "2025-12-31", "2026-02-01"),
            ]),
            "Liabilities": _fact(currency, [
                _annual(800, "2025-12-31", "2026-02-01"),
            ]),
            "StockholdersEquity": _fact(currency, [
                _annual(1200, "2025-12-31", "2026-02-01"),
            ]),
            "EarningsPerShareDiluted": _fact("USD/shares", [
                _annual(3.25, "2025-12-31", "2026-02-01"),
            ]),
            "WeightedAverageNumberOfDilutedSharesOutstanding": _fact("shares", [
                _annual(100, "2025-12-31", "2026-02-01"),
            ]),
        }}
    }


def test_ticker_map_normalizes_cik():
    result = normalize_ticker_map({"0": {"ticker": "MSFT", "cik_str": 789019}})
    assert result == {"MSFT": "0000789019"}


def test_companyfacts_builds_financial_fallback():
    result = normalize_company_facts(_companyfacts())
    assert result["revenue_yoy_pct"] == 20
    assert result["eps_yoy_pct"] == 50
    assert result["gross_margin_pct"] == 50
    assert result["operating_margin_pct"] == 20
    assert result["debt_ratio_pct"] == 40
    assert result["free_cash_flow"] == 220
    assert result["financial_report_date"] == "2025-12-31"
    assert result["us_sec_balance_sheet_date"] == "2025-12-31"
    assert result["total_revenue_ttm"] is None
    assert result["us_sec_official"] is True


def test_non_usd_filing_keeps_ratios_but_not_usd_absolute_values():
    result = normalize_company_facts(_companyfacts("TWD"))
    assert result["revenue_yoy_pct"] == 20
    assert result["gross_margin_pct"] == 50
    assert result["total_revenue_ttm"] is None
    assert result["operating_cash_flow"] is None
    assert result["free_cash_flow"] is None
    assert result["us_sec_filing_currency"] == "TWD"


def test_sec_fallback_never_replaces_primary_values():
    merged = merge_sec_fallback(
        {"revenue_yoy_pct": 33, "per": 20, "us_company_data_source": "Yahoo Finance"},
        normalize_company_facts(_companyfacts()),
    )
    assert merged["revenue_yoy_pct"] == 33
    assert merged["per"] == 20
    assert merged["gross_margin_pct"] == 50
    assert merged["fundamental_available"] == 1
    assert merged["financial_quality_available"] == 1
    assert merged["us_sec_fallback_used"] is True
    assert "gross_margin_pct" in merged["us_sec_fallback_field_names"]
    assert merged["us_company_data_source"] == "Yahoo Finance + SEC EDGAR"


def test_refresh_replaces_only_prior_sec_filled_fields():
    first = merge_sec_fallback(
        {"revenue_yoy_pct": 33, "per": 20},
        normalize_company_facts(_companyfacts()),
    )
    changed = _companyfacts()
    changed["facts"]["us-gaap"]["GrossProfit"] = _fact("USD", [
        _annual(720, "2025-12-31", "2026-02-01"),
    ])
    second = merge_sec_fallback(first, normalize_company_facts(changed))
    assert second["revenue_yoy_pct"] == 33
    assert second["per"] == 20
    assert second["gross_margin_pct"] == 60


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_fetch_uses_official_endpoints_then_daily_cache(tmp_path):
    calls = []

    def getter(url, **kwargs):
        calls.append(url)
        if "company_tickers" in url:
            return _Response({"0": {"ticker": "MSFT", "cik_str": 789019}})
        return _Response(_companyfacts())

    cache_path = tmp_path / "sec.json"
    first = fetch_sec_company_fundamentals(
        ["MSFT"], cache_path=cache_path, getter=getter, sleeper=lambda _: None,
    )
    second = fetch_sec_company_fundamentals(
        ["MSFT"], cache_path=cache_path,
        getter=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache miss")),
        sleeper=lambda _: None,
    )
    assert first["MSFT"]["us_sec_data_available"] is True
    assert second == first
    assert len(calls) == 2
    assert date.fromisoformat(first["MSFT"]["financial_report_date"])
