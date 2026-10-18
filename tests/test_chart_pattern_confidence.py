from chart_pattern_confidence import attach_chart_pattern_confidence


def _row(symbol, *, confirmed=True, volume=True, direction="bullish"):
    return {
        "symbol": symbol, "name": symbol, "market": "TW", "type": "個股",
        "overall_rank": 3, "overall_ranking_score": 77.7,
        "chart_pattern_shadow_name": "雙重底" if direction == "bullish" else "雙重頂",
        "chart_pattern_shadow_direction": direction,
        "chart_pattern_shadow_status": "收盤確認突破" if confirmed else "疑似形成，等待收盤確認",
        "chart_pattern_shadow_confidence": 82,
        "chart_pattern_shadow_volume_confirmed": volume,
        "daily_volume_ratio": 1.5 if volume else .8,
        "chart_pattern_daily_k": 65, "chart_pattern_daily_d": 55,
        "chart_pattern_weekly_k": 60, "chart_pattern_weekly_d": 50,
        "rsi": 61, "chart_pattern_macd_histogram": .4,
        "macro_score": 70, "group_score": 75,
        "institution_score": 72, "fundamental_available": True,
        "fundamental_score": 68,
    }


def test_pattern_rank_is_isolated_and_orders_stronger_confirmation_first():
    strong = _row("STRONG")
    weak = _row("WEAK", confirmed=False, volume=False)
    rows = [weak, strong]
    formal = [(row["overall_rank"], row["overall_ranking_score"]) for row in rows]
    attach_chart_pattern_confidence(rows, {"signals": [], "pattern_statistics": []})
    assert strong["chart_pattern_rank_all"] == 1
    assert weak["chart_pattern_rank_all"] == 2
    assert strong["chart_pattern_rank_score"] > weak["chart_pattern_rank_score"]
    strong_components = {item["key"]: item for item in strong["chart_pattern_rank_components"]}
    weak_components = {item["key"]: item for item in weak["chart_pattern_rank_components"]}
    assert strong_components["close"]["score"] > weak_components["close"]["score"]
    assert strong["chart_pattern_rank_affects_formal"] is False
    assert [(row["overall_rank"], row["overall_ranking_score"]) for row in rows] == formal
    assert len(strong["chart_pattern_rank_components"]) == 7


def test_forward_history_and_retest_join_the_active_pattern_only():
    row = _row("TEST")
    row.update(chart_pattern_shadow_identity="same-pivots", chart_pattern_shadow_session_date="2026-09-09")
    report = {
        "sessions": {"TW": ["2026-09-08", "2026-09-09"]},
        "signals": [{
            "market": "TW", "symbol": "TEST", "pattern": "雙重底",
            "signal_date": "2026-09-08", "direction": "bullish", "pattern_identity": "same-pivots",
            "next_session_confirmation": {"retest_status": "held", "session_date": "2026-09-09"},
        }],
        "pattern_statistics": [{
            "market": "TW", "pattern": "雙重底",
            "horizons": {"1": {"sample_count": 6, "hit_rate_pct": 66.7}},
        }],
    }
    attach_chart_pattern_confidence([row], report)
    components = {item["key"]: item for item in row["chart_pattern_rank_components"]}
    assert components["retest"]["score"] == 100
    assert components["history"]["score"] == 66.7
    assert row["chart_pattern_holding_1m_score"] is not None


def test_missing_evidence_is_explicit_and_never_filled_with_neutral_50():
    row = _row("MISSING")
    for key in ("chart_pattern_weekly_k", "chart_pattern_weekly_d", "chart_pattern_macd_histogram"):
        row[key] = None
    attach_chart_pattern_confidence([row], {})
    components = {item["key"]: item for item in row["chart_pattern_rank_components"]}
    assert components["kd"]["score"] is None
    assert components["rsi_macd"]["score"] is None
    assert row["chart_pattern_rank_coverage_pct"] < 100


def test_direction_status_uses_arrows_without_removing_indicator_names():
    bullish = _row("UP")
    attach_chart_pattern_confidence([bullish], {})
    components = {item["key"]: item for item in bullish["chart_pattern_rank_components"]}
    assert components["kd"]["status"] == "日KD▲／週KD▲"
    assert components["rsi_macd"]["status"] == "RSI 61.0／MACD▲"

    bearish = _row("DOWN", direction="bearish")
    bearish.update({
        "chart_pattern_daily_k": 35, "chart_pattern_daily_d": 45,
        "chart_pattern_weekly_k": 30, "chart_pattern_weekly_d": 40,
        "chart_pattern_macd_histogram": -.4,
    })
    attach_chart_pattern_confidence([bearish], {})
    components = {item["key"]: item for item in bearish["chart_pattern_rank_components"]}
    assert components["kd"]["status"] == "日KD▼／週KD▼"
    assert components["rsi_macd"]["status"] == "RSI 61.0／MACD▼"


def test_retest_requires_same_pivots_direction_dates_and_bounded_age():
    import copy
    from chart_pattern_confidence import _latest_signal
    row = _row("TEST")
    row.update(chart_pattern_shadow_identity="current", chart_pattern_shadow_session_date="2026-09-09")
    signal = {"market": "TW", "symbol": "TEST", "pattern": "雙重底", "direction": "bullish",
              "signal_date": "2026-09-08", "pattern_identity": "current",
              "next_session_confirmation": {"session_date": "2026-09-09", "retest_status": "held"}}
    report = {"sessions": {"TW": ["2026-09-08", "2026-09-09", "2026-09-10"]}, "signals": [signal]}
    assert _latest_signal(report, row) == signal
    for key, value in (("pattern_identity", "older"), ("direction", "bearish"), ("signal_date", "2026-09-10")):
        changed = copy.deepcopy(report); changed["signals"][0][key] = value
        assert _latest_signal(changed, row) is None
    changed = copy.deepcopy(report)
    changed["signals"][0]["next_session_confirmation"]["session_date"] = "2026-09-10"
    assert _latest_signal(changed, row) is None  # future check unavailable at as-of
    for key in ("chart_pattern_shadow_identity", "chart_pattern_shadow_session_date"):
        changed_row = dict(row); changed_row.pop(key)
        assert _latest_signal(report, changed_row) is None
    report["sessions"]["TW"] += ["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"]
    row["chart_pattern_shadow_session_date"] = "2026-09-16"
    assert _latest_signal(report, row) is None


def test_no_retest_does_not_award_continuation_points():
    from chart_pattern_confidence import _component_scores
    row = _row("TEST")
    row.update(chart_pattern_shadow_identity="current", chart_pattern_shadow_session_date="2026-09-09")
    report = {"sessions": {"TW": ["2026-09-08", "2026-09-09"]}, "signals": [{
        "market": "TW", "symbol": "TEST", "pattern": "雙重底", "direction": "bullish",
        "signal_date": "2026-09-08", "pattern_identity": "current",
        "next_session_confirmation": {"session_date": "2026-09-09", "retest_status": "not_retested", "direction_confirmed": False}}]}
    item = next(c for c in _component_scores(row, report) if c["key"] == "retest")
    assert item["score"] is None
    assert item["status"] == "隔日未碰確認線；不代表方向延續"


def test_later_aggregate_is_not_applied_to_older_snapshot():
    from chart_pattern_confidence import _history_stat
    row = _row('TEST')
    row['chart_pattern_shadow_session_date'] = '2026-09-08'
    report = {'sessions': {'TW': ['2026-09-08','2026-09-09']}, 'pattern_statistics': [
        {'market': 'TW','pattern': '雙重底','horizons': {'5': {'sample_count': 20,'hit_rate_pct': 90}}}]}
    assert _history_stat(report, row)[0] is None
