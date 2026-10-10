"""Synthetic offline tests. Fixtures are not live provider/official evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
import threading

import pytest

import tw_private_cohort as cohort
import tw_private_cohort_diagnostics as diag
from tw_daily_shadow_attestation import parse_official_price_rows

NOW = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)
STAMP = "2026-10-10T08:00:00+00:00"
SECRET = "DO_NOT_EXPORT_PROVIDER_OR_PRIVATE_VALUE"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fixture():
    first = datetime(2026, 6, 22)
    dates = [(first + timedelta(days=i)).date().isoformat() for i in range(109)
             if (first + timedelta(days=i)).weekday() < 5]
    manifest = [{"symbol": symbol, "market": "TW", "type": "個股"} for symbol in diag.PILOT]
    manifest += [{"symbol": f"{1000+i}.TW", "market": "TW", "type": "ETF" if i % 7 == 0 else "個股"}
                 for i in range(188)]
    # Absent nonpilot venue is intentional. Never infer mainboard or emerging.
    manifest += [{"symbol": "7415.TWO", "market": "TW", "type": "個股"},
                 {"symbol": "7815.TWO", "market": "TW", "type": "個股"}]
    formal = {"updated_at": "2026-10-10 16:00:00", "data": [
        {**item, "official_session_date": dates[-1], "price": 9876543.125, "score": 98,
         "rank": i + 1, "name": SECRET} for i, item in enumerate(manifest)]}
    histories, official = {}, {}
    for symbol, (code, _, venue) in diag.PILOT.items():
        bars = []
        for i, day in enumerate(dates):
            close = 101.125 + i % 7
            bars.append({"date": day, "open": close - .5, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 98765 + i})
        histories[symbol] = {"status": "validated_in_memory", "symbol": symbol, "market": "TW",
            "venue": venue, "source": cohort.SOURCE, "source_session_date": dates[-1],
            "requested_at": "2026-10-10T07:59:00+00:00", "observed_at": STAMP,
            "price_basis": "raw_unadjusted", "volume_unit": "shares",
            "adjustment_evidence": "explicit_request_and_provider_contract",
            "request": {"timeframe": "D", "adjusted": "false", "from": dates[0], "to": dates[-1]},
            "bars": bars, "response_sha256": digest(bars)}
        last = bars[-1]
        rawdate = str(int(last["date"][:4]) - 1911) + last["date"][5:7] + last["date"][8:10]
        if venue == "TWSE":
            source = "TWSE OpenAPI"
            raw = {"Code": code, "Date": rawdate, **dict(zip(
                ["OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice", "TradeVolume"],
                [last[key] for key in cohort.BAR_FIELDS]))}
        else:
            source = "TPEx OpenAPI"
            raw = {"SecuritiesCompanyCode": code, "Date": rawdate, **dict(zip(
                ["Open", "High", "Low", "Close", "TradingShares"],
                [last[key] for key in cohort.BAR_FIELDS]))}
        official[symbol] = parse_official_price_rows(source, [raw], fetched_at=STAMP)[symbol]
    calendar = {"available": True, "status": "verified", "source_statuses": ["verified_twse_tpex"],
                "sessions": dates}
    return formal, manifest, histories, calendar, official


class Rest:
    def __init__(self, dividends=None, capital=None):
        self.payloads = {"dividends": {"data": []} if dividends is None else dividends,
                         "capital_changes": {"data": []} if capital is None else capital}
        self.calls = []
        self.corporate_actions = SimpleNamespace(dividends=lambda **kw: self.call("dividends", kw),
                                                capital_changes=lambda **kw: self.call("capital_changes", kw))

    def call(self, method, kwargs):
        self.calls.append((method, kwargs))
        response = self.payloads[method]
        if isinstance(response, Exception):
            raise response
        return response() if callable(response) else deepcopy(response)


@pytest.fixture(autouse=True)
def freeze_clock(monkeypatch):
    monkeypatch.setattr(diag.clock, "monotonic", lambda: 100.0)


def run(parts=None, rest=None, **kwargs):
    formal, manifest, histories, calendar, official = parts or fixture()
    return diag.build_diagnostics(rest or Rest(), histories, calendar, formal=formal, manifest=manifest,
                                  official_records=official, now=NOW, deadline=kwargs.pop("deadline", 200.0),
                                  cancelled=kwargs.pop("cancelled", threading.Event()), **kwargs)


def cash(**changes):
    return {"date": "2026-07-15", "symbol": "2330", "exchange": "TWSE", "dividendType": "息",
            "cashDividend": 3.125, "stockDividendShares": 0, "name": SECRET, **changes}


def capital(**changes):
    return {"resumeDate": "2026-07-15", "symbol": "6290", "exchange": "TPEx",
            "actionType": "capital_reduction", "raw": {"referencePrice": 7654321.5, "reason": SECRET}, **changes}


def inspect(rows, method="dividends", **envelope):
    return diag.inspect_action_response({"data": rows, **envelope}, method=method,
                                        first="2026-06-22", last="2026-10-08")


def test_full_frozen_manifest_and_two_private_histories_are_distinct():
    parts = fixture(); before = deepcopy(parts); rest = Rest()
    result = run(parts, rest)
    assert parts == before
    assert result["status"] == "metadata_diagnostics_complete_held"
    assert result["full_manifest_count"] == 192
    assert result["acquired_history_count"] == result["source_feature_rebuilt_count"] == 2
    assert result["unacquired_history_count"] == 190
    assert result["official_crosscheck_verified_count"] == 2
    assert result["formal_v6_unchanged"] is True
    assert result["coverage_status"] == "provider_coverage_unverified"
    assert result["corporate_action_coverage_verified"] is False
    assert result["full_cohort_readiness_evaluated"] is False
    assert result["scoring_executed"] is result["ranking_executed"] is result["eligibility_evaluated"] is False
    assert rest.calls == [(method, {"start_date": parts[3]["sessions"][0], "end_date": parts[3]["sessions"][-1]})
                          for method in ("dividends", "capital_changes")]


def test_manifest_count_is_dynamic_but_always_full_membership():
    parts = fixture(); parts[0]["data"].pop(); parts[1].pop()
    result = run(parts)
    assert result["full_manifest_count"] == 191
    assert result["unacquired_history_count"] == 189
    assert result["status"] == "metadata_diagnostics_complete_held"


def test_empty_response_never_proves_coverage_or_no_actions():
    result = run()
    for endpoint in result["corporate_actions"].values():
        assert endpoint["status"] == "empty_response_inspected"
        assert endpoint["coverage_status"] == diag.COVERAGE
        assert endpoint["cash_event_count"] == endpoint["structural_event_count"] == 0
    assert result["reason_counts"]["action_coverage_unknown"] == 2


@pytest.mark.parametrize("mutation", ["partial", "duplicate", "type", "market", "missing_pilot", "too_many"])
def test_bad_manifest_causes_no_provider_reads(mutation):
    parts = fixture(); rest = Rest()
    if mutation == "partial": parts[1].pop()
    elif mutation == "duplicate": parts[1][2] = deepcopy(parts[1][1])
    elif mutation == "type": parts[1][0]["type"] = "ETF"
    elif mutation == "market": parts[1][0]["market"] = "US"
    elif mutation == "missing_pilot":
        parts[1][0]["symbol"] = "9999.TW"; parts[0]["data"][0]["symbol"] = "9999.TW"
    else:
        parts[1].extend(deepcopy(parts[1][:70])); parts[0]["data"].extend(deepcopy(parts[0]["data"][:70]))
    result = run(parts, rest)
    assert result["status"] == "blocked" and rest.calls == []


@pytest.mark.parametrize("mutation", ["future", "old", "bad_stamp", "stale_row", "calendar", "sessions", "overspan", "unknown_history"])
def test_invalid_inputs_fail_closed(mutation):
    parts = fixture(); rest = Rest()
    if mutation == "future": parts[0]["updated_at"] = "2030-01-01 00:00:00"
    elif mutation == "old": parts[0]["updated_at"] = "2026-01-01 00:00:00"
    elif mutation == "bad_stamp": parts[0]["updated_at"] = None
    elif mutation == "stale_row": parts[0]["data"][5]["official_session_date"] = "2026-01-01"
    elif mutation == "calendar": parts[3]["source_statuses"] = ["verified_conservative_union"]
    elif mutation == "sessions": parts[3]["sessions"][1] = parts[3]["sessions"][0]
    elif mutation == "overspan": parts[3]["sessions"][0] = "2026-01-01"
    else: parts[2]["AAPL"] = {}
    result = run(parts, rest)
    assert result["status"] == "blocked" and rest.calls == []


def test_unacquired_and_invalid_history_are_not_rebuilt():
    parts = fixture(); parts[2].pop("6290.TWO"); parts[2]["2330.TW"]["response_sha256"] = "a" * 64
    result = run(parts)
    assert result["acquired_history_count"] == 1
    assert result["unacquired_history_count"] == 191
    assert result["daily_features_rebuilt_count"] == 0
    assert result["reason_counts"]["history_digest"] == result["reason_counts"]["history_unavailable"] == 1


def test_latest_official_match_compares_all_ohlcv_and_raw_envelope():
    parts = fixture(); raw = parts[4]["2330.TW"]["raw_record"]; raw["TradeVolume"] += 1
    parts[4]["2330.TW"] = parse_official_price_rows("TWSE OpenAPI", [raw], fetched_at=STAMP)["2330.TW"]
    result = run(parts)
    assert result["official_mismatch_count"] == result["official_match_count"] == 1
    assert result["reason_counts"]["official_crosscheck_mismatch"] == 1
    assert result["daily_features_rebuilt_count"] == 2


@pytest.mark.parametrize("mutation", ["raw", "source", "url", "stale", "future", "missing", "hash"])
def test_unverifiable_official_record_is_not_a_match(mutation):
    parts = fixture(); record = parts[4]["2330.TW"]
    if mutation == "raw": record["raw_record"]["ClosingPrice"] += 1
    elif mutation == "source": record["source"] = SECRET
    elif mutation == "url": record["source_url"] = "https://invalid.example/" + SECRET
    elif mutation == "stale": record["raw_record"]["Date"] = "1150101"
    elif mutation == "future": record["fetched_at"] = "2030-01-01T00:00:00Z"
    elif mutation == "missing": parts[4].pop("2330.TW")
    else: record["raw_record_sha256"] = "e" * 64
    result = run(parts)
    assert result["official_unavailable_count"] == 1 and result["official_match_count"] == 1


def test_only_pilot_classifications_are_retained_and_metadata_keys_are_allowlisted():
    safe, private = inspect([cash(), cash(symbol="9999", name=SECRET), cash(symbol="2330", exchange="TPEx")],
                            start_date="2026-06-22", nextCursor=SECRET, arbitrary_provider_secret=SECRET)
    assert safe["pilot_row_count"] == 2 and safe["unrelated_row_count"] == 1
    assert safe["metadata_fields_present"] == ["start_date", "nextCursor"]
    assert all(event["symbol"] == "2330.TW" for event in private)
    assert all(set(event) == {"symbol", "effective_date", "classification"} for event in private)
    assert "9999" not in json.dumps(private)
    assert SECRET not in json.dumps(safe)
    assert "arbitrary_provider_secret" not in json.dumps(safe)


def test_cash_structural_first_bar_and_out_of_window_counts():
    safe, private = inspect([cash(date="2026-06-22"), cash(date="2026-07-16", dividendType="權", cashDividend=0),
                            cash(date="2026-07-17", dividendType="權息", stockDividendShares=20),
                            cash(date="2025-07-15")])
    assert safe["cash_event_count"] == 1 and safe["structural_event_count"] == 2
    assert safe["unknown_event_count"] == safe["out_of_window_row_count"] == 1
    assert safe["first_bar_event_count"] == 1
    assert safe["observed_date_from"] == "2025-07-15"
    assert safe["coverage_status"] == diag.COVERAGE


@pytest.mark.parametrize("changes", [{"cashDividend": 0}, {"cashDividend": "2"}, {"cashDividend": None},
    {"stockDividendShares": None}, {"stockDividendShares": 1}, {"stockDividendShares": False},
    {"dividendType": SECRET}, {"date": "not-a-date"}, {"exchange": SECRET}, {"symbol": 2330},
    {"rightsSubscriptionShares": 1}, {"rightsSubscriptionRatio": True}])
def test_unknown_or_malformed_target_is_not_cash(changes):
    safe, private = inspect([cash(**changes)])
    assert safe["unknown_event_count"] == 1
    assert safe["cash_event_count"] == safe["structural_event_count"] == 0
    assert private[0]["classification"] == "unknown"


@pytest.mark.parametrize("changes", [{}, {"actionType": "par_value_change"},
    {"actionType": "etf_split_or_merge", "raw": {"splitType": "分割"}},
    {"actionType": "etf_split_or_merge", "raw": {"splitType": "反分割"}}])
def test_known_capital_changes_are_structural(changes):
    safe, private = inspect([capital(**changes)], "capital_changes")
    assert safe["structural_event_count"] == 1
    assert private[0]["classification"] == "structural"


@pytest.mark.parametrize("changes", [{"actionType": SECRET}, {"resumeDate": ""}, {"exchange": SECRET}])
def test_unknown_capital_shapes_remain_unknown(changes):
    safe, _ = inspect([capital(**changes)], "capital_changes")
    assert safe["unknown_event_count"] == 1 and safe["structural_event_count"] == 0


def test_identical_duplicates_and_conflicts_do_not_double_count_events():
    safe, private = inspect([cash(), cash(), cash(cashDividend=4), cash(cashDividend=5)])
    assert safe["response_count"] == safe["pilot_row_count"] == 4
    assert safe["duplicate_row_count"] == safe["conflicting_event_count"] == 1
    assert safe["cash_event_count"] == 0 and safe["unknown_event_count"] == 1
    assert len(private) == 1


def test_malformed_unattributed_and_unrelated_records_never_get_pilot_proof():
    safe, private = inspect([None, 42, {"date": "2026-07-15"}, {"symbol": "9999"},
                            {"symbol": "PRIVATE_SYMBOL_VALUE"}])
    assert safe["malformed_row_count"] == 4 and safe["unrelated_row_count"] == 1
    assert private == [] and safe["pilot_row_count"] == 0


@pytest.mark.parametrize("payload,reason", [([], "invalid_provider_payload"), ({}, "invalid_provider_payload"),
    ({"data": {}}, "invalid_provider_payload"), ({"data": [None] * 5001}, "response_row_budget"),
    ({"data": [], "value": "x" * 4097}, "response_string_budget"),
    ({"data": [], "value": float("nan")}, "invalid_provider_payload"),
    ({"data": [], "value": 2**100}, "invalid_provider_payload"),
    ({"data": [], "value": object()}, "invalid_provider_payload"),
    ({"data": [], 7: "value"}, "invalid_provider_payload")])
def test_malicious_payloads_are_bounded(payload, reason):
    with pytest.raises(diag.DiagnosticBlocked, match=reason):
        diag.inspect_action_response(payload, method="dividends", first="2026-06-22", last="2026-10-08")


def test_byte_budget_is_checked_across_all_marketwide_rows():
    rows = [{"symbol": "9999", "name": "x" * 4000} for _ in range(600)]
    with pytest.raises(diag.DiagnosticBlocked, match="response_byte_budget"):
        inspect(rows)


def test_depth_cycles_and_container_budgets():
    nested = {}
    for _ in range(10): nested = {"x": nested}
    for extra, reason in [(nested, "response_shape_budget"), ({str(i): i for i in range(65)}, "response_shape_budget")]:
        with pytest.raises(diag.DiagnosticBlocked, match=reason): inspect([], extra=extra)
    cyclic = {}; cyclic["x"] = cyclic
    with pytest.raises(diag.DiagnosticBlocked, match="invalid_provider_payload"): inspect([], extra=cyclic)


class ProviderError(Exception):
    def __init__(self, code):
        super().__init__(SECRET)
        self.status_code = code
        self.response_text = SECRET

    def __str__(self):
        raise AssertionError("Provider error must never be rendered")


@pytest.mark.parametrize("code,reason", [(401, "provider_authentication_denied"),
    (403, "provider_entitlement_denied"), (429, "provider_rate_limited"), (500, "provider_failure")])
def test_provider_errors_use_safe_enums_and_stop_without_retry(code, reason):
    rest = Rest(dividends=ProviderError(code)); result = run(rest=rest)
    assert result["status"] == "blocked" and result["reason_counts"][reason] == 1
    assert result["corporate_action_request_count"] == 1 and len(rest.calls) == 1
    assert result["corporate_actions"]["capital_changes"]["status"] == "not_attempted"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("code,reason", [(401, "provider_authentication_denied"),
    (403, "provider_entitlement_denied"), (429, "provider_rate_limited")])
def test_error_envelopes_are_recognized_without_body_echo(code, reason):
    result = run(rest=Rest(dividends={"statusCode": code, "message": SECRET}))
    assert result["reason_counts"][reason] == 1
    assert SECRET not in json.dumps(result)


def test_timeout_exception_is_bounded_and_stops():
    rest = Rest(dividends=TimeoutError(SECRET)); result = run(rest=rest)
    assert result["reason_counts"]["provider_timeout"] == 1 and len(rest.calls) == 1
    assert result["sdk_socket_cancellation_supported"] is False
    assert result["sdk_response_bound_enforced_before_allocation"] is False


@pytest.mark.parametrize("cancel", [False, True])
def test_deadline_or_cancellation_before_work_makes_zero_calls(cancel):
    event = threading.Event()
    if cancel: event.set()
    rest = Rest(); result = run(rest=rest, deadline=200 if cancel else 100, cancelled=event)
    assert rest.calls == [] and result["status"] == "blocked"
    assert result["reason_counts"]["request_cancelled" if cancel else "request_time_budget"] == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_deadline_or_cancellation_after_first_call_discards_and_stops(monkeypatch, cancel):
    event = threading.Event()
    def late():
        if cancel: event.set()
        else: monkeypatch.setattr(diag.clock, "monotonic", lambda: 300.0)
        return {"data": [cash()]}
    rest = Rest(dividends=late); result = run(rest=rest, cancelled=event)
    assert len(rest.calls) == 1
    assert result["corporate_actions"]["dividends"]["cash_event_count"] == 0
    assert result["reason_counts"]["request_cancelled" if cancel else "request_time_budget"] == 1


def test_deadline_after_processing_prevents_second_endpoint(monkeypatch):
    original = diag.inspect_action_response
    def slow(*args, **kwargs):
        response = original(*args, **kwargs)
        monkeypatch.setattr(diag.clock, "monotonic", lambda: 300.0)
        return response
    monkeypatch.setattr(diag, "inspect_action_response", slow)
    rest = Rest(dividends={"data": [cash()]}); result = run(rest=rest)
    assert len(rest.calls) == 1 and result["reason_counts"]["request_time_budget"] == 1
    assert result["corporate_actions"]["dividends"]["cash_event_count"] == 0


def test_late_exception_is_deadline_not_provider_failure(monkeypatch):
    def late():
        monkeypatch.setattr(diag.clock, "monotonic", lambda: 300.0)
        raise ProviderError(401)
    rest = Rest(dividends=late); result = run(rest=rest)
    assert len(rest.calls) == 1 and result["reason_counts"]["request_time_budget"] == 1
    assert result["corporate_actions"]["dividends"]["status"] == "request_time_budget"


def test_missing_sdk_method_stops_before_call():
    rest = Rest(); rest.corporate_actions.dividends = None
    result = run(rest=rest)
    assert result["reason_counts"]["sdk_method_unavailable"] == 1 and rest.calls == []


def test_formal_mutation_is_detected_without_exporting_hash():
    parts = fixture()
    def mutate():
        parts[0]["data"][0]["price"] += 1
        return {"data": []}
    result = run(parts, Rest(dividends=mutate))
    assert result["formal_v6_unchanged"] is False
    assert result["reason_counts"]["formal_input_mutated"] == 1
    assert result["status"] == "blocked"


def test_public_projection_exports_no_rows_values_hashes_symbols_or_provider_text(monkeypatch):
    parts = fixture()
    original = cohort.daily_features
    measured = []
    def compute(bars):
        value = original(bars); measured.append(value)
        return {**value, "private_marker": SECRET}
    monkeypatch.setattr(cohort, "daily_features", compute)
    rest = Rest(dividends={"data": [cash()], "nextCursor": SECRET, "vendorSecretName": SECRET},
                capital={"data": [capital()]})
    result = run(parts, rest); serialized = json.dumps(result)
    assert len(measured) == 2 and measured[0]["price"] == parts[2]["2330.TW"]["bars"][-1]["close"]
    for forbidden in [SECRET, "2330", "6290", "vendorSecretName", "9876543.125", "7654321.5",
                      parts[2]["2330.TW"]["response_sha256"], "raw_record", "sha256", '"score"', '"rank"', '"ohlcv"', '"features"']:
        assert forbidden not in serialized
    assert result["hashes_exported"] is result["market_values_exported"] is False
    assert result["official_network_calls"] == 0


def test_injected_calendar_object_uses_existing_verified_contract():
    parts = fixture(); evidence = parts[3]; calls = []
    class Calendar:
        def lookup(self, *args):
            calls.append(args)
            return deepcopy(evidence)
    result = diag.build_diagnostics(Rest(), parts[2], Calendar(), formal=parts[0], manifest=parts[1],
                                   official_records=parts[4], now=NOW, deadline=200, cancelled=None)
    assert result["status"] == "metadata_diagnostics_complete_held"
    assert len(calls) == 1 and calls[0][0] == "TW"


def test_no_injected_official_records_never_trigger_network():
    parts = fixture()
    result = diag.build_diagnostics(Rest(), parts[2], parts[3], formal=parts[0], manifest=parts[1],
                                   now=NOW, deadline=200, cancelled=None)
    assert result["official_unavailable_count"] == 2 and result["official_network_calls"] == 0


@pytest.mark.parametrize("changes", [{"raw": None}, {"actionType": "etf_split_or_merge", "raw": {}},
    {"actionType": "etf_split_or_merge", "raw": {"splitType": SECRET}}])
def test_positive_capital_family_proves_structural_without_optional_detail(changes):
    safe, _ = inspect([capital(**changes)], "capital_changes")
    assert safe["structural_event_count"] == 1


@pytest.mark.parametrize("kind", ["權", "權息"])
def test_explicit_rights_proves_structural_without_optional_amounts(kind):
    row = cash(dividendType=kind); row.pop("cashDividend"); row.pop("stockDividendShares")
    safe, _ = inspect([row])
    assert safe["structural_event_count"] == 1


@pytest.mark.parametrize("envelope,status", [({}, "absent"),
    ({"start_date": "2026-06-22", "end_date": "2026-10-08"}, "match"),
    ({"start_date": "2026-06-22"}, "partial_match"),
    ({"start_date": "2026-06-23"}, "conflict"), ({"end_date": SECRET}, "invalid")])
def test_echoed_ranges_are_validated_without_public_provider_strings(envelope, status):
    safe, _ = inspect([], **envelope)
    assert safe["range_echo_status"] == status
    assert SECRET not in json.dumps(safe)


@pytest.mark.parametrize("envelope,status", [({}, "absent"), ({"page": 1}, "present_unknown"),
    ({"nextCursor": SECRET}, "continuation_indicated"), ({"hasMore": True}, "continuation_indicated"),
    ({"hasMore": False}, "no_continuation_indicated"), ({"nextPage": 2}, "continuation_indicated"),
    ({"nextCursor": None}, "no_continuation_indicated"), ({"next": SECRET, "hasMore": False}, "conflicting_indicators"),
    ({"hasMore": SECRET}, "invalid"), ({"nextCursor": {}}, "invalid")])
def test_pagination_metadata_never_causes_followup(envelope, status):
    safe, _ = inspect([], **envelope)
    assert safe["pagination_status"] == status and safe["coverage_status"] == diag.COVERAGE
    assert SECRET not in json.dumps(safe)


@pytest.mark.parametrize("envelope,status", [({}, "absent"), ({"total": 0}, "matches_response_count"),
    ({"totalCount": 50}, "exceeds_response_count"), ({"total": "50"}, "invalid"),
    ({"total": True}, "invalid"), ({"total": 1, "count": 0}, "conflicting")])
def test_numeric_totals_are_compared_without_claiming_completeness(envelope, status):
    safe, _ = inspect([], **envelope)
    assert safe["total_count_status"] == status and safe["coverage_status"] == diag.COVERAGE


def test_local_endpoint_timing_is_observed_on_success_and_failure():
    for rest in (Rest(), Rest(dividends=ProviderError(429))):
        endpoint = run(rest=rest)["corporate_actions"]["dividends"]
        requested = datetime.fromisoformat(endpoint["requested_at"])
        observed = datetime.fromisoformat(endpoint["observed_at"])
        assert requested.tzinfo is not None and observed >= requested
        assert 0 <= endpoint["elapsed_ms"] <= 86400000


def test_repeated_conflicting_rows_are_also_deduplicated():
    safe, _ = inspect([cash(), cash(), cash(cashDividend=4), cash(cashDividend=4), cash(cashDividend=5)])
    assert safe["duplicate_row_count"] == 2
    assert safe["conflicting_event_count"] == safe["unknown_event_count"] == 1


def test_generic_error_envelope_with_data_does_not_look_successful():
    rest = Rest(dividends={"data": [], "statusCode": 500, "message": SECRET})
    result = run(rest=rest)
    assert result["reason_counts"]["provider_failure"] == 1 and len(rest.calls) == 1


def test_response_5000_row_boundary_is_accepted_but_never_proves_completeness():
    safe, private = inspect([{"symbol": "9999", "date": "2026-07-15"}] * 5000)
    assert safe["response_count"] == safe["unrelated_row_count"] == 5000
    assert private == [] and safe["coverage_status"] == diag.COVERAGE


def test_below_response_count_metadata_is_explicit():
    safe, _ = inspect([cash()], total=0)
    assert safe["total_count_status"] == "below_response_count"


def test_second_endpoint_failure_retains_first_safe_observation():
    rest = Rest(dividends={"data": [cash()]}, capital=ProviderError(403))
    result = run(rest=rest)
    assert len(rest.calls) == 2 and result["status"] == "blocked"
    assert result["corporate_actions"]["dividends"]["cash_event_count"] == 1
    assert result["corporate_actions"]["capital_changes"]["status"] == "provider_entitlement_denied"
    assert result["corporate_action_coverage_verified"] is False


@pytest.mark.parametrize("total", [5, None, "3.125", -1, True])
def test_present_total_dividend_cannot_contradict_cash_only(total):
    safe, private = inspect([cash(dividend=total)])
    assert safe["unknown_event_count"] == 1 and safe["cash_event_count"] == 0
    assert private[0]["classification"] == "unknown"


def test_matching_optional_total_dividend_allows_cash_classification():
    safe, _ = inspect([cash(dividend=3.125)])
    assert safe["cash_event_count"] == 1
    assert safe["row_field_presence_counts"]["dividend"] == 1


def test_conflicting_totals_preserve_possible_truncation_signal():
    safe, _ = inspect([], count=0, total=50)
    assert safe["total_count_status"] == "conflicting"
    assert safe["truncation_signal"] is True
    assert safe["coverage_status"] == diag.COVERAGE
