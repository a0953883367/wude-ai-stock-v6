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
            "broker_date": "2026-08-21",
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


def test_intraday_snapshot_does_not_count_empty_broker_placeholders():
    previous = {
        "2330.TW": {
            "symbol": "2330.TW", "market": "TW",
            "institution_available": True,
        },
        "2317.TW": {
            "symbol": "2317.TW", "market": "TW",
            "broker_available": False,
        },
    }

    _, _, _, brokers = _tw_intraday_enrichment(previous)

    assert brokers == {}


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


def _complete_shadow_row(market: str = "TW") -> dict:
    row = {
        "symbol": "2330.TW" if market == "TW" else "NVDA",
        "market": market,
        "official_session_date": "2026-08-20",
        "price": 100,
        "avg_volume20": 1_000_000,
        "market_data_quality_score": 95,
        "market_contract_valid": True,
        "technical_score": 90,
        "volume_score": 90,
        "market_flow_score": 90,
        "institution_score": 90,
        "positioning_score": 90,
        "group_score": 90,
        "entry_score": 90,
        "macro_score": 85,
        "kline_score": 85,
        "rsi": 55,
        "change_pct": 1.5,
        "daily_volume_ratio": 1.4,
        "news_data_available": True,
        "news_penalty": 0,
    }
    if market == "TW":
        row.update({"institution_available": True, "credit_available": True})
    else:
        row.update({
            "us_live_data_available": True,
            "us_short_volume_available": True,
            "extended_hours_available": True,
            "extended_change_pct": 1.5,
        })
    return row


def test_predictions_are_frozen_at_each_markets_completed_close_checkpoint():
    tw_noon = _complete_shadow_row("TW")
    _attach_next_session_predictions([tw_noon], period="noon", generated_at="2026-08-20 12:00:00")
    assert tw_noon["next_session_direction"] == "🕒 等待收盤"

    tw_evening = _complete_shadow_row("TW")
    _attach_next_session_predictions([tw_evening], period="evening", generated_at="2026-08-20 20:00:00")
    assert tw_evening["next_session_model_version"] == "V5-shadow"
    assert tw_evening["next_session_market_model"] == "TW-NEXT-V5"
    assert tw_evening["next_session_generated_at"] == "2026-08-20 20:00:00"

    us_evening = _complete_shadow_row("US")
    _attach_next_session_predictions([us_evening], period="evening", generated_at="2026-08-20 20:00:00")
    assert us_evening["next_session_direction"] == "🕒 等待收盤"

    us_morning = _complete_shadow_row("US")
    _attach_next_session_predictions([us_morning], period="morning", generated_at="2026-08-21 06:00:00")
    assert us_morning["next_session_market_model"] == "US-NEXT-V5"
    assert us_morning["next_session_generated_at"] == "2026-08-21 06:00:00"


def test_intraday_refresh_carries_same_completed_close_forecast():
    prior = _complete_shadow_row("TW")
    _attach_next_session_predictions([prior], period="evening", generated_at="2026-08-20 20:00:00")
    current = _complete_shadow_row("TW")
    current["price"] = 101
    _attach_next_session_predictions(
        [current],
        period="noon",
        intraday=True,
        previous={current["symbol"]: prior},
        generated_at="2026-08-21 12:00:00",
    )
    assert current["next_session_generated_at"] == "2026-08-20 20:00:00"
    assert current["next_session_direction"] == prior["next_session_direction"]
    assert "不重新配分" in current["next_session_note"]
