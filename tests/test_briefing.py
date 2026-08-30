from briefing import (
    _attach_next_session_predictions,
    _carry_completed_tw_rows,
    _carry_forward_symbol_session_regressions,
    _qualified_tw_market_top,
    _report_session_issues,
    _simulation_input_rows,
    _freeze_tw_prices_until_close,
    _tw_intraday_enrichment,
)


def _session_rows(session_dates: list[str]) -> list[dict]:
    return [
        {
            "symbol": f"TW{index:02d}.TW", "market": "TW", "type": "個股",
            "official_session_date": session_date,
        }
        for index, session_date in enumerate(session_dates)
    ]


def test_report_session_guard_rejects_mixed_and_regressed_market_data():
    previous = {"data": _session_rows(["2026-08-27"] * 10)}
    current = _session_rows(["2026-08-26"] * 8 + ["2026-08-27"] * 2)

    issues = _report_session_issues(current, previous)

    assert any("交易日混雜" in issue for issue in issues)
    assert any("主交易日倒退" in issue for issue in issues)
    assert any("個股交易日倒退" in issue for issue in issues)


def test_report_session_guard_accepts_coherent_forward_market_data():
    previous = {"data": _session_rows(["2026-08-26"] * 10)}
    current = _session_rows(["2026-08-27"] * 10)

    assert _report_session_issues(current, previous) == []


def test_single_symbol_session_regression_carries_forward_verified_row():
    previous_rows = _session_rows(["2026-08-28"] * 10)
    previous_rows[0]["price"] = 100
    current = _session_rows(["2026-08-27"] + ["2026-08-28"] * 9)
    current[0]["price"] = 90

    repaired, carried = _carry_forward_symbol_session_regressions(
        current, {"data": previous_rows}
    )

    assert carried == ["TW00.TW"]
    assert repaired[0]["official_session_date"] == "2026-08-28"
    assert repaired[0]["price"] == 100
    assert repaired[0]["session_carry_forward"] is True
    assert repaired[0]["session_carry_forward_observed_date"] == "2026-08-27"
    assert _report_session_issues(repaired, {"data": previous_rows}) == []


def test_broad_market_session_regression_is_never_carried_forward():
    previous = {"data": _session_rows(["2026-08-28"] * 10)}
    current = _session_rows(["2026-08-27"] * 10)

    repaired, carried = _carry_forward_symbol_session_regressions(current, previous)

    assert carried == []
    assert repaired == current
    assert any("主交易日倒退" in issue for issue in _report_session_issues(repaired, previous))


def test_closed_period_can_retain_verified_cohort_during_source_regression():
    previous_rows = _session_rows(["2026-08-28"] * 10)
    for index, row in enumerate(previous_rows):
        row["price"] = 100 + index
    current = _session_rows(["2026-08-26"] * 10)

    repaired, carried = _carry_forward_symbol_session_regressions(
        current,
        {"data": previous_rows},
        allow_closed_cohort_regression=True,
    )

    assert carried == [f"TW{index:02d}.TW" for index in range(10)]
    assert {row["official_session_date"] for row in repaired} == {"2026-08-28"}
    assert {row["session_carry_forward_reason"] for row in repaired} == {
        "closed_period_cohort_source_regression"
    }
    assert _report_session_issues(repaired, {"data": previous_rows}) == []


def test_closed_period_never_hides_whole_us_cohort_regression():
    previous_rows = _session_rows(["2026-08-28"] * 10)
    current = _session_rows(["2026-08-27"] * 10)
    for index, row in enumerate(previous_rows):
        row.update({"symbol": f"US{index:02d}", "market": "US"})
    for index, row in enumerate(current):
        row.update({"symbol": f"US{index:02d}", "market": "US"})

    repaired, carried = _carry_forward_symbol_session_regressions(
        current,
        {"data": previous_rows},
        allow_closed_cohort_regression=True,
    )

    assert carried == []
    assert repaired == current


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


def test_noon_and_intraday_defer_taiwan_price_fetch_until_close():
    assert _freeze_tw_prices_until_close("noon") is True
    assert _freeze_tw_prices_until_close("morning") is False
    assert _freeze_tw_prices_until_close("evening") is False
    assert _freeze_tw_prices_until_close("evening", intraday=True) is True


def test_noon_report_carries_only_last_completed_taiwan_rows():
    universe = [
        {"symbol": "1590.TW", "market": "TW"},
        {"symbol": "4979.TWO", "market": "TW"},
        {"symbol": "NVDA", "market": "US"},
    ]
    previous = {
        "1590.TW": {
            "symbol": "1590.TW", "market": "TW",
            "official_session_date": "2026-08-28", "price": 1415.0,
        },
        "4979.TWO": {"symbol": "4979.TWO", "market": "TW", "price": 300.0},
        "NVDA": {
            "symbol": "NVDA", "market": "US",
            "official_session_date": "2026-08-28", "price": 227.98,
        },
    }

    rows = _carry_completed_tw_rows(universe, previous)

    assert [row["symbol"] for row in rows] == ["1590.TW"]
    assert rows[0]["official_session_date"] == "2026-08-28"
    assert rows[0]["price_fetch_deferred_until_close"] is True


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


def test_simulation_rows_include_frozen_pick_that_fell_outside_top20():
    current_top = [
        {"symbol": "AMZN", "market": "US", "official_close_price": 101},
        {"symbol": "NVDA", "market": "US", "official_close_price": 202},
    ]
    all_rows = [
        {"symbol": "AMZN", "market": "US", "official_close_price": 999},
        {"symbol": "PATH", "market": "US", "official_close_price": 16.66},
        {"symbol": "2330.TW", "market": "TW", "official_close_price": 2390},
    ]

    result = _simulation_input_rows(current_top, all_rows)

    assert [row["symbol"] for row in result] == ["AMZN", "NVDA", "PATH", "2330.TW"]
    assert result[0]["official_close_price"] == 101
    assert next(row for row in result if row["symbol"] == "PATH")["official_close_price"] == 16.66


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
    assert tw_evening["next_session_model_version"] == "V7-shadow"
    assert tw_evening["next_session_market_model"] == "TW-NEXT-V7"
    assert tw_evening["next_session_generated_at"] == "2026-08-20 20:00:00"
    assert len(tw_evening["next_session_model_votes"]) == 10

    us_evening = _complete_shadow_row("US")
    _attach_next_session_predictions([us_evening], period="evening", generated_at="2026-08-20 20:00:00")
    assert us_evening["next_session_direction"] == "🕒 等待收盤"

    us_morning = _complete_shadow_row("US")
    _attach_next_session_predictions([us_morning], period="morning", generated_at="2026-08-21 06:00:00")
    assert us_morning["next_session_market_model"] == "US-NEXT-V6"
    assert us_morning["next_session_generated_at"] == "2026-08-21 06:00:00"


def test_intraday_refresh_carries_same_completed_close_forecast():
    prior = _complete_shadow_row("TW")
    _attach_next_session_predictions([prior], period="evening", generated_at="2026-08-20 20:00:00")
    prior["next_session_data_mode"] = "固定快照（雜湊驗證通過）"
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


def test_repeated_tw_evening_uses_same_fixed_forecast():
    prior = _complete_shadow_row("TW")
    _attach_next_session_predictions(
        [prior], period="evening", generated_at="2026-08-20 20:00:00"
    )
    prior["next_session_data_mode"] = "固定快照（雜湊驗證通過）"
    current = _complete_shadow_row("TW")
    current.update({"technical_score": 10, "volume_score": 10, "market_flow_score": 10})
    _attach_next_session_predictions(
        [current], period="evening", previous={current["symbol"]: prior},
        generated_at="2026-08-20 21:00:00",
    )
    assert current["next_session_direction"] == prior["next_session_direction"]
    assert current["next_session_up_votes"] == prior["next_session_up_votes"]
    assert current["next_session_generated_at"] == "2026-08-20 20:00:00"
    assert "重新整理不重新配分" in current["next_session_note"]


def test_unverified_mutable_tw_report_is_recomputed_at_formal_evening():
    prior = _complete_shadow_row("TW")
    _attach_next_session_predictions(
        [prior], period="evening", generated_at="2026-08-20 20:00:00"
    )
    assert prior["next_session_data_mode"] == "主要資料完整"

    current = _complete_shadow_row("TW")
    _attach_next_session_predictions(
        [current], period="evening", previous={current["symbol"]: prior},
        generated_at="2026-08-20 21:00:00",
    )

    assert current["next_session_generated_at"] == "2026-08-20 21:00:00"
    assert "next_session_model_votes" in current


def test_waiting_placeholder_is_never_mislabeled_as_a_frozen_forecast():
    prior = _complete_shadow_row("TW")
    _attach_next_session_predictions(
        [prior], period="noon", intraday=True,
        generated_at="2026-08-20 12:00:00",
    )
    assert prior["next_session_direction"] == "🕒 等待收盤"
    assert prior["next_session_generated_at"] == ""

    current = _complete_shadow_row("TW")
    _attach_next_session_predictions(
        [current], period="noon", intraday=True,
        previous={current["symbol"]: prior},
        generated_at="2026-08-20 13:00:00",
    )

    assert current["next_session_direction"] == "🕒 等待收盤"
    assert current["next_session_signal_level"] == "WAITING"
    assert "沿用" not in current["next_session_note"]


def test_tw_abstention_explains_the_actual_gate():
    row = _complete_shadow_row("TW")
    row.update({"market_data_quality_score": 20})
    _attach_next_session_predictions(
        [row], period="evening", generated_at="2026-08-20 20:00:00"
    )
    assert row["next_session_direction"] == "⚪ 棄權"
    assert "未達 75 分" in row["next_session_note"]


def test_us_fallback_feed_is_explicit_without_changing_ranking_fields():
    row = _complete_shadow_row("US")
    row.update({
        "action": "🟢 可買", "score": 71.2,
        "overall_rank": 1, "overall_ranking_score": 69.8,
        "us_live_data_available": False,
        "us_option_data_available": False,
    })
    ranking_before = {
        key: row[key]
        for key in ("action", "score", "overall_rank", "overall_ranking_score")
    }

    _attach_next_session_predictions(
        [row], period="morning", generated_at="2026-08-21 06:00:00"
    )

    assert {key: row[key] for key in ranking_before} == ranking_before
    assert row["next_session_data_mode"] == "FINRA／盤前盤後備援（SIP未取得）"
    assert row["next_session_signal_level"] in {"RESEARCH", "STRONG", "ABSTAIN"}
