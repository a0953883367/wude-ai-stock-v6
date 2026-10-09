from copy import deepcopy
from datetime import datetime, timezone

import pytest

from news_risk import classify_news
from point_in_time_events import build_event_snapshot, event_snapshot, sec_companyfacts_events

CUT = "2026-10-09T14:00:00Z"


def event(**changes):
    row = {"event_id": "report-2026-q3", "symbol": "TEST", "event_type": "earnings",
           "source": "SEC EDGAR companyfacts", "revision": "r1",
           "published_at": "2026-10-09T12:00:00Z", "first_seen": "2026-10-09T12:01:00Z",
           "period_end": "2026-09-30", "payload": {"eps": 2}}
    return {**row, **changes}


def test_cutoff_exact_boundary_and_late_collection():
    item = event(first_seen=CUT)
    assert len(event_snapshot([item], cutoff=CUT)["events"]) == 1
    assert not event_snapshot([item], cutoff="2026-10-09T13:59:59Z")["events"]
    late = event(first_seen="2026-10-10T00:00:00Z")
    assert event_snapshot([late], cutoff=CUT)["excluded"][0]["reason"] == "not_available_at_cutoff"


@pytest.mark.parametrize("field,value,reason", [
    ("published_at", "2026-10-09", "missing_or_imprecise_publication_time"),
    ("published_at", None, "missing_or_imprecise_publication_time"),
    ("published_at", "2026-10-09T12:00:00", "missing_or_imprecise_publication_time"),
    ("first_seen", None, "missing_or_imprecise_first_seen"),
    ("first_seen", "2026-10-08T12:00:00Z", "first_seen_precedes_publication"),
    ("revision", "", "missing_identity_or_revision"),
    ("revision_published_at", "2026-10-09", "missing_or_imprecise_revision_time"),
    ("revision_published_at", "2026-10-08T12:00:00Z", "revision_precedes_publication"),
    ("event_type", "earnings_calendar_forecast", "unsupported_event_type"),
])
def test_bad_metadata_fails_closed(field, value, reason):
    snapshot = event_snapshot([event(**{field: value})], cutoff=CUT)
    assert snapshot["events"] == []
    assert snapshot["excluded"][0]["reason"] == reason


def test_revisions_never_leak_and_input_is_immutable():
    original = event()
    revision = event(revision="r2", revision_published_at="2026-10-10T12:00:00Z",
                     first_seen="2026-10-10T12:01:00Z", payload={"eps": 1})
    inputs = [revision, original]
    before = deepcopy(inputs)
    earlier = event_snapshot(inputs, cutoff=CUT)
    later = event_snapshot(inputs, cutoff="2026-10-11T00:00:00Z")
    assert earlier["events"][0]["payload"]["eps"] == 2
    assert len(earlier["available_revision_history"]) == 1
    assert later["events"][0]["payload"]["eps"] == 1
    assert len(later["available_revision_history"]) == 2
    assert inputs == before


def test_duplicate_observation_preserves_earliest_known_time():
    snapshot = event_snapshot([event(first_seen="2026-10-09T13:00:00Z"), event()], cutoff=CUT)
    assert snapshot["counts"]["duplicates"] == 1
    assert snapshot["events"][0]["first_seen"] == "2026-10-09T12:01:00Z"


def test_conflicting_revision_fails_closed_only_once_conflict_observed():
    conflicting = event(first_seen="2026-10-10T00:00:00Z", payload={"eps": 999})
    assert len(event_snapshot([event(), conflicting], cutoff=CUT)["events"]) == 1
    result = event_snapshot([event(), conflicting], cutoff="2026-10-11T00:00:00Z")
    assert not result["events"]
    assert result["excluded"][0]["reason"] == "conflicting_revision"


def test_ambiguous_revision_does_not_guess_id_order():
    result = event_snapshot([event(), event(revision="r2", payload={"eps": 3})], cutoff=CUT)
    assert not result["events"]
    assert result["excluded"][0]["reason"] == "ambiguous_latest_revision"


def test_trust_not_inferred_from_official_boolean_or_source_substring():
    result = event_snapshot([event(source="SEC rumor site", official=True)], cutoff=CUT)
    assert result["events"][0]["source_trust"] == "unverified"


def test_timezone_conversion_and_naive_cutoff_rejection():
    result = event_snapshot([event(first_seen="2026-10-09T22:00:00+08:00")], cutoff=CUT)
    assert result["events"][0]["available_at"] == CUT
    with pytest.raises(ValueError):
        event_snapshot([], cutoff="2026-10-09T14:00:00")


def test_actual_existing_news_classifier_schema_is_excluded_without_precision():
    now = datetime(2026, 10, 9, 14, tzinfo=timezone.utc)
    classified = classify_news([{"title": "TEST guidance cut", "publisher": "Reuters",
                                 "providerPublishTime": now.timestamp() - 60, "link": "https://example.test/a"}], now=now)
    assert classified["news_articles"][0]["published_at"] == "2026-10-09"
    result = build_event_snapshot({**classified, "symbol": "TEST", "news_scanned_at": CUT}, cutoff=CUT)
    assert result["events"] == []
    assert result["counts"]["exclusion_reasons"] == {"missing_or_imprecise_publication_time": 1}


def test_sec_raw_companyfacts_preserves_accessions_and_period_end_separately():
    rows = [{"accn": "original", "filed": "2026-10-01", "end": "2026-06-30", "val": 2, "form": "10-Q"},
            {"accn": "amended", "filed": "2026-10-10", "end": "2026-06-30", "val": 1, "form": "10-Q/A"}]
    payload = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": rows}}}}}
    records = sec_companyfacts_events(payload, symbol="TEST", first_seen=CUT)
    assert {item["revision"] for item in records} == {"original", "amended"}
    assert all(item["period_ends"] == ["2026-06-30"] for item in records)
    assert {item["published_at"] for item in records} == {"2026-10-01", "2026-10-10"}
    result = event_snapshot(records, cutoff=CUT)
    assert not result["events"]
    assert result["counts"]["exclusion_reasons"]["missing_or_imprecise_publication_time"] == 2


def test_pipeline_adapter_carries_explicit_contract_without_score_mutation():
    row = {"symbol": "TEST", "point_in_time_events": [event(event_type="guidance")], "final_score": 81}
    before = deepcopy(row)
    snapshot = build_event_snapshot(row, cutoff=CUT)
    assert snapshot["events"][0]["event_type"] == "guidance"
    assert snapshot["shadow_only"] is True and snapshot["affects_scores"] is False
    assert "production_event_coverage_not_validated" in snapshot["coverage"]
    assert row == before


def test_empty_and_invalid_records_are_auditable():
    assert build_event_snapshot({}, cutoff=CUT)["counts"]["eligible_events"] == 0
    result = event_snapshot([None, {}], cutoff=CUT)
    assert len(result["excluded"]) == 2


def test_fractional_seconds_use_time_order_not_string_order():
    original = event(first_seen="2026-10-09T12:00:00Z")
    amended = event(revision="r2", revision_published_at="2026-10-09T12:00:00.1Z",
                    first_seen="2026-10-09T12:00:00.2Z", payload={"eps": 3})
    result = event_snapshot([original, amended], cutoff=CUT)
    assert result["events"][0]["revision"] == "r2"
    copies = event_snapshot([original, event(first_seen="2026-10-09T12:00:00.1Z")], cutoff=CUT)
    assert copies["events"][0]["first_seen"] == "2026-10-09T12:00:00Z"


def test_conflicting_amendment_never_revives_old_current_revision():
    original = event()
    revision = event(revision="r2", revision_published_at="2026-10-10T12:00:00Z",
                     first_seen="2026-10-10T12:01:00Z", payload={"eps": 1})
    conflict = {**revision, "payload": {"eps": 99}}
    records = [original, revision, conflict]
    before = event_snapshot(records, cutoff=CUT)
    assert before["events"][0]["revision"] == "r1"
    after = event_snapshot(records, cutoff="2026-10-11T00:00:00Z")
    assert after["events"] == []
    assert [item["revision"] for item in after["available_revision_history"]] == ["r1"]
    assert after["counts"]["exclusion_reasons"] == {
        "conflicting_revision": 1, "identity_has_conflicting_revision": 1,
    }
