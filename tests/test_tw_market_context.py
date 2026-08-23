from model_lab import model_predictions
from strategy import _tw_layered_score
from tw_market_context import attach_tw_context, build_tw_market_context


def _tw_rows():
    rows = []
    for index in range(12):
        rows.append({
            "symbol": f"{2300 + index}.TW",
            "market": "TW",
            "type": "個股",
            "industry": "半導體" if index < 6 else "面板",
            "change_pct": 3.0 if index < 6 else -2.0,
            "official_session_date": "2026-08-21",
        })
    for index in range(12):
        rows.append({
            "symbol": f"{6100 + index}.TWO",
            "market": "TW",
            "type": "個股",
            "industry": "光通訊" if index < 6 else "生技",
            "change_pct": 2.0 if index < 6 else -1.0,
            "official_session_date": "2026-08-21",
        })
    rows.append({
        "symbol": "NVDA", "market": "US", "type": "個股",
        "industry": "AI半導體", "change_pct": 5.0,
        "official_session_date": "2026-08-21",
    })
    return rows


def test_context_uses_twse_tpex_and_never_attaches_to_us():
    rows = _tw_rows()
    context = build_tw_market_context(rows, {
        "加權指數": {"change_pct": 1.0, "session_date": "2026-08-21"},
        "櫃買指數": {"change_pct": -0.5, "session_date": "2026-08-21"},
    })
    attach_tw_context(rows, context)

    twse = next(row for row in rows if row["symbol"] == "2300.TW")
    tpex = next(row for row in rows if row["symbol"] == "6100.TWO")
    us = next(row for row in rows if row["symbol"] == "NVDA")
    assert twse["tw_exchange"] == "TWSE"
    assert tpex["tw_exchange"] == "TPEx"
    assert twse["tw_market_context_score"] != tpex["tw_market_context_score"]
    assert twse["tw_sector_context_score"] > 50
    assert "tw_market_context_score" not in us


def test_relative_strength_compares_stock_with_same_exchange_and_sector():
    rows = _tw_rows()
    for row in rows:
        if row["market"] == "TW":
            row["tw_return_5d_pct"] = 1.0
            row["tw_return_20d_pct"] = 2.0
    leader = next(row for row in rows if row["symbol"] == "2300.TW")
    leader["tw_return_5d_pct"] = 10.0
    leader["tw_return_20d_pct"] = 18.0
    context = build_tw_market_context(rows, {
        "加權指數": {"change_pct": 1.0, "session_date": "2026-08-21"},
        "櫃買指數": {"change_pct": -.5, "session_date": "2026-08-21"},
    })
    attach_tw_context(rows, context)
    assert leader["tw_relative_strength_available"] is True
    assert leader["tw_relative_strength_score"] >= 65
    assert leader["tw_market_relative_5d_pct"] > 0
    assert leader["tw_sector_relative_20d_pct"] > 0
    assert "領先" in leader["tw_relative_strength_status"]


def test_broad_same_day_breadth_is_valid_fallback_when_index_is_missing():
    rows = [
        {
            "symbol": f"{1100 + index}.TW",
            "market": "TW",
            "type": "個股",
            "industry": "測試",
            "change_pct": 1.0 if index < 14 else -0.5,
            "official_session_date": "2026-08-21",
        }
        for index in range(20)
    ]
    context = build_tw_market_context(rows, {})
    assert context["available"] is True
    assert context["exchanges"]["TWSE"]["available"] is True
    assert "指數暫缺" in context["exchanges"]["TWSE"]["source"]


def test_small_sample_cannot_masquerade_as_market_context():
    rows = [
        {
            "symbol": f"{1100 + index}.TW",
            "market": "TW",
            "type": "個股",
            "industry": "測試",
            "change_pct": 1.0,
            "official_session_date": "2026-08-21",
        }
        for index in range(9)
    ]
    context = build_tw_market_context(rows, {})
    assert context["available"] is False
    assert context["exchanges"]["TWSE"]["available"] is False


def test_stock_layer_is_70_20_10_and_missing_context_is_not_fabricated():
    score, weights = _tw_layered_score(
        80, 60, 40, is_etf=False,
        sector_available=True, market_available=True,
    )
    assert weights == {"individual": 70, "sector": 20, "market": 10}
    assert score == 72.0

    original, missing_weights = _tw_layered_score(
        80, 0, 0, is_etf=False,
        sector_available=False, market_available=False,
    )
    assert original == 80.0
    assert missing_weights == {"individual": 100, "sector": 0, "market": 0}


def test_etf_market_layer_is_larger_than_stock_market_layer():
    _stock, stock_weights = _tw_layered_score(
        70, 60, 40, is_etf=False,
        sector_available=True, market_available=True,
    )
    _etf, etf_weights = _tw_layered_score(
        70, 60, 40, is_etf=True,
        sector_available=True, market_available=True,
    )
    assert stock_weights["market"] == 10
    assert etf_weights["market"] == 25


def test_weak_tw_market_does_not_erase_strong_stock_with_strong_peers():
    row = {
        "market": "TW", "price": 100, "avg_volume20": 1_000_000,
        "market_data_quality_score": 95, "market_contract_valid": True,
        "technical_score": 90, "volume_score": 88, "market_flow_score": 85,
        "institution_score": 85, "positioning_score": 80, "entry_score": 85,
        "kline_score": 80, "rsi": 58, "change_pct": 2.0,
        "daily_volume_ratio": 1.5, "news_data_available": True,
        "institution_available": True, "credit_available": True,
        "tw_sector_context_available": True, "tw_sector_context_score": 85,
        "tw_market_context_available": True, "tw_market_context_score": 32,
    }
    votes = model_predictions(row)
    assert sum(vote["direction"] == "UP" for vote in votes.values()) >= 6


def test_us_predictions_ignore_taiwan_context_fields():
    row = {
        "market": "US", "price": 100, "avg_volume20": 1_000_000,
        "market_data_quality_score": 95, "market_contract_valid": True,
        "technical_score": 75, "volume_score": 70, "market_flow_score": 68,
        "positioning_score": 65, "group_score": 66, "entry_score": 70,
        "macro_score": 62, "kline_score": 65, "rsi": 58,
        "change_pct": 1.0, "daily_volume_ratio": 1.2,
        "news_data_available": True, "us_live_data_available": True,
        "us_short_volume_available": True, "extended_hours_available": True,
        "extended_change_pct": 0.5,
    }
    baseline = model_predictions(dict(row))
    row.update({
        "tw_sector_context_available": True,
        "tw_sector_context_score": 0,
        "tw_market_context_available": True,
        "tw_market_context_score": 0,
    })
    assert model_predictions(row) == baseline
