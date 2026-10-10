"""Offline integration fixtures: no network, credentials, or live SDK."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

import fubon_daily_history as f
from tools.probe_fubon_daily_history import run_probe, sanitize_status
from test_fubon_daily_history import Calendar, NOW, SESSIONS, payload
from tw_daily_shadow_attestation import SOURCES, parse_official_price_rows


def inputs():
    rows = [{"symbol": s, "market": "TW", "type": "個股", "official_session_date": SESSIONS[-1]} for s in f.PILOT]
    rows.extend({"symbol": f"{1000+i}.TW", "market": "TW", "type": "ETF" if i % 3 == 0 else "個股",
                 "official_session_date": SESSIONS[-1]} for i in range(190))
    formal = {"updated_at": NOW.isoformat(), "data": rows}
    manifest = [{k: r[k] for k in ("symbol", "market", "type")} for r in rows]
    return formal, manifest


def raw(source, *, day=None):
    spec = SOURCES[source]
    return {spec["symbol"]: "2330" if source == "TWSE OpenAPI" else "6290", "Date": day or SESSIONS[-1],
            **dict(zip(spec["fields"], [10, 12, 9, 11, 1234]))}


class Response:
    def __init__(self, data=None, *, status=200, headers=None, body=None):
        self.status_code, self.headers, self.closed = status, headers or {}, False
        self.body = body if body is not None else json.dumps(data).encode()

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset+chunk_size]

    def close(self):
        self.closed = True


def fixtures(monkeypatch):
    history_calls, ca_calls, get_calls = [], [], []
    private_refs = []
    def candles(**kwargs):
        history_calls.append(kwargs)
        return payload("2330.TW" if kwargs["symbol"] == "2330" else "6290.TWO")
    def action(name):
        def call(**kwargs):
            ca_calls.append((name, kwargs))
            return {"data": []}
        return call
    stock = SimpleNamespace(historical=SimpleNamespace(candles=candles), corporate_actions=SimpleNamespace(
        dividends=action("dividends"), capital_changes=action("capital_changes")))
    sdk = SimpleNamespace(marketdata=SimpleNamespace(rest_client=SimpleNamespace(stock=stock)))
    def get(url, **kwargs):
        get_calls.append((url, kwargs))
        source = next(name for name, spec in SOURCES.items() if spec["url"] == url)
        return Response([raw(source), {SOURCES[source]["symbol"]: "9000", "Date": SESSIONS[-1]}])
    import requests
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(f, "_load_frozen_cohort_input", inputs)
    collector = f.collect_private
    def wrapped(*a, **kw):
        safe, private = collector(*a, **kw)
        private_refs.append(private)
        return safe, private
    monkeypatch.setattr(f, "collect_private", wrapped)
    return sdk, history_calls, ca_calls, get_calls, private_refs


def test_opt_in_full_manifest_two_histories_two_ca_two_official_reads(monkeypatch):
    sdk, history, ca, gets, refs = fixtures(monkeypatch)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    clean = sanitize_status(result)
    assert clean["status"] == "validated_in_memory", result
    diagnostic = clean["cohort_diagnostics"]
    assert diagnostic["status"] == "metadata_diagnostics_complete_held"
    assert diagnostic["full_manifest_count"] == 192
    assert diagnostic["acquired_history_count"] == 2
    assert diagnostic["unacquired_history_count"] == 190
    assert diagnostic["daily_features_rebuilt_count"] == diagnostic["official_match_count"] == 2
    assert len(history) == len(ca) == len(gets) == 2
    assert diagnostic["corporate_action_request_count"] == diagnostic["official_request_count"] == 2
    assert diagnostic["official_network_calls"] == 2
    assert diagnostic["corporate_action_coverage_verified"] is False
    assert diagnostic["formal_v6_unchanged"] is True
    assert all(ref == {} for ref in refs)
    assert all(call[1]["stream"] is True and call[1]["allow_redirects"] is False and 0 < call[1]["timeout"] <= 5 for call in gets)
    assert all(call[1] == {"start_date": SESSIONS[0], "end_date": SESSIONS[-1]} for call in ca)
    text = json.dumps(diagnostic)
    assert "1234" not in text and "sha256" not in text and '"raw_record"' not in text and '"bars"' not in text


def test_default_pilot_is_unchanged(monkeypatch):
    sdk, history, ca, gets, _ = fixtures(monkeypatch)
    monkeypatch.setattr(f, "_load_frozen_cohort_input", lambda: pytest.fail("default read formal report"))
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar())
    assert result["validated_count"] == 2 and len(history) == 2
    assert "cohort_diagnostics" not in result and not ca and not gets


def test_diagnostics_requires_both_pilots_before_work():
    with pytest.raises(ValueError):
        f.DailyPilot().status(None, ["2330.TW"], Calendar(), include_cohort_diagnostics=True)


def test_history_denial_has_zero_additional_reads(monkeypatch):
    sdk, _, ca, gets, _ = fixtures(monkeypatch)
    def denied(**kwargs):
        error = RuntimeError("provider secret")
        error.status_code = 403
        raise error
    sdk.marketdata.rest_client.stock.historical.candles = denied
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    assert result["reason"] == "provider_entitlement_denied"
    assert "cohort_diagnostics" not in result and not ca and not gets


def test_missing_local_report_blocks_diagnostics_without_additional_calls(monkeypatch):
    sdk, _, ca, gets, _ = fixtures(monkeypatch)
    def absent():
        raise FileNotFoundError()
    monkeypatch.setattr(f, "_load_frozen_cohort_input", absent)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    assert sanitize_status(result)["cohort_diagnostics"]["status"] == "blocked"
    assert not ca and not gets


def test_frozen_input_is_complete_typed_and_bounded(tmp_path):
    path = tmp_path / "all_analysis.json"
    formal, manifest = inputs()
    formal["data"][2]["symbol"] = "00886.TWO"
    path.write_text(json.dumps(formal))
    loaded, actual = f._load_frozen_cohort_input(path)
    assert loaded == formal and len(actual) == 192
    assert actual[2] == {"symbol": "00886.TWO", "market": "TW", "type": "ETF"}
    assert all("venue" not in row for row in actual)
    path.write_bytes(b" " * (f.MAX_FORMAL_BYTES + 1))
    with pytest.raises(ValueError, match="byte_budget"):
        f._load_frozen_cohort_input(path)


def private_histories():
    _, private = f.collect_private(SimpleNamespace(historical=SimpleNamespace(candles=lambda **k: payload("2330.TW" if k["symbol"] == "2330" else "6290.TWO"))), list(f.PILOT), Calendar(), now=NOW)
    return private


@pytest.mark.parametrize("stamp,reused", [(NOW.isoformat(), True), ("2099-01-01T00:00:00+00:00", False), ("2026-10-08T00:00:00+00:00", False)])
def test_reused_snapshots_need_post_session_nonfuture_timestamp(stamp, reused):
    formal, _ = inputs()
    for row, source in zip(formal["data"][:2], SOURCES):
        row["shadow_daily_ohlcv_proof"] = parse_official_price_rows(source, [raw(source)], fetched_at=stamp)[row["symbol"]]
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return Response([])
    records, audit = f._official_pilot_records(formal, private_histories(), deadline=time.monotonic()+20, cancelled=threading.Event(), get=get)
    assert len(calls) == (0 if reused else 2)
    assert len(records) == (2 if reused else 0)
    assert audit["official_reused_record_count"] == (2 if reused else 0)


@pytest.mark.parametrize("response,reason", [
    (Response([], status=302), "official_redirect_blocked"),
    (Response([], status=403), "official_http_denied"),
    (Response([], headers={"Content-Length": str(f.MAX_OFFICIAL_BYTES+1)}), "official_body_budget"),
    (Response(body=b"x"*(f.MAX_OFFICIAL_BYTES+1)), "official_body_budget"),
    (Response([{}]*5001), "official_row_budget"),
    (Response(body=b"private malformed json"), "official_parse_failure"),
    (Response({"data": []}), "official_payload_invalid"),
])
def test_official_failure_is_bounded_coded_and_not_retried(response, reason):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response
    records, audit = f._official_pilot_records({}, private_histories(), deadline=time.monotonic()+20, cancelled=threading.Event(), get=get)
    assert not records and len(calls) == 2 and response.closed
    assert audit["official_reason_counts"] == {reason: 2}
    assert "private" not in json.dumps(audit)


def test_cancel_after_first_official_read_stops_second():
    cancel, calls = threading.Event(), []
    def get(url, **kwargs):
        calls.append(url)
        cancel.set()
        return Response([])
    with pytest.raises(f.HistoryBlocked, match="request_time_budget"):
        f._official_pilot_records({}, private_histories(), deadline=time.monotonic()+20, cancelled=cancel, get=get)
    assert len(calls) == 1


def test_probe_flag_is_explicit_and_sanitizer_strips_private_fields(monkeypatch):
    sdk, *_ = fixtures(monkeypatch)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    result["cohort_diagnostics"]["private"] = {"price": 1234, "sha256": "secret"}
    result["cohort_diagnostics"]["corporate_actions"]["dividends"]["raw"] = "secret"
    calls = []
    def relay(kind, request, timeout):
        calls.append(request)
        return result
    clean = run_probe(relay, include_cohort_diagnostics=True)
    assert calls == [{"symbols": list(f.PILOT), "include_cohort_diagnostics": True}]
    assert "secret" not in json.dumps(clean) and "1234" not in json.dumps(clean)
    result["cohort_diagnostics"]["reason_counts"] = {"provider secret": 1}
    assert sanitize_status(result)["reason"] == "invalid_or_unavailable_relay_status"


def test_stage_b_never_warms_missing_session_or_logs_in():
    calls = []
    def relay(kind, request, timeout):
        calls.append((kind, request))
        return f.empty_status(list(f.PILOT), NOW, "existing_session_unavailable")
    result = run_probe(relay, include_cohort_diagnostics=True)
    assert result["reason"] == "existing_session_unavailable"
    assert calls == [("tw_daily_history_status", {"symbols": list(f.PILOT), "include_cohort_diagnostics": True})]


def test_workflow_diagnostics_only_in_existing_source_validation_job():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/stock-briefing.yml").read_text()
    block = workflow.split("      tw_cohort_diagnostics:\n", 1)[1].split("      validation_only:", 1)[0]
    assert "default: false" in block and "type: boolean" in block
    assert "--include-cohort-diagnostics" in workflow.split("  source_validation:\n", 1)[1].split("  gate:\n", 1)[0]
    assert "--include-cohort-diagnostics" not in workflow.split("  briefing:\n", 1)[1]


def test_existing_api_service_forwards_only_explicit_boolean_and_has_health_marker():
    from live_api import LiveDataService
    from tw_private_cohort_diagnostics import VERSION
    service = LiveDataService()
    calls = []
    service._daily_pilot = SimpleNamespace(status=lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    service.daily_history_status(list(f.PILOT), Calendar())
    service.daily_history_status(list(f.PILOT), Calendar(), include_cohort_diagnostics="true")
    service.daily_history_status(list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    assert [kw["include_cohort_diagnostics"] for _, kw in calls] == [False, False, True]
    assert all(args[0] is None for args, _ in calls)
    marker = service.health()["tw_daily_history_probe"]["private_cohort_diagnostics"]
    assert marker == {"version": VERSION, "supported": True, "enabled_by_default": False, "pilot_symbol_count": 2}


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(ranking_executed=True),
    lambda d: d.update(acquired_history_count=True),
    lambda d: d.update(unacquired_history_count=189),
    lambda d: d.update(official_request_count=3),
    lambda d: d.update(history_from="private"),
    lambda d: d.update(reason_counts={"private": 1}),
    lambda d: d.update(official_reason_counts={"private": 1}),
    lambda d: d["corporate_actions"]["dividends"].update(metadata_fields_present=["private"]),
    lambda d: d["corporate_actions"]["dividends"].update(range_echo_status="private"),
    lambda d: d["corporate_actions"]["dividends"].update(requested_at="private"),
    lambda d: d["corporate_actions"]["dividends"].update(elapsed_ms=True),
    lambda d: d["corporate_actions"]["dividends"].update(truncation_signal="private"),
])
def test_diagnostic_metadata_must_match_strict_public_schema(monkeypatch, mutate):
    sdk, *_ = fixtures(monkeypatch)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    mutate(result["cohort_diagnostics"])
    assert sanitize_status(result)["reason"] == "invalid_or_unavailable_relay_status"


def test_official_deadline_reason_is_preserved_by_worker(monkeypatch):
    sdk, _, ca, _, refs = fixtures(monkeypatch)
    def expired(*args, **kwargs):
        raise f.HistoryBlocked("request_time_budget")
    monkeypatch.setattr(f, "_official_pilot_records", expired)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    assert result["reason"] == "request_time_budget"
    assert result["request_count_known"] is False and result["request_count"] is None
    assert not ca and all(ref == {} for ref in refs)


def test_official_latest_session_mismatch_is_explicit():
    def get(url, **kwargs):
        source = next(name for name, spec in SOURCES.items() if spec["url"] == url)
        return Response([raw(source, day="2026-10-08")])
    records, audit = f._official_pilot_records({}, private_histories(), deadline=time.monotonic()+20, cancelled=threading.Event(), get=get)
    assert len(records) == 2 and audit["official_reason_counts"] == {"official_session_mismatch": 2}


def test_ca_timeout_keeps_guard_and_starts_no_second_call(monkeypatch):
    sdk, _, _, _, refs = fixtures(monkeypatch)
    started, release = threading.Event(), threading.Event()
    calls = []
    def slow(**kwargs):
        calls.append("dividends")
        started.set()
        release.wait(2)
        return {"data": []}
    sdk.marketdata.rest_client.stock.corporate_actions.dividends = slow
    sdk.marketdata.rest_client.stock.corporate_actions.capital_changes = lambda **kw: calls.append("capital_changes")
    pilot = f.DailyPilot()
    first = pilot.status(sdk, list(f.PILOT), Calendar(), timeout=.05, include_cohort_diagnostics=True)
    assert started.is_set() and first["reason"] == "request_time_budget"
    second = pilot.status(sdk, list(f.PILOT), Calendar(), timeout=.05, include_cohort_diagnostics=True)
    assert second["reason"] == "request_in_progress"
    release.set()
    deadline = time.monotonic() + 2
    while pilot._guard.locked() and time.monotonic() < deadline:
        time.sleep(.005)
    assert calls == ["dividends"] and all(ref == {} for ref in refs)
