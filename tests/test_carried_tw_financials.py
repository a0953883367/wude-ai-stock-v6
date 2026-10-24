"""Exercise the report loader/carry/valuation boundary, without provider calls."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import briefing
from valuation_risk_shadow import _company_values

SOURCE = "MOPS via TWSE/TPEx OpenAPI"


def official(symbol="TEST.TW", **changes):
    return {
        "symbol": symbol, "market": "TW", "type": "個股",
        "official_session_date": "2026-10-19", "official_close_price": 100,
        "price": 100, "ma20": 98, "support1": 95, "resistance1": 105,
        "financial_report_date": "2026-06-30",
        "financial_quality_source": SOURCE, "financial_quality_official": True,
        "financial_statement_unit": "TWD_thousands_as_reported",
        "total_assets": 5000, "total_debt": 2000, "total_liabilities": 2000000,
        "debt_ratio_pct": 40, "roe_pct": 12, "operating_cash_flow": 500000,
        "financial_quality_fields": ["total_assets", "total_debt", "debt_ratio_pct", "roe_pct"],
        "financial_quality_field_count": 4, "financial_quality_available": 1,
        **changes,
    }


def test_loader_completed_session_carry_preserves_frozen_vintage_and_mixed_units(tmp_path, monkeypatch):
    rows = [official(), official("MISSING.TW", total_liabilities=None)]
    path = tmp_path / "all_analysis.json"
    original = json.dumps({"updated_at": "2026-10-19 20:00:00", "data": rows})
    path.write_text(original)
    monkeypatch.setattr(briefing, "SETTINGS", SimpleNamespace(reports_dir=tmp_path))
    previous = briefing._previous_rows()
    before = deepcopy(previous)
    # A newer cache exists but must never supply financial data to carried rows.
    (tmp_path / "tw_financial_official_cache.json").write_text(json.dumps({
        "schema_version": 2, "symbols": {"TEST": {
            "financial_report_date": "2026-09-30", "total_liabilities": 9000,
        }},
    }))
    carried = briefing._carry_completed_tw_rows(rows, previous)
    for row in carried:
        assert "total_debt" not in row and "total_liabilities" not in row
        assert row["financial_quality_fields"] == ["debt_ratio_pct", "roe_pct", "total_assets"]
        assert row["financial_quality_field_count"] == 3
        assert row["financial_quality_available"] == 1
        assert row["carried_financial_exclusions"]["total_liabilities"]
        assert row["after_close_price_policy"] == "carried_completed_session"
        for key in ("price", "official_session_date", "official_close_price", "ma20",
                    "support1", "resistance1", "financial_report_date",
                    "financial_statement_unit", "debt_ratio_pct", "roe_pct", "operating_cash_flow"):
            assert row[key] == before[row["symbol"]][key]
        assert _company_values({**row, "market_cap": 10000000, "total_cash": 1000000})["enterprise_value"] is None
    assert previous == before
    assert path.read_text() == original
    repeated = briefing._carry_completed_tw_rows(rows, {r["symbol"]: r for r in carried})
    assert repeated == carried
    # This is the public row shape; removed aliases cannot survive serialization.
    assert all("total_debt" not in r for r in json.loads(json.dumps(carried)))


@pytest.mark.parametrize("unit,expected", [("TWD_thousands_as_reported", 2000), ("TWD", None), (None, None)])
def test_carried_schema2_liabilities_require_official_manifest_and_unit(unit, expected):
    prior = official(total_debt=None, total_liabilities=2000, financial_statement_unit=unit,
                     financial_quality_fields=["total_assets", "total_liabilities", "debt_ratio_pct"])
    prior.pop("total_debt")
    row = briefing._carry_completed_tw_rows([prior], {prior["symbol"]: prior})[0]
    assert row.get("total_liabilities") == expected
    assert ("total_liabilities" in row["financial_quality_fields"]) == (expected is not None)
    assert row["financial_statement_unit"] == unit
    assert row["financial_report_date"] == "2026-06-30"


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True])
def test_unavailable_fields_are_not_counted_as_financial_coverage(value):
    prior = official(total_assets=value, total_debt=value, total_liabilities=value,
                     roe_pct=None, debt_ratio_pct=None, operating_cash_flow=None,
                     financial_quality_fields=["total_assets", "total_debt", "roe_pct", "missing"])
    row = briefing._carry_completed_tw_rows([prior], {prior["symbol"]: prior})[0]
    assert row["financial_quality_fields"] == []
    assert row["financial_quality_field_count"] == 0
    assert row["financial_quality_available"] == 0


def test_other_sources_and_non_tw_rows_are_not_reinterpreted():
    row = official(financial_quality_source="Other provider", total_debt=500, total_liabilities=2000)
    carried = briefing._carry_completed_tw_rows([row], {row["symbol"]: row})[0]
    assert carried == {**row, "after_close_price_policy": "carried_completed_session",
                       "price_fetch_deferred_until_close": True}
    us = official("TEST", market="US")
    assert briefing._carry_completed_tw_rows([us], {"TEST": us}) == []


def test_closed_cohort_regression_cannot_reintroduce_legacy_debt():
    prior = official()
    current = {**prior, "official_session_date": "2026-10-16", "price": 80,
               "financial_report_date": "2026-09-30"}
    before = deepcopy(prior)
    rows, carried = briefing._carry_forward_symbol_session_regressions(
        [current], {"data": [prior]}, allow_closed_cohort_regression=True,
    )
    assert carried == ["TEST.TW"]
    assert "total_debt" not in rows[0] and "total_liabilities" not in rows[0]
    assert rows[0]["price"] == 100
    assert rows[0]["financial_report_date"] == "2026-06-30"
    assert rows[0]["session_carry_forward_reason"] == "closed_period_cohort_source_regression"
    assert prior == before


@pytest.mark.parametrize("changes", [
    {"financial_quality_fields": None},
    {"financial_quality_official": False},
    {"financial_report_date": None},
])
def test_matching_numeric_value_is_not_a_substitute_for_provenance(changes):
    prior = official(total_liabilities=2000, financial_quality_fields=["total_liabilities"])
    prior.update(changes)
    row = briefing._carry_completed_tw_rows([prior], {prior["symbol"]: prior})[0]
    assert "total_liabilities" not in row


def test_verified_zero_liabilities_and_original_cash_flow_remain_available():
    prior = official(total_liabilities=0, financial_quality_fields=["total_liabilities"])
    prior.pop("total_debt")
    row = briefing._carry_completed_tw_rows([prior], {prior["symbol"]: prior})[0]
    assert row["total_liabilities"] == 0
    assert row["financial_quality_field_count"] == 1
    assert row["financial_quality_available"] == 1
    assert row["operating_cash_flow"] == 500000
