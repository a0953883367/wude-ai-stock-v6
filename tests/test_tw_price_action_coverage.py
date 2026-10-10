from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest

from tw_price_action_coverage import (
    NO_MATCH, SOURCES, evaluate_tw_price_action_coverage,
    fetch_tw_price_action_coverage, fetch_tw_price_action_coverage_for_plans, source_request_url,
)


START, END = "2026-10-01", "2026-10-08"
NOW = "2026-10-10T01:00:00+00:00"


def report(source, rows=None):
    """Minimal named columns from the verified official report schemas."""
    spec = SOURCES[source]
    table = {"fields": [spec["date_field"], spec["code_field"], spec["price_field"]],
             "data": [] if rows is None else rows}
    if spec["venue"] != "TWSE":
        table["totalCount"] = len(table["data"])
        return {"stat": "ok", "date": "20261001~20261008", "tables": [table]}
    raw = {"stat": "OK", **table}
    if source == "twse_par_value_change":
        raw["params"] = {"startDate": "20261001", "endDate": "20261008"}
    elif source == "twse_etf_split_reverse":
        raw.update(startDate="20261001", endDate="20261008")
    else:
        raw.update(strDate="20261001", endDate="20261008")
    return raw


def envelope(source, rows=None):
    return {"source_id": source, "coverage_from": START, "through_session": END,
            "complete": True, "observed_at": NOW,
            "request_url": source_request_url(source, START, END),
            "payload": report(source, rows)}


def payload(venue="TWSE"):
    return {"venue": venue, "sources": [envelope(source) for source, spec in SOURCES.items()
                                       if spec["venue"] == venue]}


def evaluate(data, symbol="2330.TW", **kwargs):
    args = {"symbol": symbol, "original_session": START,
            "through_session": END, "observed_at": NOW, **kwargs}
    return evaluate_tw_price_action_coverage(data, **args)


def test_zero_events_requires_every_price_basis_family_and_declared_scope():
    assert evaluate(payload())["status"] == "clear"
    assert evaluate(payload("TPEX_MAINBOARD"), "6488.TWO")["status"] == "clear"
    for missing in range(4):
        incomplete = payload()
        incomplete["sources"].pop(missing)
        assert evaluate(incomplete)["status"] == "unsupported"
    assert evaluate({"events": [], "status": "ok"})["status"] == "unsupported"
    assert evaluate({"venue": "TWSE", "sources": []})["status"] == "unsupported"


@pytest.mark.parametrize("source", list(SOURCES))
def test_all_event_families_block_validated_event(source):
    venue = SOURCES[source]["venue"]
    symbol = "2330.TW" if venue == "TWSE" else "6488.TWO"
    data = payload(venue)
    data["sources"] = [envelope(source, [["115/10/07", symbol.split(".")[0], "60.00"]])]
    result = evaluate(data, symbol)
    assert result["status"] == "blocked"
    assert result["events"][0]["effective_session"] == "2026-10-07"
    assert result["events"][0]["family"] == SOURCES[source]["family"]
    assert result["reasons"]  # Positive evidence blocks despite missing other feeds.


@pytest.mark.parametrize("event_date", ["1151007", "20261007", "115年10月07日", "115/10/07", "2026-10-07"])
def test_verified_exchange_date_formats(event_date):
    data = payload()
    data["sources"][0] = envelope("twse_ex_right_dividend", [[event_date, "2330", "60"]])
    assert evaluate(data)["status"] == "blocked"


def test_original_close_already_incorporates_same_session_event():
    data = payload()
    data["sources"][0] = envelope("twse_ex_right_dividend", [["115/10/01", "2330", "60"]])
    assert evaluate(data)["status"] == "clear"
    data["sources"][0] = envelope("twse_ex_right_dividend", [["115/10/08", "2330", "60"]])
    assert evaluate(data)["status"] == "blocked"


def test_other_symbol_does_not_invalidate_plan():
    data = payload()
    data["sources"][0] = envelope("twse_ex_right_dividend", [["115/10/07", "0050", "60"]])
    assert evaluate(data)["status"] == "clear"


@pytest.mark.parametrize("change", [
    {"complete": False}, {"complete": "true"}, {"through_session": "2026-10-07"},
    {"observed_at": "2026-10-08T04:00:00+00:00"},
    {"observed_at": "2026-10-11T01:00:00+00:00"},
    {"observed_at": "2026-10-10T01:00:00"},
    {"request_url": "https://example.com/exRight/TWT49U"},
    {"request_url": source_request_url("twse_ex_right_dividend", START, END) + "&stockNo=2330"},
    {"payload": []}, {"payload": {"stat": "failed"}},
])
def test_unverified_or_incomplete_envelope_fails_closed(change):
    data = payload()
    data["sources"][0].update(change)
    assert evaluate(data)["status"] == "unsupported"


def test_no_match_contract_requires_exact_request_and_complete_envelope():
    data = payload()
    for item in data["sources"]:
        if item["source_id"] in {"twse_ex_right_dividend", "twse_capital_reduction"}:
            item["payload"] = {"stat": NO_MATCH}
    assert evaluate(data)["status"] == "clear"
    data["sources"][0]["complete"] = False
    assert evaluate(data)["status"] == "unsupported"
    data["sources"][0]["complete"] = True
    del data["sources"][0]["request_url"]
    assert evaluate(data)["status"] == "unsupported"
    assert evaluate({"stat": NO_MATCH})["status"] == "unsupported"


@pytest.mark.parametrize("case", ["count", "extra_table", "schema", "row_width", "date", "range", "duplicate", "echo"])
def test_malformed_or_partial_tpex_report_fails_closed(case):
    data = payload("TPEX_MAINBOARD")
    item = data["sources"][0]
    raw = item["payload"]
    table = raw["tables"][0]
    if case == "count":
        table["totalCount"] = 1
    elif case == "extra_table":
        raw["tables"].append(deepcopy(table))
    elif case == "schema":
        table["fields"][0] = "unknown"
    elif case == "echo":
        raw["date"] = "20261002~20261008"
    else:
        row = ["115/10/07", "6488", "60"]
        if case == "row_width":
            row.append("extra")
        elif case == "date":
            row[0] = "115/13/07"
        elif case == "range":
            row[0] = "115/10/09"
        table["data"] = [row, row] if case == "duplicate" else [row]
        table["totalCount"] = len(table["data"])
    assert evaluate(data, "6488.TWO")["status"] == "unsupported"


def test_duplicate_source_and_cross_venue_scope_cannot_clear():
    data = payload()
    data["sources"].append(deepcopy(data["sources"][0]))
    assert evaluate(data)["status"] == "unsupported"
    assert evaluate(payload(), "6488.TWO")["status"] == "unsupported"
    assert evaluate({**payload(), "venue": "TPEX_EMERGING"})["status"] == "unsupported"


def test_future_unclosed_or_overlong_window_not_attested():
    assert evaluate(payload(), observed_at="2026-10-08T05:29:59Z")["status"] == "unsupported"
    assert evaluate(payload(), original_session="2026-08-01")["status"] == "unsupported"
    assert evaluate(payload(), through_session="2026-09-30")["status"] == "unsupported"


def test_fetch_bulk_once_per_family_and_returns_no_raw_payload():
    calls = []

    def fetch(url):
        calls.append(url)
        source = next(source for source, spec in SOURCES.items() if url.split("?")[0] == spec["url"])
        return report(source)

    now = datetime.fromisoformat(NOW)
    result = fetch_tw_price_action_coverage({"2330.TW", "0050.TW", "6488.TWO", "8299.TWO"}, START, END, now=now, fetch_json=fetch)
    assert len(calls) == 9
    assert len(set(calls)) == 9
    assert {item["status"] for item in result.values()} == {"clear"}
    assert all("payload" not in source for item in result.values() for source in item["sources"])
    assert all(urlsplit(url).hostname in {"www.twse.com.tw", "www.tpex.org.tw"} for url in calls)


def test_fetch_failure_is_exchange_local_and_specific():
    def fetch(url):
        source = next(source for source, spec in SOURCES.items() if url.split("?")[0] == spec["url"])
        if source == "twse_capital_reduction":
            raise TimeoutError("timed out")
        return report(source)

    result = fetch_tw_price_action_coverage({"2330.TW", "6488.TWO"}, START, END,
                                           now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert result["2330.TW"]["status"] == "unsupported"
    assert any("TimeoutError" in reason for reason in result["2330.TW"]["reasons"])
    assert result["6488.TWO"]["status"] == "clear"


def test_bad_window_and_non_tw_symbol_never_fetch():
    calls = []
    fetch = lambda url: calls.append(url)
    result = fetch_tw_price_action_coverage({"2330.TW"}, "2026-01-01", END,
                                           now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert result["2330.TW"]["status"] == "unsupported"
    result = fetch_tw_price_action_coverage({"AAPL"}, START, END,
                                           now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert result["AAPL"]["status"] == "unsupported"
    assert calls == []


def test_no_match_fetch_contract_supported_but_announcement_snapshot_not_supported():
    def fetch(url):
        source = next(source for source, spec in SOURCES.items() if url.split("?")[0] == spec["url"])
        return {"stat": NO_MATCH} if source in {"twse_ex_right_dividend", "twse_capital_reduction"} else report(source)

    result = fetch_tw_price_action_coverage({"2330.TW"}, START, END,
                                           now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert result["2330.TW"]["status"] == "clear"
    result = fetch_tw_price_action_coverage({"2330.TW"}, START, END,
                                           now=datetime.fromisoformat(NOW), fetch_json=lambda url: [])
    assert result["2330.TW"]["status"] == "unsupported"


def test_plan_batch_fetches_once_and_preserves_each_original_price_basis():
    calls = []

    def fetch(url):
        calls.append(url)
        source = next(source for source, spec in SOURCES.items() if url.split("?")[0] == spec["url"])
        return report(source, [["115/10/05", "2330", "60"]] if source == "twse_ex_right_dividend" else None)

    requests = [
        {"plan_id": "old", "symbol": "2330.TW", "original_session": START, "through_session": END},
        {"plan_id": "new", "symbol": "2330.TW", "original_session": "2026-10-06", "through_session": END},
        {"plan_id": "tpex", "symbol": "6488.TWO", "original_session": "2026-10-06", "through_session": END},
    ]
    result = fetch_tw_price_action_coverage_for_plans(requests, now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert len(calls) == 9
    assert result["old"]["status"] == "blocked"
    assert result["new"]["status"] == "clear"
    assert result["tpex"]["status"] == "clear"
    assert result["new"]["coverage_from"] == START
    assert result["new"]["plan_original_session"] == "2026-10-06"
    assert result["new"]["events"] == []


def test_plan_batch_cannot_clear_partial_coverage_when_older_event_removed():
    def fetch(url):
        source = next(source for source, spec in SOURCES.items() if url.split("?")[0] == spec["url"])
        if source == "twse_par_value_change":
            raise TimeoutError("unavailable")
        return report(source, [["115/10/05", "2330", "60"]] if source == "twse_ex_right_dividend" else None)

    requests = [{"plan_id": label, "symbol": "2330.TW", "original_session": original, "through_session": END}
                for label, original in [("old", START), ("new", "2026-10-06")]]
    result = fetch_tw_price_action_coverage_for_plans(requests, now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert result["old"]["status"] == "blocked"
    assert result["new"]["status"] == "unsupported"


def test_stale_or_duplicate_plans_do_not_widen_other_batch():
    calls = []

    def fetch(url):
        calls.append(url)
        source = next(source for source, spec in SOURCES.items() if url.split("?")[0] == spec["url"])
        return report(source)

    requests = [
        {"plan_id": "old", "symbol": "2330.TW", "original_session": "2025-10-01", "through_session": END},
        {"plan_id": "ok", "symbol": "2330.TW", "original_session": START, "through_session": END},
        {"plan_id": "dup", "symbol": "2330.TW", "original_session": START, "through_session": END},
        {"plan_id": "dup", "symbol": "2330.TW", "original_session": START, "through_session": END},
    ]
    result = fetch_tw_price_action_coverage_for_plans(requests, now=datetime.fromisoformat(NOW), fetch_json=fetch)
    assert len(calls) == 4
    assert result["old"]["status"] == result["dup"]["status"] == "unsupported"
    assert result["ok"]["status"] == "clear"
