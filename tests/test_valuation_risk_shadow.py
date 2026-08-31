from __future__ import annotations

import json

import valuation_risk_shadow
from briefing import _update_valuation_risk_shadow_safely
from valuation_risk_shadow import update_valuation_risk_shadow


def _row(symbol: str, market_cap: float, revenue: float, per: float, fcf: float) -> dict:
    return {
        "symbol": symbol, "name": symbol, "market": "US", "type": "個股",
        "industry": "AI晶片", "official_session_date": "2026-08-28",
        "official_close_price": 100, "market_cap": market_cap,
        "total_revenue_ttm": revenue, "net_income_ttm": market_cap / per,
        "per": per, "free_cash_flow": fcf, "overall_rank": 1,
        "overall_ranking_score": 88,
    }


def _peer_rows() -> list[dict]:
    rows = [_row("EXPENSIVE", 5_000_000_000_000, 250_000_000_000, 50, 80_000_000_000)]
    rows.extend(
        _row(f"PEER{i}", 100_000_000_000, 50_000_000_000, 15, 5_000_000_000)
        for i in range(5)
    )
    return rows


def test_extreme_company_shows_years_and_peer_excess_without_claiming_certain_bubble(tmp_path):
    report = update_valuation_risk_shadow(
        tmp_path, _peer_rows(), period="evening",
        updated_at="2026-08-29 20:00:00", intraday=True,
    )
    item = next(row for row in report["data"] if row["symbol"] == "EXPENSIVE")
    assert item["sales_years"] == 20
    assert item["earnings_years"] == 50
    assert item["free_cash_flow_years"] == 62.5
    assert item["valuation_pressure_score"] >= 70
    assert item["estimated_excess_market_value"] > 0
    assert "不宣稱確定泡沫" in report["method"]["purpose"]


def test_etf_is_explicitly_separated_from_company_valuation(tmp_path):
    report = update_valuation_risk_shadow(
        tmp_path,
        [{"symbol": "SMH", "name": "SMH", "market": "US", "type": "ETF", "price": 300}],
        period="morning", updated_at="2026-08-29 06:00:00", intraday=False,
    )
    item = report["data"][0]
    assert item["status"] == "not_applicable"
    assert item["valuation_pressure_score"] is None
    assert "ETF" in item["reason"]


def test_bank_uses_financial_company_profile_instead_of_sales_fcf(tmp_path):
    rows = []
    for index in range(6):
        rows.append({
            "symbol": f"BANK{index}", "name": f"BANK{index}", "market": "US",
            "type": "個股", "industry": "Bank", "official_session_date": "2026-08-28",
            "official_close_price": 100, "market_cap": 100_000_000_000,
            "per": 12 + index, "pbr": 1.0 + index * .1, "roe_pct": 12,
        })
    report = update_valuation_risk_shadow(
        tmp_path, rows, period="morning",
        updated_at="2026-08-29 06:00:00", intraday=False,
    )
    item = report["data"][0]
    assert item["valuation_profile"] == "FINANCIAL"
    assert item["status"] == "ready"
    assert set(item["peer_medians"]) == {"pbr", "earnings_years"}


def test_official_per_and_pbr_can_classify_without_inventing_market_cap(tmp_path):
    rows = [{
        "symbol": f"TW{i}.TW", "name": f"TW{i}", "market": "TW",
        "type": "個股", "industry": "電子", "official_session_date": "2026-08-28",
        "official_close_price": 100, "per": 20 + i, "pbr": 2 + i * .1,
    } for i in range(6)]
    report = update_valuation_risk_shadow(
        tmp_path, rows, period="morning",
        updated_at="2026-08-29 06:00:00", intraday=False,
    )
    item = report["data"][0]
    assert item["status"] == "ready"
    assert item["market_cap"] is None
    assert item["market_value_gap_available"] is False
    assert item["estimated_excess_market_value"] is None
    assert "市值差額仍待" in item["reason"]


def test_official_statement_eps_and_book_value_fill_missing_per_pbr(tmp_path):
    rows = [{
        "symbol": f"OFF{i}.TW", "name": f"OFF{i}", "market": "TW",
        "type": "個股", "industry": "電子", "official_session_date": "2026-08-28",
        "official_close_price": 100, "financial_report_date": "2026-06-30",
        "eps": 2.5 + i * .1, "book_value_per_share": 40 + i,
    } for i in range(6)]
    report = update_valuation_risk_shadow(
        tmp_path, rows, period="evening",
        updated_at="2026-08-29 20:00:00", intraday=True,
    )
    item = report["data"][0]
    assert item["status"] == "ready"
    assert item["earnings_years"] == 20
    assert item["per_source"] == "price_divided_by_official_annualized_ytd_eps"
    assert item["pbr"] == 2.5
    assert item["pbr_source"] == "price_divided_by_official_book_value_per_share"


def test_tw_official_thousand_unit_is_normalized_before_market_value_display(tmp_path):
    row = {
        "symbol": "3653.TW", "name": "健策", "market": "TW", "type": "個股",
        "industry": "電子", "official_session_date": "2026-08-28",
        "official_close_price": 5785, "financial_report_date": "2026-06-30",
        "financial_statement_unit": "TWD_thousands_as_reported",
        "statement_revenue_ytd": 12_579_732,
        "statement_net_income_ytd": 3_716_689,
        "eps": 25.33, "book_value_per_share": 160.69,
    }
    report = update_valuation_risk_shadow(
        tmp_path, [row], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=True,
    )
    item = report["data"][0]
    inferred_shares = 3_716_689_000 / 25.33
    assert item["market_cap"] == 5785 * inferred_shares
    assert item["market_cap"] > 800_000_000_000
    assert item["revenue_annualized"] == 25_159_464_000
    assert item["financial_statement_unit"] == "TWD"


def test_shadow_does_not_mutate_official_rows_or_weights(tmp_path):
    rows = _peer_rows()
    before = json.loads(json.dumps(rows))
    report = update_valuation_risk_shadow(
        tmp_path, rows, period="morning",
        updated_at="2026-08-29 06:00:00", intraday=False,
    )
    assert rows == before
    assert report["official_ranking_affected"] is False
    assert report["official_weights_affected"] is False
    assert report["places_orders"] is False


def test_completed_close_freezes_then_scores_next_session_outcomes(tmp_path):
    first = _peer_rows()
    update_valuation_risk_shadow(
        tmp_path, first, period="morning",
        updated_at="2026-08-29 06:00:00", intraday=False,
    )
    second = []
    for row in first:
        current = dict(row)
        current["official_session_date"] = "2026-08-31"
        current["official_close_price"] = 102
        second.append(current)
    report = update_valuation_risk_shadow(
        tmp_path, second, period="morning",
        updated_at="2026-09-01 06:00:00", intraday=False,
    )
    assert report["validation"]["US"]["effective_samples"] == 6
    assert report["validation"]["US"]["effective_sessions"] == 1
    assert all(item["return_pct"] == 2 for item in report["outcomes"])


def test_failure_is_quarantined_from_formal_pipeline(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("valuation test failure")

    monkeypatch.setattr(valuation_risk_shadow, "update_valuation_risk_shadow", fail)
    success = _update_valuation_risk_shadow_safely(
        tmp_path, [], period="evening",
        updated_at="2026-08-29 20:00:00", intraday=False,
    )
    health = json.loads((tmp_path / "valuation_risk_shadow_health.json").read_text(encoding="utf-8"))
    assert success is False
    assert health["formal_pipeline_continues"] is True
    assert health["changes_rankings"] is False
    assert health["changes_weights"] is False
