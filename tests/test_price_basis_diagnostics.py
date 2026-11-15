"""Diagnostic text must never change price/source gates or model inputs."""
from copy import deepcopy
from decimal import Decimal

import pytest

from shadow_stock_conclusion import _price_diagnostic, _price_matches_close
from test_shadow_stock_conclusion import conclude, row_for


@pytest.mark.parametrize("price,close,expected", [
    (None, None, "缺少來源快照價格；缺少日K收盤價格"),
    (None, 100, "缺少來源快照價格"),
    (100, None, "缺少日K收盤價格"),
    ("", 100, "缺少來源快照價格"),
    (100, " ", "缺少日K收盤價格"),
    (0, 100, "來源快照價格無效（須為有限正數）"),
    (100, -1, "日K收盤價格無效（須為有限正數）"),
    (float("nan"), 100, "來源快照價格無效（須為有限正數）"),
    (100, float("inf"), "日K收盤價格無效（須為有限正數）"),
    (True, 100, "來源快照價格無效（須為有限正數）"),
    ([100], 100, "來源快照價格無效（須為有限正數）"),
])
def test_missing_and_invalid_are_not_reported_as_numeric_mismatch(price, close, expected):
    diagnostic = _price_diagnostic(price, {"close": close})
    assert diagnostic["status"] == "price_missing_or_invalid"
    assert diagnostic["reason"] == expected
    assert diagnostic["absolute_difference"] is None
    assert not _price_matches_close(price, close)


@pytest.mark.parametrize("market,price,close,source,difference,tolerance", [
    ("TW", 187.5, 188.5, "TWSE OpenAPI", "1.0", "0.01"),
    ("TW", 715, 718, "TPEx OpenAPI", "3", "0.01"),
    ("US", 346.62, 347.01, "Yahoo Finance daily bars", "0.39", "0.01"),
    ("US", 28.43, 28.448, "Yahoo Finance daily bars", "0.018", "0.01"),
    ("TW", 2000.021, 2000, "TWSE OpenAPI", "0.021", "0.02000"),
])
def test_known_numeric_differences_preserve_failed_gates_and_exact_inputs(
        tmp_path, market, price, close, source, difference, tolerance):
    row = row_for(market)
    row["price"] = price
    row["source_snapshot"].update(close=close, source=source)
    original = deepcopy(row)
    result = conclude(tmp_path, row)
    diagnostic = result["price_basis"]["diagnostic"]
    assert diagnostic == {
        "status": "price_basis_mismatch",
        "reason": "來源快照價格與日K收盤不一致；兩欄皆有數值，不等於缺少收盤資料",
        "close_source": source, "close_source_session_date": "2026-10-05",
        "absolute_difference": difference, "allowed_tolerance": tolerance,
    }
    assert row == original
    assert result["price_basis"]["reported_quote"] == price
    assert result["price_basis"]["completed_close"] == close
    assert result["price_basis"]["aligned"] is False
    assert next(g for g in result["gates"] if g["code"] == "price")["passed"] is False
    assert result["code"] == "insufficient"
    assert result["entry_evaluation"]["eligible"] is None
    assert result["automatic_orders"] is False
    assert result["validation"]["formal_adoption_ready"] is False
    if source == "Yahoo Finance daily bars":
        assert next(g for g in result["gates"] if g["code"] == "source")["passed"] is False


@pytest.mark.parametrize("price,close", [(17.57, 17.56), (1999.98, 2000), (1000.01, 1000)])
def test_diagnostics_keep_decimal_boundary_inclusive(price, close):
    diagnostic = _price_diagnostic(price, {"close": close})
    assert _price_matches_close(price, close)
    assert diagnostic["status"] == "aligned"
    assert Decimal(diagnostic["absolute_difference"]) <= Decimal(diagnostic["allowed_tolerance"])


def test_missing_provider_and_date_are_never_invented(tmp_path):
    row = row_for("US")
    row["price"] = 1002
    row["source_snapshot"].update(source=None, source_session_date=None)
    result = conclude(tmp_path, row)
    diagnostic = result["price_basis"]["diagnostic"]
    assert diagnostic["close_source"] is None
    assert diagnostic["close_source_session_date"] is None
    assert {g["code"] for g in result["gates"] if not g["passed"]} >= {"price", "source", "source_date"}


@pytest.mark.parametrize("price,close", [(b"100", 100), (100, b"100")])
def test_non_json_float_convertible_value_cannot_break_diagnostic(price, close):
    assert not _price_matches_close(price, close)
    diagnostic = _price_diagnostic(price, {"close": close})
    assert diagnostic["status"] == "price_comparison_unavailable"
    assert diagnostic["absolute_difference"] is None
    assert diagnostic["allowed_tolerance"] is None


@pytest.mark.parametrize("price,expected", [("1_02", "price_basis_mismatch"),
                                           ("0x64", "price_missing_or_invalid")])
def test_numeric_string_diagnostic_uses_existing_python_gate(price, expected):
    diagnostic = _price_diagnostic(price, {"close": 100})
    assert diagnostic["status"] == expected
    assert not _price_matches_close(price, 100)


def test_aligned_numbers_do_not_attest_yahoo_or_complete_quarter_validation(tmp_path):
    row = row_for("US")
    row["source_snapshot"]["source"] = "Yahoo Finance daily bars"
    result = conclude(tmp_path, row)
    assert result["price_basis"]["diagnostic"]["status"] == "aligned"
    assert result["code"] == "insufficient"
    assert not next(g for g in result["gates"] if g["code"] == "source")["passed"]
    from test_research_judgment import fixture
    from report_research import _research_judgment
    source, research, context = fixture()
    judgment = _research_judgment(source, research, {}, context)
    assert judgment["quarter"]["source_fully_verified"] is False
    assert judgment["quarter"]["investment_judgment_supported"] is False

