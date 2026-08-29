import json
from pathlib import Path

import pandas as pd

from inverse_etf_shadow import (
    build_mapping_database,
    build_product_rows,
    empty_state,
    load_catalog,
    update_inverse_etf_shadow,
)


def test_current_374_rows_are_mapped_without_touching_formal_models():
    rows = json.loads(Path("reports/all_analysis.json").read_text(encoding="utf-8"))["data"]
    database = build_mapping_database(rows, updated_at="test")
    assert database["universe_count"] == 374
    assert database["summary"]["TW"] == 188
    assert database["summary"]["US"] == 186
    assert len(database["mappings"]) == len({row["symbol"] for row in rows})
    assert database["policy"]["formal_ranking_locked"] is True
    assert database["policy"]["flow_weight_shadow_unchanged"] is True
    assert database["policy"]["medium_45_day_unchanged"] is True
    assert database["policy"]["long_6_month_unchanged"] is True


def test_taiwan_never_claims_minus_two_or_three_and_us_has_verified_products():
    catalog = load_catalog()
    assert all(p["daily_target"] == -1 for p in catalog["products"] if p["market"] == "TW")
    assert {-2, -3}.issubset({p["daily_target"] for p in catalog["products"] if p["market"] == "US"})
    assert {"SQQQ", "SOXS", "TECS", "SPXS"}.issubset({p["symbol"] for p in catalog["products"]})


def test_product_price_extractor_requires_exact_session():
    catalog = {"products": [{"symbol": "SQQQ", "market": "US", "group": "us_nasdaq", "daily_target": -3}]}
    histories = {"SQQQ": pd.DataFrame({"open": [10], "close": [11]}, index=pd.to_datetime(["2026-08-28"]))}
    assert build_product_rows(catalog, histories, {"US": "2026-08-29"})["SQQQ"]["data_complete"] is False
    assert build_product_rows(catalog, histories, {"US": "2026-08-28"})["SQQQ"]["close"] == 11


def test_shadow_uses_actual_etf_price_not_underlying_times_leverage(tmp_path):
    catalog = {"version": 1, "products": [{"symbol": "SQQQ", "name": "SQQQ", "market": "US", "daily_target": -3, "group": "us_nasdaq", "benchmark": "NDX", "official_source": "official"}]}
    rows = [{"symbol": f"X{i}", "name": f"X{i}", "market": "US", "type": "個股", "theme": "NASDAQ", "industry": "科技ETF", "official_session_date": "2026-08-28", "change_pct": -4, "breakdown20": True} for i in range(4)]
    signal = {"SQQQ": {**catalog["products"][0], "session_date": "2026-08-28", "open": 10, "close": 10, "data_complete": True}}
    update_inverse_etf_shadow(tmp_path, rows, signal, period="morning", updated_at="signal", catalog=catalog)
    next_rows = [{**row, "official_session_date": "2026-08-31", "change_pct": 10} for row in rows]
    next_price = {"SQQQ": {**catalog["products"][0], "session_date": "2026-08-31", "open": 10, "close": 10.5, "data_complete": True}}
    state = update_inverse_etf_shadow(tmp_path, next_rows, next_price, period="morning", updated_at="next", catalog=catalog)
    outcome = state["markets"]["US"]["cohorts"][0]["outcomes"]["1"]
    assert outcome["actual_etf_return_pct"] == 5.0
    assert outcome["price_source"] == "inverse_etf_own_ohlc"
    assert state["policy"]["daily_return_times_leverage_forbidden"] is True


def test_empty_state_is_a_separate_book():
    state = empty_state()
    assert state["mode"] == "isolated_inverse_etf_shadow"
    assert state["policy"]["broker_orders"] is False
    assert "flow_weight_shadow" not in state
