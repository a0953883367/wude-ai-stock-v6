from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from market_calendar import OfficialMarketCalendar
from tw_daily_shadow_attestation import parse_official_price_rows
from tw_prospective_registry import build_tw_prospective_registry, validate_tw_prospective_registry


REGISTERED = datetime(2026, 10, 10, 1, tzinfo=timezone.utc)
NEXT_CLOSE = datetime(2026, 10, 12, 5, 35, tzinfo=timezone.utc)


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def calendar(tmp_path):
    sessions = [f"2026-10-{day:02}" for day in (8, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27)]
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"version": 1, "markets": {"TW": {"years": {"2026": {
        "status": "verified_twse_tpex", "sessions": sessions, "sources": ["TWSE", "TPEx"],
    }}}}}))
    return OfficialMarketCalendar(path, auto_refresh=False, allow_network=False)


def official(session="2026-10-08", close=110, fetched=REGISTERED, *, tpex=False):
    source = "TPEx OpenAPI" if tpex else "TWSE OpenAPI"
    raw = ({"SecuritiesCompanyCode": "6488", "Date": session,
            "Open": str(close), "High": str(close + 2), "Low": str(close - 2),
            "Close": str(close), "TradingShares": "1000"} if tpex else
           {"Code": "2330", "Date": session, "OpeningPrice": str(close),
            "HighestPrice": str(close + 2), "LowestPrice": str(close - 2),
            "ClosingPrice": str(close), "TradeVolume": "1000"})
    return next(iter(parse_official_price_rows(source, [raw], fetched_at=fetched.isoformat()).values()))


def candidate(*, tpex=False):
    symbol = "6488.TWO" if tpex else "2330.TW"
    levels = {"entry_low": 100, "entry_high": 105, "stop": 90, "target1": 125, "target2": 140}
    identity = {"market": "TW", "symbol": symbol, "horizon": "short",
                "source_session_date": "2026-10-08", "source_batch_at": "2026-10-09 17:00:00",
                "source_price": 110, "levels": levels}
    return {"market": "TW", "symbol": symbol, "risk_blocks": [], "plans": {"short": {
        **levels, "horizon": "short", "active_entry_plan": True,
        "buy_window_sessions": 2, "max_hold_sessions": 5,
        "plan_quality": {"entry_eligible": True},
        "conclusion": {"code": "wait", "plan_assessment": {"data_gates_passed": True},
                       "evaluated_at": "2026-10-09T09:00:00Z",
                       "expires_at": "2026-10-09T12:00:00Z",
                       "evidence": {"news_expires_at": "2026-10-09T12:00:00Z"},
                       "plan_snapshot": {"id": digest(identity), **identity}},
    }}}


def risk(now=NEXT_CLOSE, session="2026-10-12", *, symbol="2330.TW"):
    return {"market": "TW", "symbol": symbol, "source_session_date": session,
            "observed_at": now.isoformat(), "source_validity": "verified", "risk_blocks": [],
            "news": {"status": "verified", "observed_at": now.isoformat(),
                     "expires_at": (now + timedelta(hours=18)).isoformat(),
                     "source_ids": ["existing_verified_news"]},
            "corporate_actions": {"status": "clear", "observed_at": now.isoformat(),
                                  "source_ids": ["existing_official_ca"], "coverage_from": "2026-10-08",
                                  "through_session": session}}


def run(cal, previous=None, rows=None, quote=None, current_risk=None, now=REGISTERED):
    quotes = {} if quote is None else {quote["symbol"]: quote}
    risks = {} if current_risk is None else {current_risk["symbol"]: current_risk}
    return build_tw_prospective_registry(rows or [], previous, calendar=cal, now=now,
                                         evaluation_context={"official_records": quotes, "risk_snapshots": risks})


def enroll(cal, *, tpex=False):
    return run(cal, rows=[candidate(tpex=tpex)], quote=official(tpex=tpex))


def evaluate(cal, previous, *, now=NEXT_CLOSE, session="2026-10-12", close=103, current_risk=None, rows=None):
    return run(cal, previous, rows=rows, now=now, quote=official(session, close, now),
               current_risk=risk(now, session) if current_risk is None else current_risk)


def test_first_real_registration_freezes_now_not_source_batch_and_never_triggers(tmp_path):
    row, quote = candidate(), official()
    before = deepcopy((row, quote))
    first = run(calendar(tmp_path), rows=[row], quote=quote, current_risk=risk())
    record = first["records"][0]
    frozen = record["frozen"]
    assert frozen["registered_at"] == REGISTERED.isoformat()
    assert frozen["source_batch_at"] == "2026-10-09 17:00:00"
    assert frozen["valid_sessions"] == ["2026-10-12", "2026-10-13"]
    assert frozen["valid_through_session"] == "2026-10-13"
    assert frozen["evaluation_expires_at"] == "2026-10-14T13:30:00+08:00"
    assert at(frozen["data_freshness_expires_at"]) < REGISTERED < at(frozen["evaluation_expires_at"])
    assert record["status"] == "enrolled_pending"
    assert record["evaluations"][0]["quote"] is None
    assert (row, quote) == before
    assert "raw_record\"" not in json.dumps(first)
    assert first["policy"]["automatic_orders"] is False
    assert first["policy"]["accuracy_claim"] is None


def test_repeat_same_timestamp_is_idempotent_and_later_registration_does_not_reset(tmp_path):
    cal = calendar(tmp_path)
    first = enroll(cal)
    assert run(cal, first, rows=[candidate()], quote=official()) == first
    later = run(cal, first, rows=[candidate()], quote=official(), now=REGISTERED + timedelta(minutes=1))
    assert len(later["records"]) == 1
    assert later["records"][0]["frozen"] == first["records"][0]["frozen"]
    assert later["records"][0]["status"] == "enrolled_pending"
    assert len(first["records"][0]["evaluations"]) == 1


def test_actual_next_completed_close_uses_original_range_not_new_generated_range(tmp_path):
    cal = calendar(tmp_path)
    first = enroll(cal)
    revised = candidate()
    plan = revised["plans"]["short"]
    plan.update(entry_low=92, entry_high=99)
    snapshot = plan["conclusion"]["plan_snapshot"]
    snapshot["levels"].update(entry_low=92, entry_high=99)
    snapshot["id"] = digest({key: value for key, value in snapshot.items() if key != "id"})
    result = evaluate(cal, first, rows=[revised], close=103)
    record = result["records"][0]
    assert record["status"] == "triggered_close_only"
    assert record["frozen"] == first["records"][0]["frozen"]
    assert record["evaluations"][-1]["quote"]["ohlcv"]["close"] == 103
    assert result["rejections"][0]["reason"] == "amended_frozen_plan_rejected"
    assert result["policy"]["intraday_touch_evaluated"] is False


def test_final_valid_session_can_be_observed_after_publication(tmp_path):
    cal = calendar(tmp_path)
    observed = evaluate(cal, enroll(cal), close=106)
    result = evaluate(cal, observed, now=at("2026-10-13T06:00:00Z"), session="2026-10-13")
    assert result["records"][0]["status"] == "triggered_close_only"


@pytest.mark.parametrize("close,status,reason", [
    (106, "observed_wait", "close_outside_frozen_entry_range"),
    (98, "observed_wait", "close_outside_frozen_entry_range"),
    (100, "triggered_close_only", "close_inside_frozen_entry_range"),
    (105, "triggered_close_only", "close_inside_frozen_entry_range"),
    (90, "invalidated", "close_breached_frozen_stop"),
])
def test_close_only_geometry_and_frozen_stop(tmp_path, close, status, reason):
    cal = calendar(tmp_path)
    record = evaluate(cal, enroll(cal), close=close)["records"][0]
    assert record["status"] == status
    assert record["last_reason"] == reason


def test_intraday_touch_without_close_condition_never_triggers(tmp_path):
    cal = calendar(tmp_path)
    quote = official("2026-10-12", 106, NEXT_CLOSE)  # Low=104 touches original band.
    result = run(cal, enroll(cal), now=NEXT_CLOSE, quote=quote, current_risk=risk())
    assert result["records"][0]["status"] == "observed_wait"


@pytest.mark.parametrize("initial_close", [90, 103])
def test_terminal_invalidated_and_triggered_records_cannot_resurrect(tmp_path, initial_close):
    cal = calendar(tmp_path)
    terminal = evaluate(cal, enroll(cal), close=initial_close)
    result = evaluate(cal, terminal, now=at("2026-10-13T06:00:00Z"), session="2026-10-13", close=103)
    assert result["records"] == terminal["records"]


def test_expired_plan_cannot_resurrect_or_backfill_old_good_close(tmp_path):
    cal = calendar(tmp_path)
    expired_at = at("2026-10-14T05:30:00Z")
    expired = run(cal, enroll(cal), now=expired_at, quote=official("2026-10-12", 103, NEXT_CLOSE))
    assert expired["records"][0]["status"] == "expired"
    again = run(cal, expired, rows=[candidate()], quote=official(), now=expired_at + timedelta(minutes=1))
    assert again["records"] == expired["records"]


def test_missing_quote_and_same_batch_remain_pending(tmp_path):
    cal = calendar(tmp_path)
    first = enroll(cal)
    for quote in (None, official()):
        result = run(cal, first, now=REGISTERED + timedelta(minutes=1), quote=quote)
        assert result["records"][0]["status"] == "enrolled_pending"


@pytest.mark.parametrize("quote,now,reason", [
    (official("2026-10-12", 103, NEXT_CLOSE), at("2026-10-12T05:29:00Z"), "quote_session_not_completed"),
    (official("2026-10-12", 103, at("2026-10-12T05:29:00Z")), NEXT_CLOSE, "invalid_quote_observation_time"),
    (official("2026-10-12", 103, at("2026-10-12T06:00:00Z")), NEXT_CLOSE, "invalid_quote_observation_time"),
    (official("2026-10-09", 103, REGISTERED), NEXT_CLOSE, "quote_not_official_session"),
    (official("2026-10-12", 103, NEXT_CLOSE), at("2026-10-13T06:00:00Z"), "missed_prospective_session"),
])
def test_unfinished_future_prefetched_holiday_and_stale_quotes_quarantined(tmp_path, quote, now, reason):
    cal = calendar(tmp_path)
    result = run(cal, enroll(cal), quote=quote, now=now, current_risk=risk(now))
    assert result["records"][0]["status"] == "quarantined"
    assert result["records"][0]["last_reason"] == reason


@pytest.mark.parametrize("key,value", [
    ("market", "US"), ("source", "Yahoo Finance"), ("source_url", "https://example.com"),
    ("unit", "USD/shares"), ("interval", "1m"), ("source_session_date", "2026-10-13"),
    ("raw_record_sha256", "0" * 64), ("raw_record", {}),
    ("ohlcv", {"open": 103, "high": 105, "low": 101, "close": 103, "volume": True}),
])
def test_official_quote_provenance_tamper_fails_closed(tmp_path, key, value):
    cal = calendar(tmp_path)
    quote = official("2026-10-12", 103, NEXT_CLOSE)
    quote[key] = value
    result = run(cal, enroll(cal), quote=quote, current_risk=risk(), now=NEXT_CLOSE)
    assert result["records"][0]["last_reason"] == "invalid_official_record"


def test_news_expiry_pauses_and_genuinely_current_refresh_can_resume(tmp_path):
    cal = calendar(tmp_path)
    expired_risk = risk()
    expired_risk["news"].update(observed_at="2026-10-11T00:00:00Z", expires_at="2026-10-11T18:00:00Z")
    result = evaluate(cal, enroll(cal), current_risk=expired_risk)
    assert result["records"][0]["status"] == "observed_wait"
    assert result["records"][0]["last_reason"] == "news_refresh_required"
    refreshed = NEXT_CLOSE + timedelta(minutes=1)
    resumed = evaluate(cal, result, now=refreshed)
    assert resumed["records"][0]["status"] == "triggered_close_only"
    assert resumed["records"][0]["frozen"] == result["records"][0]["frozen"]


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.update(source_validity="unsupported"), "current_risk_unverified"),
    (lambda r: r.update(source_session_date="2026-10-08"), "current_risk_unverified"),
    (lambda r: r.update(observed_at="2026-10-08T05:35:00Z"), "current_risk_unverified"),
    (lambda r: r["corporate_actions"].update(status="unsupported"), "corporate_actions_unverified"),
    (lambda r: r["corporate_actions"].update(source_ids=[]), "corporate_actions_unverified"),
    (lambda r: r["corporate_actions"].update(coverage_from="2026-10-12"), "corporate_actions_unverified"),
    (lambda r: r["corporate_actions"].update(through_session="2026-10-08"), "corporate_actions_unverified"),
    (lambda r: r["news"].update(status="unsupported"), "news_source_unverified"),
    (lambda r: r["news"].update(observed_at="2026-10-12T06:00:00Z"), "news_source_unverified"),
    (lambda r: r["news"].update(expires_at="2026-10-20T00:00:00Z"), "news_source_unverified"),
])
def test_risk_news_ca_missing_stale_or_invented_freshness_quarantines(tmp_path, mutate, reason):
    cal = calendar(tmp_path)
    current = risk()
    mutate(current)
    record = evaluate(cal, enroll(cal), current_risk=current)["records"][0]
    assert record["status"] == "quarantined"
    assert record["last_reason"] == reason


def test_missing_current_risk_and_new_negative_risk_are_distinct(tmp_path):
    cal = calendar(tmp_path)
    result = run(cal, enroll(cal), quote=official("2026-10-12", 103, NEXT_CLOSE), now=NEXT_CLOSE)
    assert result["records"][0]["last_reason"] == "current_risk_unavailable"
    current = risk()
    current["risk_blocks"] = ["new blocking event"]
    assert evaluate(cal, enroll(cal), current_risk=current)["records"][0]["status"] == "invalidated"
    current = risk()
    current["corporate_actions"]["status"] = "blocked"
    assert evaluate(cal, enroll(cal), current_risk=current)["records"][0]["status"] == "invalidated"


def test_calendar_missing_or_unverified_blocks_enrollment_and_evaluation(tmp_path):
    cal = calendar(tmp_path)
    first = enroll(cal)
    missing = OfficialMarketCalendar(tmp_path / "missing.json", auto_refresh=False, allow_network=False)
    assert not enroll(missing)["records"]
    assert evaluate(missing, first)["records"][0]["last_reason"] == "official_calendar_unavailable"
    cal._state["markets"]["TW"]["years"]["2026"]["status"] = "weekdays_assumed"
    assert not enroll(cal)["records"]


def test_duplicate_candidates_and_raw_provider_duplicates_never_enroll(tmp_path):
    cal = calendar(tmp_path)
    row = candidate()
    result = run(cal, rows=[row, deepcopy(row)], quote=official())
    assert not result["records"]
    assert all(r["reason"] == "duplicate_plan_candidate" for r in result["rejections"])
    raw = official()["raw_record"]
    records = parse_official_price_rows("TWSE OpenAPI", [raw, raw], fetched_at=REGISTERED.isoformat())
    assert records == {}
    assert not run(cal, rows=[row], quote=None)["records"]


@pytest.mark.parametrize("mutate", [
    lambda row: row["plans"]["short"].update(active_entry_plan=False),
    lambda row: row["plans"]["short"]["plan_quality"].update(entry_eligible=False),
    lambda row: row["plans"]["short"]["conclusion"].update(code="eligible"),
    lambda row: row["plans"]["short"]["conclusion"]["plan_assessment"].update(data_gates_passed=False),
    lambda row: row["plans"]["short"].update(buy_window_sessions=0),
])
def test_enrollment_exact_qualification_required(tmp_path, mutate):
    row = candidate()
    mutate(row)
    assert not run(calendar(tmp_path), rows=[row], quote=official())["records"]


def test_us_rows_are_ignored_and_tpex_can_register_and_trigger(tmp_path):
    cal = calendar(tmp_path)
    us = candidate()
    us.update(market="US", symbol="AAPL", raw_us={"secret": "never_publish"})
    assert not run(cal, rows=[us], quote=official())["records"]
    first = enroll(cal, tpex=True)
    result = run(cal, first, now=NEXT_CLOSE, quote=official("2026-10-12", 103, NEXT_CLOSE, tpex=True),
                 current_risk=risk(symbol="6488.TWO"))
    assert result["records"][0]["status"] == "triggered_close_only"
    assert "raw_us" not in json.dumps(result)


@pytest.mark.parametrize("mutate", [
    lambda state: state["records"][0]["frozen"]["levels"].update(entry_high=999),
    lambda state: state["records"][0]["frozen"].update(registered_at="2026-10-08T00:00:00Z"),
    lambda state: state["records"][0].update(status="triggered_close_only"),
    lambda state: state["records"][0]["evaluations"][0].update(reason="amended"),
    lambda state: state["records"].append(deepcopy(state["records"][0])),
])
def test_existing_ledger_tampering_rejected_even_if_outer_digest_recomputed(tmp_path, mutate):
    cal = calendar(tmp_path)
    state = enroll(cal)
    mutate(state)
    state["registry_digest"] = digest({key: value for key, value in state.items() if key != "registry_digest"})
    with pytest.raises(ValueError):
        run(cal, state, now=NEXT_CLOSE)


def test_registry_clock_cannot_move_backwards_and_now_must_be_aware(tmp_path):
    cal = calendar(tmp_path)
    with pytest.raises(ValueError, match="invalid_existing_registry"):
        run(cal, enroll(cal), now=REGISTERED - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone"):
        run(cal, now=datetime(2026, 10, 10))


def test_cannot_first_register_old_plan_after_next_outcome_known(tmp_path):
    cal = calendar(tmp_path)
    result = run(cal, rows=[candidate()], quote=official(), now=NEXT_CLOSE)
    assert not result["records"]
    assert result["rejections"][0]["reason"] == "original_price_snapshot_stale"


@pytest.mark.parametrize("field", ["news", "corporate_actions"])
def test_malformed_nested_risk_never_crashes_or_becomes_clear(tmp_path, field):
    cal = calendar(tmp_path)
    current = risk()
    current[field] = "not an attestation"
    result = evaluate(cal, enroll(cal), current_risk=current)
    assert result["records"][0]["status"] == "quarantined"


def test_cross_year_calendar_coverage_cannot_be_guessed(tmp_path):
    cal = calendar(tmp_path)
    cal._state["markets"]["TW"]["years"]["2026"]["sessions"] = ["2026-10-08"]
    result = enroll(cal)
    assert not result["records"]
    assert result["rejections"][0]["reason"] == "official_calendar_unavailable"


def test_quarantined_evidence_can_resume_only_with_current_verification(tmp_path):
    cal = calendar(tmp_path)
    current = risk()
    current["corporate_actions"]["status"] = "unsupported"
    first = evaluate(cal, enroll(cal), current_risk=current)
    assert first["records"][0]["status"] == "quarantined"
    later = evaluate(cal, first, now=NEXT_CLOSE + timedelta(minutes=1))
    assert later["records"][0]["status"] == "triggered_close_only"
    assert later["records"][0]["frozen"] == first["records"][0]["frozen"]


def test_invalid_risk_metadata_cannot_publish_nested_raw_payloads(tmp_path):
    cal = calendar(tmp_path)
    current = risk()
    current["news"]["source_ids"] = [{"raw_record": {"raw_us": "private_provider_payload"}}]
    result = evaluate(cal, enroll(cal), current_risk=current)
    assert result["records"][0]["status"] == "quarantined"
    assert "private_provider_payload" not in json.dumps(result)
    assert "raw_us" not in json.dumps(result)


@pytest.mark.parametrize("unsupported", ["news", "corporate_actions", "entire_risk"])
def test_observed_stop_breach_is_terminal_even_when_risk_verification_fails(tmp_path, unsupported):
    cal = calendar(tmp_path)
    current = risk()
    if unsupported == "entire_risk":
        current["source_validity"] = "unsupported"
    else:
        current[unsupported]["status"] = "unsupported"
    breached = evaluate(cal, enroll(cal), close=89, current_risk=current)
    assert breached["records"][0]["status"] == "invalidated"
    assert breached["records"][0]["last_reason"] == "observed_price_basis_or_stop_breach"
    recovered = evaluate(cal, breached, close=103, now=at("2026-10-13T06:00:00Z"), session="2026-10-13")
    assert recovered["records"] == breached["records"]


def test_skipped_intervening_close_creates_irreversible_quarantine(tmp_path):
    cal = calendar(tmp_path)
    now = at("2026-10-13T06:00:00Z")
    skipped = evaluate(cal, enroll(cal), now=now, session="2026-10-13", close=103)
    event = skipped["records"][0]["evaluations"][-1]
    assert event["status"] == "quarantined"
    assert event["reason"] == "missed_prospective_session"
    assert event["missed_sessions"] == ["2026-10-12"]
    # A genuine earlier fetched_at cannot retroactively become a timely ledger
    # observation. Nor can fresh same-session risk clear a continuity gap.
    backfilled = run(cal, skipped, quote=official("2026-10-12", 106, NEXT_CLOSE),
                     current_risk=risk(), now=now + timedelta(minutes=1))
    assert backfilled["records"][0]["last_reason"] == "missed_prospective_session"
    refreshed = evaluate(cal, backfilled, now=now + timedelta(minutes=2), session="2026-10-13", close=103)
    assert refreshed["records"][0]["status"] == "quarantined"
    assert refreshed["records"][0]["last_reason"] == "missed_prospective_session"


def test_missing_input_after_skipped_session_also_records_irreversible_gap(tmp_path):
    cal = calendar(tmp_path)
    now = at("2026-10-13T06:00:00Z")
    skipped = run(cal, enroll(cal), now=now)
    assert skipped["records"][0]["last_reason"] == "missed_prospective_session"
    assert skipped["records"][0]["evaluations"][-1]["missed_sessions"] == ["2026-10-12"]


def test_timely_outside_band_close_then_next_close_inside_band_can_trigger(tmp_path):
    cal = calendar(tmp_path)
    first = evaluate(cal, enroll(cal), close=106)
    assert first["records"][0]["status"] == "observed_wait"
    assert first["records"][0]["evaluations"][-1]["price_observation_valid"] is True
    second = evaluate(cal, first, now=at("2026-10-13T06:00:00Z"), session="2026-10-13", close=103)
    assert second["records"][0]["status"] == "triggered_close_only"


def test_timely_price_observation_survives_news_quarantine_without_hiding_stop(tmp_path):
    cal = calendar(tmp_path)
    current = risk()
    current["news"]["status"] = "unsupported"
    first = evaluate(cal, enroll(cal), close=106, current_risk=current)
    assert first["records"][0]["status"] == "quarantined"
    assert first["records"][0]["evaluations"][-1]["price_observation_valid"] is True
    refreshed = evaluate(cal, first, now=NEXT_CLOSE + timedelta(minutes=1), close=106)
    assert refreshed["records"][0]["status"] == "observed_wait"
    second = evaluate(cal, refreshed, now=at("2026-10-13T06:00:00Z"), session="2026-10-13", close=103)
    assert second["records"][0]["status"] == "triggered_close_only"


@pytest.mark.parametrize("override_supported", [True, False])
def test_per_plan_risk_overrides_preserve_distinct_original_coverage(tmp_path, override_supported):
    cal = calendar(tmp_path)
    first = enroll(cal)
    second_row = candidate()
    plan = second_row["plans"]["short"]
    snapshot = plan["conclusion"]["plan_snapshot"]
    snapshot.update(source_session_date="2026-10-12", source_batch_at=NEXT_CLOSE.isoformat())
    snapshot["id"] = digest({key: value for key, value in snapshot.items() if key != "id"})
    plan["conclusion"].update(evaluated_at=NEXT_CLOSE.isoformat(), expires_at="2026-10-12T23:35:00Z")
    two = evaluate(cal, first, close=110, rows=[second_row])
    assert len(two["records"]) == 2
    assert [record["status"] for record in two["records"]] == ["observed_wait", "enrolled_pending"]
    now = at("2026-10-13T06:00:00Z")
    fallback = risk(now, "2026-10-13")
    fallback["corporate_actions"]["coverage_from"] = "2026-10-12"
    old_plan_risk = risk(now, "2026-10-13")
    if not override_supported:
        old_plan_risk["source_validity"] = "unsupported"
    result = build_tw_prospective_registry([], two, calendar=cal, now=now, evaluation_context={
        "official_records": {"2330.TW": official("2026-10-13", 103, now)},
        "risk_snapshots": {"2330.TW": fallback},
        "risk_snapshots_by_plan_id": {first["records"][0]["frozen"]["plan_id"]: old_plan_risk},
    })
    assert result["records"][0]["status"] == ("triggered_close_only" if override_supported else "quarantined")
    assert result["records"][1]["status"] == "triggered_close_only"


def test_explicit_null_plan_risk_does_not_fall_back_to_symbol_risk(tmp_path):
    cal = calendar(tmp_path)
    first = enroll(cal)
    plan_id = first["records"][0]["frozen"]["plan_id"]
    result = build_tw_prospective_registry([], first, calendar=cal, now=NEXT_CLOSE, evaluation_context={
        "official_records": {"2330.TW": official("2026-10-12", 103, NEXT_CLOSE)},
        "risk_snapshots": {"2330.TW": risk()}, "risk_snapshots_by_plan_id": {plan_id: None},
    })
    assert result["records"][0]["last_reason"] == "current_risk_unavailable"
    with pytest.raises(ValueError, match="invalid_evaluation_context"):
        build_tw_prospective_registry([], first, calendar=cal, now=NEXT_CLOSE,
                                      evaluation_context={"risk_snapshots_by_plan_id": []})


def test_read_only_public_validation_copies_without_evaluation_and_rejects_tamper(tmp_path):
    first = enroll(calendar(tmp_path))
    validated = validate_tw_prospective_registry(first, now=NEXT_CLOSE)
    assert validated == first
    assert validated is not first
    assert validated["records"][0] is not first["records"][0]
    assert validated["records"][0]["status"] == "enrolled_pending"
    assert len(validated["records"][0]["evaluations"]) == 1
    invalid = deepcopy(first)
    invalid["registry_digest"] = "0" * 64
    with pytest.raises(ValueError, match="invalid_existing_registry"):
        validate_tw_prospective_registry(invalid, now=NEXT_CLOSE)
    with pytest.raises(ValueError, match="timezone"):
        validate_tw_prospective_registry(first, now=datetime(2026, 10, 12))
    with pytest.raises(ValueError, match="invalid_existing_registry"):
        validate_tw_prospective_registry(None, now=NEXT_CLOSE)


def test_ca_audit_retains_only_bounded_scalar_reasons(tmp_path):
    cal = calendar(tmp_path)
    current = risk()
    current["corporate_actions"].update(status="unsupported", reason="identity_actions_refresh_required",
                                         reasons=[{"raw_record": "do_not_publish"}] + ["x" * 300] * 10)
    result = evaluate(cal, enroll(cal), current_risk=current)
    audit = result["records"][0]["evaluations"][-1]["risk"]["corporate_actions"]
    assert audit["reason"] == "identity_actions_refresh_required"
    assert len(audit["reasons"]) == 8
    assert all(len(value) == 200 for value in audit["reasons"])
    assert "do_not_publish" not in json.dumps(result)
