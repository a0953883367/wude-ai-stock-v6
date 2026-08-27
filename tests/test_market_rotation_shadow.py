from copy import deepcopy
import json

from market_rotation_shadow import (
    build_market_snapshot,
    empty_state,
    select_picks,
    update_market_rotation_shadow,
)


def _rows(market="TW", session_date="2026-08-27", second=False):
    rows = []
    suffix = ".TW" if market == "TW" else ""
    for index in range(30):
        if index < 10:
            industry, change, volume, base = "點火族", (4.0 if second else 3.0), 1.8, 80
            institution, growth = 1_000_000, 85
        elif index < 20:
            industry, change, volume, base = "退潮族", (-2.0 if second else -1.0), 0.7, 82
            institution, growth = -1_000_000, 35
        else:
            industry, change, volume, base = "整理族", 0.2, 1.0, 70
            institution, growth = 0, 55
        rows.append({
            "symbol": f"{1000 + index}{suffix}" if market == "TW" else f"S{index}",
            "name": f"測試{index}", "market": market, "type": "個股",
            "industry": industry, "change_pct": change,
            "daily_volume_ratio": volume,
            "breakout20": index < 10,
            "rsi": 65 if index < 10 else 75,
            "kline_pattern": "突破收高" if index < 10 else "上影壓力",
            "short_term_ranking_score": base,
            "short_term_score": base, "short_term_rank_tier": 2,
            "official_session_date": session_date,
            "official_open_price": 100,
            "official_close_price": 100 + change,
            "institution_available": market == "TW",
            "institution_net": institution if market == "TW" else None,
            "institution_buy_days_5": 3 if market == "TW" and index < 10 else 1 if market == "TW" else None,
            "institution_5d": institution * 5 if market == "TW" else None,
            "growth_score": growth if market == "US" else None,
            "revenue_yoy_pct": 30 if growth >= 60 else -5,
        })
    return rows


def test_first_snapshot_collects_baseline_without_backdating_stage():
    rows = _rows()
    before = deepcopy(rows)
    snapshot = build_market_snapshot(rows, "TW")
    assert snapshot["session_date"] == "2026-08-27"
    assert snapshot["market_state"] == "broad_bull"
    assert snapshot["sectors"][0]["industry"] == "點火族"
    assert snapshot["sectors"][0]["stage"] == "collecting"
    assert rows == before


def test_second_snapshot_can_confirm_expansion_without_future_rows():
    first = build_market_snapshot(_rows(), "TW")
    second = build_market_snapshot(
        _rows(session_date="2026-08-28", second=True), "TW", first
    )
    strong = next(item for item in second["sectors"] if item["industry"] == "點火族")
    assert strong["stage"] == "expansion"
    assert strong["rotation_score"] >= 65


def test_rotation_overlay_is_separate_from_unchanged_baseline_order():
    rows = _rows()
    snapshot = build_market_snapshot(rows, "TW")
    baseline = select_picks(rows, "TW", snapshot, rotation_overlay=False)
    rotation = select_picks(rows, "TW", snapshot, rotation_overlay=True)
    assert {pick["industry"] for pick in baseline} == {"退潮族"}
    assert {pick["industry"] for pick in rotation} == {"點火族"}
    assert all(pick["base_score"] == 82 for pick in baseline)


def test_tw_strict_and_practical_qualifications_are_shadow_only():
    rows = _rows()
    snapshot = build_market_snapshot(rows, "TW")
    strict = select_picks(
        rows, "TW", snapshot, rotation_overlay=True,
        qualification_mode="strict_5of5",
    )
    practical = select_picks(
        rows, "TW", snapshot, rotation_overlay=True,
        qualification_mode="practical_4of5",
    )
    assert len(strict) == 10
    assert len(practical) == 10
    assert all(pick["industry"] == "點火族" for pick in strict)
    assert all(pick["tw_five_condition"]["strict_5of5"] for pick in strict)

    no_institution = deepcopy(rows)
    for row in no_institution[:10]:
        row["institution_buy_days_5"] = None
        row["institution_5d"] = None
    snapshot = build_market_snapshot(no_institution, "TW")
    assert select_picks(
        no_institution, "TW", snapshot, rotation_overlay=True,
        qualification_mode="strict_5of5",
    ) == []
    practical = select_picks(
        no_institution, "TW", snapshot, rotation_overlay=True,
        qualification_mode="practical_4of5",
    )
    assert len(practical) == 10
    assert all(pick["tw_five_condition"]["status"] == "practical_4of5" for pick in practical)


def test_tw_and_us_use_different_market_specific_evidence():
    tw = build_market_snapshot(_rows("TW"), "TW")
    us = build_market_snapshot(_rows("US", "2026-08-26"), "US")
    tw_strong = next(item for item in tw["sectors"] if item["industry"] == "點火族")
    us_strong = next(item for item in us["sectors"] if item["industry"] == "點火族")
    assert tw_strong["market_specific_evidence_score"] == 100
    assert us_strong["market_specific_evidence_score"] > 80
    assert empty_state()["policy"]["market_isolation"].startswith("台股使用法人")


def test_forward_ab_does_not_settle_on_same_session(tmp_path):
    first_rows = _rows()
    state = update_market_rotation_shadow(
        tmp_path, first_rows, period="evening",
        updated_at="2026-08-27 20:00:00",
    )
    tw = state["markets"]["TW"]
    assert tw["pending"]["signal_session_date"] == "2026-08-27"
    assert tw["outcomes"] == []

    state = update_market_rotation_shadow(
        tmp_path, first_rows, period="evening",
        updated_at="2026-08-27 20:30:00",
    )
    assert state["markets"]["TW"]["outcomes"] == []

    state = update_market_rotation_shadow(
        tmp_path, _rows(session_date="2026-08-28", second=True),
        period="evening", updated_at="2026-08-28 20:00:00",
    )
    tw = state["markets"]["TW"]
    assert len(tw["outcomes"]) == 1
    assert tw["outcomes"][0]["session_date"] == "2026-08-28"
    assert tw["summary"]["formal_ranking_locked"] is True
    persisted = json.loads((tmp_path / "market_rotation_shadow.json").read_text())
    assert persisted["policy"]["automatic_merge"] is False
    assert persisted["policy"]["broker_orders"] is False


def test_wrong_period_cannot_freeze_market(tmp_path):
    state = update_market_rotation_shadow(
        tmp_path, _rows("US", "2026-08-26"), period="evening",
        updated_at="2026-08-27 20:00:00",
    )
    assert state["markets"]["US"]["snapshots"] == []


def test_missing_next_session_is_never_replaced_by_later_prices(tmp_path):
    update_market_rotation_shadow(
        tmp_path, _rows(), period="evening", updated_at="2026-08-27 20:00:00",
    )
    missing = _rows(session_date="2026-08-28", second=True)
    missing[10]["official_open_price"] = None
    update_market_rotation_shadow(
        tmp_path, missing, period="evening", updated_at="2026-08-28 20:00:00",
    )
    state = update_market_rotation_shadow(
        tmp_path, _rows(session_date="2026-08-31", second=True),
        period="evening", updated_at="2026-08-31 20:00:00",
    )
    outcomes = state["markets"]["TW"]["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "data_insufficient"
    assert outcomes[0]["session_date"] == "2026-08-28"
    assert state["markets"]["TW"]["summary"]["valid_trading_days"] == 0
    assert state["markets"]["TW"]["pending"]["signal_session_date"] == "2026-08-31"
