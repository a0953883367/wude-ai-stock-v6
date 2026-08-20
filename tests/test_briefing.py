from briefing import (
    _attach_next_session_predictions,
    _qualified_tw_market_top,
    _tw_intraday_enrichment,
)


def test_intraday_snapshot_reuses_enrichment_without_stale_price_fields():
    previous = {
        "6116.TW": {
            "symbol": "6116.TW",
            "market": "TW",
            "price": 14.4,
            "rsi": 65,
            "available": 1.0,
            "foreign": -2_292_868,
            "institution_5d": -4_000_000,
            "credit_available": 1.0,
            "margin_1d_change": -387,
            "fundamental_available": 1.0,
            "per": 12.5,
            "revenue_yoy_pct": 7.2,
            "broker_available": True,
            "top_brokers_buy": [{"name": "A", "net": 10}],
        },
        "AAPL": {"symbol": "AAPL", "market": "US", "foreign": 999},
    }

    institutions, credit, fundamentals, brokers = _tw_intraday_enrichment(previous)

    assert institutions["6116"]["foreign"] == -2_292_868
    assert institutions["6116"]["institution_5d"] == -4_000_000
    assert credit["6116"]["margin_1d_change"] == -387
    assert fundamentals["6116"]["per"] == 12.5
    assert fundamentals["6116"]["revenue_yoy_pct"] == 7.2
    assert brokers["6116"]["broker_available"] is True
    assert "price" not in institutions["6116"]
    assert "rsi" not in institutions["6116"]
    assert "AAPL" not in institutions


def test_market_top_excludes_observation_blocked_etf_and_us_rows():
    qualified = {
        "symbol": "2330.TW", "market": "TW", "type": "個股",
        "overall_rank_tier": 2,
    }
    rows = [
        {"symbol": "6116.TW", "market": "TW", "type": "個股", "overall_rank_tier": 0},
        {"symbol": "2002.TW", "market": "TW", "type": "個股", "overall_rank_tier": 1},
        {"symbol": "0050.TW", "market": "TW", "type": "ETF", "overall_rank_tier": 2},
        {"symbol": "AAPL", "market": "US", "type": "個股", "overall_rank_tier": 2},
        qualified,
    ]

    assert _qualified_tw_market_top(rows) == [qualified]


def test_next_session_shadow_fields_do_not_change_ranking_or_action():
    row = {
        "symbol": "HOT", "action": "🟢 可買", "overall_rank": 1,
        "market_data_quality_score": 90, "market_contract_valid": True,
        "technical_score": 90, "volume_score": 90, "market_flow_score": 85,
        "positioning_score": 80, "group_score": 80, "entry_score": 90,
        "macro_score": 55, "rsi": 84, "change_pct": 8,
        "daily_volume_ratio": .7, "breakout20": True,
    }
    _attach_next_session_predictions([row])
    assert row["action"] == "🟢 可買"
    assert row["overall_rank"] == 1
    assert row["next_session_direction"] == "⚪ 棄權"
    assert set(row["next_session_tracks"]) == {"overnight", "session", "full_day"}
