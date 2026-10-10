"""Offline bulk-feed safety fixtures; no exchange, SDK, or relay calls."""
from copy import deepcopy
import gzip
from io import BytesIO
import json
import threading
import time

import pytest

import fubon_daily_history as f
from tools.probe_fubon_daily_history import sanitize_official_sources, sanitize_status
from tw_daily_shadow_attestation import SOURCES
from test_tw_private_diagnostics_integration import fixtures
from test_fubon_daily_history import Calendar

DAY = "2026-10-09"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("unexpected network request"))


def raw(source="TPEx OpenAPI", symbol=None):
    spec = SOURCES[source]
    return {spec["symbol"]: symbol or ("6290" if source == "TPEx OpenAPI" else "2330"),
            "Date": DAY, **dict(zip(spec["fields"], ["10", "12", "9", "11", "1234"]))}


class Response:
    def __init__(self, rows=None, *, body=None, headers=None, status=200):
        self.body = body if body is not None else json.dumps(rows if rows is not None else [], ensure_ascii=False).encode()
        self.headers, self.status_code = headers or {}, status
        self.closed, self.chunks_read = False, 0

    def iter_content(self, chunk_size):
        assert chunk_size == f.OFFICIAL_CHUNK_BYTES
        for offset in range(0, len(self.body), chunk_size):
            self.chunks_read += 1
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


def collect(response, source="TPEx OpenAPI"):
    spec = SOURCES[source]
    symbol = "6290.TWO" if source == "TPEx OpenAPI" else "2330.TW"
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert url == spec["url"]
        assert kwargs["stream"] is True and kwargs["allow_redirects"] is False
        assert 0 < kwargs["timeout"] <= 5
        assert set(kwargs) == {"stream", "allow_redirects", "timeout", "headers"}
        return response
    records, audit = f._official_pilot_records({}, {symbol: {"source_session_date": DAY}},
                                               deadline=time.monotonic() + 20,
                                               cancelled=threading.Event(), get=get)
    assert len(calls) == 1 and response.closed
    assert set(audit["official_sources"]) == {"TWSE", "TPEX_MAINBOARD"}
    assert sanitize_official_sources(audit) == audit["official_sources"]
    return records, audit, audit["official_sources"][f.OFFICIAL_SOURCE_IDS[source]]


@pytest.mark.parametrize("source", list(SOURCES))
def test_more_than_5000_bulk_rows_keep_only_requested_pilot(monkeypatch, source):
    import tw_daily_shadow_attestation as official
    original, retained = official.parse_official_price_rows, []
    def parse(name, rows, **kwargs):
        retained.append(deepcopy(rows))
        return original(name, rows, **kwargs)
    monkeypatch.setattr(official, "parse_official_price_rows", parse)
    rows = [raw(source, f"{i:06d}") for i in range(6000)] + [raw(source)]
    response = Response(rows)
    records, audit, metadata = collect(response, source)
    assert len(records) == 1 and retained == [[raw(source)]]
    assert metadata["row_count_observed"] == 6001
    assert metadata["decoded_bytes_observed"] == len(response.body)
    assert metadata["rows_complete"] and metadata["body_complete"]
    assert metadata["advertised_bytes"] is None
    assert audit["official_reason_counts"] == {"official_records_available": 1}


@pytest.mark.parametrize("copies", [2, 1000, f.MAX_OFFICIAL_ROWS])
def test_duplicate_pilot_rows_remain_rejected_with_two_retained(monkeypatch, copies):
    import tw_daily_shadow_attestation as official
    original, retained = official.parse_official_price_rows, []
    def parse(name, rows, **kwargs):
        retained.append(len(rows))
        return original(name, rows, **kwargs)
    monkeypatch.setattr(official, "parse_official_price_rows", parse)
    records, _, metadata = collect(Response([raw()] * copies))
    assert records == {} and retained == [2]
    assert metadata["status"] == "official_record_unavailable"
    assert metadata["row_count_observed"] == copies


def test_bulk_row_ceiling_inclusive_and_only_one_excess_row_observed():
    records, _, metadata = collect(Response([{}] * f.MAX_OFFICIAL_ROWS))
    assert not records and metadata["rows_complete"]
    assert metadata["row_count_observed"] == f.MAX_OFFICIAL_ROWS
    # Input after the 20,001st complete row must never be decoded.
    body = b"[" + b"{}," * (f.MAX_OFFICIAL_ROWS + 1) + b'{"x": [[INVALID'
    records, _, metadata = collect(Response(body=body))
    assert not records and metadata["status"] == "official_row_budget"
    assert metadata["row_count_observed"] == f.MAX_OFFICIAL_ROWS + 1
    assert metadata["row_budget_exceeded"] and not metadata["rows_complete"]


@pytest.mark.parametrize("header", [str(f.MAX_OFFICIAL_BYTES + 1), "9" * 1000])
def test_advertised_overflow_rejects_before_body_read(header):
    response = Response([raw()], headers={"Content-Length": header})
    records, _, metadata = collect(response)
    assert not records and response.chunks_read == 0
    assert metadata["status"] == "official_body_budget"
    assert metadata["advertised_length_status"] == "over_budget"
    assert metadata["advertised_byte_budget_exceeded"]
    assert metadata["advertised_bytes"] is None
    assert metadata["decoded_bytes_observed"] == metadata["row_count_observed"] == 0


@pytest.mark.parametrize("header", ["-1", "1.0", "1,2", "private header", "１２３", True])
def test_invalid_advertised_size_is_finite_and_not_exported(header):
    response = Response([raw()], headers={"Content-Length": header})
    records, audit, metadata = collect(response)
    assert not records and response.chunks_read == 0
    assert metadata["advertised_length_status"] == "invalid"
    assert metadata["status"] == "official_body_budget"
    assert not metadata["advertised_byte_budget_exceeded"]
    assert "private header" not in json.dumps(audit)


def test_decoded_byte_ceiling_and_observed_overflow_are_separate():
    response = Response(body=b"[]" + b" " * (f.MAX_OFFICIAL_BYTES - 2))
    _, _, metadata = collect(response)
    assert metadata["decoded_bytes_observed"] == f.MAX_OFFICIAL_BYTES
    assert metadata["rows_complete"] and not metadata["decoded_byte_budget_exceeded"]
    response = Response(body=b" " * (f.MAX_OFFICIAL_BYTES + 1), headers={"Content-Length": "100", "Content-Encoding": "gzip"})
    records, _, metadata = collect(response)
    assert not records and metadata["status"] == "official_body_budget"
    assert metadata["advertised_bytes"] == 100
    assert metadata["decoded_bytes_observed"] == f.MAX_OFFICIAL_BYTES + 1
    assert metadata["decoded_byte_budget_exceeded"] and not metadata["body_complete"]


@pytest.mark.parametrize("encoding,expected", [(None, "absent"), ("identity", "identity"), ("GZip", "gzip"),
                                               ("deflate", "deflate"), ("br", "br"), ("private encoding", "other")])
def test_encoding_is_finite_and_advertised_is_not_decoded_bytes(encoding, expected):
    headers = {"Content-Length": "0" * 100 + "1"}
    if encoding is not None:
        headers["Content-Encoding"] = encoding
    response = Response([raw()], headers=headers)
    records, audit, metadata = collect(response)
    assert len(records) == 1
    assert metadata["advertised_bytes"] == 1
    assert metadata["decoded_bytes_observed"] == len(response.body) > 1
    assert metadata["content_encoding"] == expected
    assert "private encoding" not in json.dumps(audit)


@pytest.mark.parametrize("body,reason", [
    (b'{"data": []}', "official_payload_invalid"),
    (b'[null]', "official_payload_invalid"),
    (b'[[1]]', "official_payload_invalid"),
    (b'[{"x": []}]', "official_payload_invalid"),
    (b'[{"x": {}}]', "official_payload_invalid"),
    (b'[{"x": NaN}]', "official_payload_invalid"),
    (b'[{"x": Infinity}]', "official_payload_invalid"),
    (b'[{"x": -Infinity}]', "official_payload_invalid"),
    (b'[{"x": 1e309}]', "official_payload_invalid"),
    (b'[{"x": 1, "x": 2}]', "official_payload_invalid"),
    (b'[{"SecuritiesCompanyCode": "6290", "SecuritiesCompanyCode": "9000"}]', "official_payload_invalid"),
    (b'[{"Open": "10", "Open": "11"}]', "official_payload_invalid"),
    (b'[{}] {}', "official_parse_failure"),
    (b'[{},]', "official_payload_invalid"),
    (b'[{"x": 1,}]', "official_parse_failure"),
    (b'[{} {}]', "official_parse_failure"),
    (b'[{"x": 01}]', "official_parse_failure"),
    (b'[{"x": tru}]', "official_parse_failure"),
    (b'[{"x": "bad\\q"}]', "official_parse_failure"),
    (b'[{"x": "\xff"}]', "official_parse_failure"),
    (b'[{"x": "unterminated}]', "official_parse_failure"),
    (b'', "official_parse_failure"),
])
def test_malformed_bulk_rejects_without_partial_pilot_output(body, reason):
    records, _, metadata = collect(Response(body=body))
    assert not records and metadata["status"] == reason
    assert metadata["body_complete"] and not metadata["rows_complete"]


def test_nested_deep_rows_are_rejected_before_json_value_allocation(monkeypatch):
    calls = []
    original = json.loads
    def loads(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)
    monkeypatch.setattr(f.json, "loads", loads)
    body = b'[{"x":' + b'[' * 5000 + b'0' + b']' * 5000 + b'}]'
    records, _, metadata = collect(Response(body=body))
    assert not records and metadata["status"] == "official_payload_invalid"
    assert calls == []


@pytest.mark.parametrize("row", [
    {"k" * 129: 1}, {"x": "v" * 4097}, {f"k{i}": 1 for i in range(65)},
    {f"k{i}": "v" * 4096 for i in range(64)},
])
def test_flat_field_key_string_and_total_row_spans_are_bounded(row):
    records, _, metadata = collect(Response([row, raw()]))
    assert not records and metadata["status"] == "official_payload_invalid"
    assert not metadata["rows_complete"]


def test_field_and_string_boundaries_and_escaped_punctuation_are_accepted():
    row = {f"k{i}": 1 for i in range(63)}
    row["k" * 128] = "v" * 4096
    row["k0"] = 'comma, bracket[ brace{ end} quote" slash\\ \t\n 中文 🎯'
    row["k1"], row["k2"], row["k3"] = True, False, None
    records, _, metadata = collect(Response([row, raw()]))
    assert len(records) == 1 and metadata["rows_complete"]
    assert metadata["row_count_observed"] == 2


def test_escaped_unicode_string_boundary_is_accepted():
    body = b'[{"x":"' + b'\\u4e2d' * 4096 + b'"},' + json.dumps(raw()).encode() + b']'
    records, _, metadata = collect(Response(body=body))
    assert len(records) == 1 and metadata["rows_complete"]


def test_valid_pilot_followed_by_malformed_nonpilot_never_returns_partial_match():
    records, _, metadata = collect(Response([raw(), {"unrelated": [1, 2]}]))
    assert not records and metadata["status"] == "official_payload_invalid"
    assert metadata["row_count_observed"] == 1


@pytest.mark.parametrize("status,reason", [(302, "official_redirect_blocked"), (401, "official_http_denied"),
                                          (403, "official_http_denied"), (429, "official_rate_limited"),
                                          (500, "official_http_error")])
def test_http_denial_no_body_no_retry_and_closed(status, reason):
    response = Response([raw()], status=status)
    records, _, metadata = collect(response)
    assert not records and response.chunks_read == 0
    assert metadata["status"] == reason and metadata["decoded_bytes_observed"] == 0


@pytest.mark.parametrize("phase", ["before_get", "after_get", "during_read", "during_parse"])
def test_deadline_or_cancellation_never_starts_second_source(monkeypatch, phase):
    cancel, calls = threading.Event(), []
    response = Response([raw("TWSE OpenAPI")] + [{}] * 10)
    if phase == "before_get":
        cancel.set()
    original_read = response.iter_content
    def chunks(chunk_size):
        for chunk in original_read(chunk_size):
            if phase == "during_read":
                cancel.set()
            yield chunk
    response.iter_content = chunks
    if phase == "during_parse":
        original = json.loads
        def loads(*args, **kwargs):
            cancel.set()
            return original(*args, **kwargs)
        monkeypatch.setattr(f.json, "loads", loads)
    def get(*args, **kwargs):
        calls.append(1)
        if phase == "after_get":
            cancel.set()
        return response
    with pytest.raises(f.HistoryBlocked, match="request_time_budget"):
        f._official_pilot_records({}, {s: {"source_session_date": DAY} for s in f.PILOT},
                                  deadline=time.monotonic() + 20, cancelled=cancel, get=get)
    assert len(calls) == (0 if phase == "before_get" else 1)
    assert response.closed == (phase != "before_get")


def test_expired_deadline_starts_no_request():
    with pytest.raises(f.HistoryBlocked, match="request_time_budget"):
        f._official_pilot_records({}, {"6290.TWO": {"source_session_date": DAY}},
                                  deadline=time.monotonic() - 1, cancelled=threading.Event(),
                                  get=lambda *a, **k: pytest.fail("expired request"))


def test_stream_error_closes_and_never_exports_exception():
    response = Response()
    def fail(chunk_size):
        yield b"[]"
        raise RuntimeError("private body and token")
    response.iter_content = fail
    records, audit, metadata = collect(response)
    assert not records and metadata["status"] == "official_request_failure"
    assert metadata["decoded_bytes_observed"] == 2 and not metadata["body_complete"]
    assert "private" not in json.dumps(audit)


def test_sanitizer_strips_arbitrary_private_fields(monkeypatch):
    sdk, *_ = fixtures(monkeypatch)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    diagnostic = result["cohort_diagnostics"]
    diagnostic["official_sources"]["TPEX_MAINBOARD"]["raw"] = {"price": 987654321, "sha256": "private marker"}
    diagnostic["official_sources"]["TWSE"]["Content-Length"] = "secret"
    clean = sanitize_status(result)
    text = json.dumps(clean)
    assert "987654321" not in text and "sha256" not in text and "private marker" not in text and "secret" not in text
    assert set(clean["cohort_diagnostics"]["official_sources"]) == {"TWSE", "TPEX_MAINBOARD"}


@pytest.mark.parametrize("mutate", [
    lambda d: d["official_sources"].update(EVIL={}),
    lambda d: d["official_sources"].pop("TWSE"),
    lambda d: d["official_sources"]["TWSE"].update(status="private"),
    lambda d: d["official_sources"]["TWSE"].update(content_encoding="private"),
    lambda d: d["official_sources"]["TWSE"].update(advertised_length_status="private"),
    lambda d: d["official_sources"]["TWSE"].update(advertised_bytes=123),
    lambda d: d["official_sources"]["TWSE"].update(decoded_bytes_observed=True),
    lambda d: d["official_sources"]["TWSE"].update(row_count_observed=f.MAX_OFFICIAL_ROWS + 2),
    lambda d: d["official_sources"]["TWSE"].update(body_complete="true"),
    lambda d: d["official_sources"]["TWSE"].update(rows_complete=False),
    lambda d: d["official_sources"]["TWSE"].update(row_budget_exceeded=True),
    lambda d: d["official_sources"]["TWSE"].update(decoded_byte_budget_exceeded=True),
    lambda d: d["official_sources"]["TWSE"].update(advertised_byte_budget_exceeded=True),
    lambda d: d["official_sources"]["TWSE"].update(status="official_http_error"),
])
def test_sanitizer_rejects_unbounded_or_inconsistent_source_diagnostics(monkeypatch, mutate):
    sdk, *_ = fixtures(monkeypatch)
    result = f.DailyPilot().status(sdk, list(f.PILOT), Calendar(), include_cohort_diagnostics=True)
    mutate(result["cohort_diagnostics"])
    assert sanitize_status(result)["reason"] == "invalid_or_unavailable_relay_status"


def test_no_snapshot_or_request_does_not_invent_observations():
    audit = f._official_audit()
    assert sanitize_official_sources(audit) == audit["official_sources"]
    for metadata in audit["official_sources"].values():
        assert metadata["status"] == "not_requested"
        assert metadata["advertised_bytes"] is None
        assert metadata["advertised_length_status"] == metadata["content_encoding"] == "not_observed"
        assert metadata["decoded_bytes_observed"] == metadata["row_count_observed"] == 0
        assert not metadata["body_complete"] and not metadata["rows_complete"]



def test_real_requests_decoding_enforces_expanded_body_ceiling_without_network():
    import requests
    from urllib3.response import HTTPResponse
    expanded = b" " * (f.MAX_OFFICIAL_BYTES + f.OFFICIAL_CHUNK_BYTES)
    compressed = gzip.compress(expanded)
    response = requests.Response()
    response.status_code = 200
    response.headers.update({"Content-Length": str(len(compressed)), "Content-Encoding": "gzip"})
    response.raw = HTTPResponse(body=BytesIO(compressed), headers=response.headers, preload_content=False)
    records, audit = f._official_pilot_records(
        {}, {"6290.TWO": {"source_session_date": DAY}},
        deadline=time.monotonic() + 20, cancelled=threading.Event(), get=lambda *a, **k: response)
    metadata = sanitize_official_sources(audit)["TPEX_MAINBOARD"]
    assert not records and metadata["status"] == "official_body_budget"
    assert metadata["advertised_bytes"] == len(compressed) < f.MAX_OFFICIAL_BYTES
    assert metadata["decoded_bytes_observed"] == f.MAX_OFFICIAL_BYTES + f.OFFICIAL_CHUNK_BYTES
    assert metadata["decoded_byte_budget_exceeded"] and not metadata["body_complete"]
    assert response.raw.closed


def test_deadline_after_fetch_closes_without_read_or_second_source(monkeypatch):
    now, calls, response = [0.0], [], Response([raw("TWSE OpenAPI")])
    monkeypatch.setattr(f.clock, "monotonic", lambda: now[0])
    def get(*args, **kwargs):
        calls.append(1)
        now[0] = 21.0
        return response
    with pytest.raises(f.HistoryBlocked, match="request_time_budget"):
        f._official_pilot_records({}, {s: {"source_session_date": DAY} for s in f.PILOT},
                                  deadline=20.0, cancelled=threading.Event(), get=get)
    assert calls == [1] and response.closed and response.chunks_read == 0


@pytest.mark.parametrize("status,mutate", [
    ("official_records_available", lambda row: row.update(decoded_bytes_observed=0, row_count_observed=0,
                                                         body_complete=True, rows_complete=True)),
    ("official_session_mismatch", lambda row: row.update(decoded_bytes_observed=2, row_count_observed=0,
                                                        body_complete=True, rows_complete=True)),
    ("official_row_budget", lambda row: row.update(decoded_bytes_observed=2, row_count_observed=0,
                                                 body_complete=True, rows_complete=True)),
    ("official_body_budget", lambda row: row.update(decoded_bytes_observed=0, row_count_observed=0)),
    ("official_body_budget", lambda row: row.update(advertised_length_status="invalid",
                                                  decoded_bytes_observed=2, body_complete=True, rows_complete=True)),
    ("official_parse_failure", lambda row: row.update(decoded_bytes_observed=2, body_complete=True, rows_complete=True)),
    ("official_payload_invalid", lambda row: row.update(decoded_bytes_observed=2, body_complete=True, rows_complete=True)),
    ("official_http_denied", lambda row: row.update(decoded_bytes_observed=2, body_complete=True)),
])
def test_sanitizer_rejects_coordinated_impossible_outcome_and_counts(status, mutate):
    audit = f._official_audit()
    audit["official_request_count"] = 1
    audit["official_reason_counts"] = {status: 1}
    row = audit["official_sources"]["TPEX_MAINBOARD"]
    row.update(status=status, advertised_length_status="absent", content_encoding="absent")
    mutate(row)
    with pytest.raises(ValueError):
        sanitize_official_sources(audit)
