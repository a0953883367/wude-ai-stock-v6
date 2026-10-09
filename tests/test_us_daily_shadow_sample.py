from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

import live_api
from market_calendar import OfficialMarketCalendar
from us_daily_shadow_sample import build_daily_shadow_samples, build_us_daily_shadow_sample
from us_market_data import fetch_us_sip_snapshots, normalize_sip_snapshot


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def bar(timestamp, close=110):
    return {"t": timestamp, "o": 100, "h": 115, "l": 95, "c": close, "v": 12000}


def calendar(tmp_path, days=None):
    days = days or {"2026-10-08": "16:00", "2026-10-09": "16:00"}
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"version": 1, "markets": {"US": {"years": {"2026": {
        "year": 2026, "status": "verified_alpaca", "sources": ["Alpaca Market Calendar"],
        "sessions": sorted(days), "session_details": {
            day: {"open": "09:30", "close": close} for day, close in days.items()},
    }}}}}))
    return OfficialMarketCalendar(path, auto_refresh=False, allow_network=False,
        clock=lambda: at("2026-10-09T16:00:00Z").timestamp())


def snapshot():
    return {"dailyBar": bar("2026-10-09T04:00:00Z"),
            "prevDailyBar": bar("2026-10-08T04:00:00Z", 108),
            "latestTrade": {"p": 111}, "secret_or_extra": "never retained"}


def test_unclosed_daily_falls_back_to_previous_closed_sample(tmp_path):
    result = build_us_daily_shadow_sample("AAPL", snapshot(), feed="sip", calendar=calendar(tmp_path),
        observed_at=at("2026-10-09T16:00:00Z"))
    assert result["selected"]["provider_field"] == "prevDailyBar"
    assert result["selected"]["raw_bar"]["c"] == 108
    assert result["candidates"][0]["status"] == "source_session_not_closed"
    assert result["history_ready"] is result["decision_eligible"] is result["affects_formal"] is False
    assert result["bar_finality_verified"] is False
    assert result["private_only"] is True
    assert "secret_or_extra" not in json.dumps(result)


def test_after_close_selects_daily_but_never_asserts_finality(tmp_path):
    result = build_us_daily_shadow_sample("AAPL", snapshot(), feed="sip", calendar=calendar(tmp_path),
        observed_at=at("2026-10-09T20:01:00Z"))
    assert result["selected"]["provider_field"] == "dailyBar"
    assert result["selected"]["session_closed"] is True
    assert result["bar_finality_verified"] is False


def test_dst_bar_dates_and_close_offsets_are_provider_dates(tmp_path):
    cal = calendar(tmp_path, {"2026-03-06": "16:00", "2026-03-09": "16:00"})
    result = build_us_daily_shadow_sample("AAPL", {
        "dailyBar": bar("2026-03-09T04:00:00Z"), "prevDailyBar": bar("2026-03-06T05:00:00Z")},
        feed="sip", calendar=cal, observed_at=at("2026-03-09T20:01:00Z"))
    assert result["selected"]["session_date"] == "2026-03-09"
    assert result["candidates"][0]["official_close_at"].endswith("16:00:00-04:00")
    assert result["candidates"][1]["official_close_at"].endswith("16:00:00-05:00")


def test_early_close_uses_verified_calendar_not_hardcoded_hour(tmp_path):
    result = build_us_daily_shadow_sample("AAPL", {"dailyBar": bar("2026-11-27T05:00:00Z")},
        feed="sip", calendar=calendar(tmp_path, {"2026-11-27": "13:00"}),
        observed_at=at("2026-11-27T18:01:00Z"))
    assert result["selected"]["official_close_at"] == "2026-11-27T13:00:00-05:00"


def test_missing_calendar_preserves_raw_bar_without_source_upgrade(tmp_path):
    cal = OfficialMarketCalendar(tmp_path / "missing.json", auto_refresh=False, allow_network=True)
    cal.refresh_async = lambda *a: pytest.fail("Private sample must not refresh the calendar")
    result = build_us_daily_shadow_sample("AAPL", snapshot(), feed="sip", calendar=cal,
        observed_at=at("2026-10-09T21:00:00Z"))
    assert result["selected"] is None
    assert result["candidates"][0]["ohlcv_complete"] is True
    assert result["candidates"][0]["status"] == "official_calendar_unavailable"


@pytest.mark.parametrize("key,value", [("o", None), ("h", 90), ("l", float("nan")),
    ("v", None), ("v", 0), ("v", True), ("c", float("inf")),
    ("t", "2026-10-08"), ("t", "2026-10-08T15:00:00Z")])
def test_incomplete_or_synthetic_candle_never_gets_filled(tmp_path, key, value):
    source = bar("2026-10-08T04:00:00Z")
    if value is None:
        source.pop(key)
    else:
        source[key] = value
    result = build_us_daily_shadow_sample("AAPL", {"dailyBar": source}, feed="sip",
        calendar=calendar(tmp_path), observed_at=at("2026-10-09T16:00:00Z"))
    assert result["selected"] is None
    if value is None:
        assert key not in result["candidates"][0]["raw_bar"]


def test_iex_is_not_relabelled_sip(tmp_path):
    result = build_us_daily_shadow_sample("AAPL", snapshot(), feed="iex", calendar=calendar(tmp_path),
        observed_at=at("2026-10-09T21:00:00Z"))
    assert result["source"] == "Alpaca IEX"
    assert result["selected"] is None
    assert result["candidates"][0]["status"] == "unapproved_feed"


def test_conflicting_provider_aliases_are_rejected(tmp_path):
    result = build_us_daily_shadow_sample("AAPL", {"prevDailyBar": bar("2026-10-08T04:00:00Z"),
        "previousDailyBar": bar("2026-10-08T04:00:00Z", 109)}, feed="sip",
        calendar=calendar(tmp_path), observed_at=at("2026-10-09T16:00:00Z"))
    assert result["status"] == "conflicting_daily_bars"
    assert result["selected"] is None


def test_private_report_keeps_original_observation_time(tmp_path):
    initial = build_us_daily_shadow_sample("AAPL", snapshot(), feed="sip",
        observed_at=at("2026-10-09T16:00:00Z"))
    initial["selected"] = {"invented": "do not trust relay flags"}
    initial["decision_eligible"] = True
    result = build_daily_shadow_samples({"AAPL": initial}, calendar(tmp_path), at("2026-10-09T21:00:00Z"))
    assert result["samples"]["AAPL"]["selected"]["session_date"] == "2026-10-08"
    assert result["samples"]["AAPL"]["decision_eligible"] is False
    assert result["closed_sample_count"] == 1
    assert result["history_ready"] is False


def test_future_observation_and_unverified_source_are_not_preserved(tmp_path):
    initial = build_us_daily_shadow_sample("AAPL", snapshot(), feed="sip",
        observed_at=at("2026-10-09T21:00:00Z"))
    result = build_daily_shadow_samples({"AAPL": initial}, calendar(tmp_path), at("2026-10-09T16:00:00Z"))
    assert result["sample_count"] == 0
    initial["endpoint"] = "https://unapproved.example"
    assert build_daily_shadow_samples({"AAPL": initial}, calendar(tmp_path), at("2026-10-09T22:00:00Z"))["sample_count"] == 0


class Response:
    def __init__(self, data): self.data = data
    def json(self): return self.data
    def raise_for_status(self): pass


def test_same_snapshot_request_collects_private_bars_without_changing_formal_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_STOCK_FEED", "sip")
    requests = []
    def get(*args, **kwargs):
        requests.append((args, kwargs))
        return Response({"AAPL": snapshot()})
    private = {}
    result = fetch_us_sip_snapshots(["AAPL"], session=SimpleNamespace(get=get), daily_shadow_samples=private,
        calendar=calendar(tmp_path), observed_at=at("2026-10-09T16:00:00Z"))
    assert len(requests) == 1
    assert set(result["AAPL"]) == set(normalize_sip_snapshot(snapshot()))
    assert all(key.startswith("us_live_") for key in result["AAPL"])
    assert private["AAPL"]["selected"]["session_date"] == "2026-10-08"
    assert "raw_bar" not in json.dumps({"all_analysis": result})


def test_relay_sidecar_is_opt_in_separate_and_symbol_bounded(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    monkeypatch.setenv("WUDE_LIVE_API_BASE", "https://live.example")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://identity.example")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "test-runtime")
    monkeypatch.setattr("us_market_data.requests.get", lambda *a, **kw: Response({"value": "test-identity"}))
    posted = []
    formal = {"AAPL": {"us_live_price": 110}}
    def post(*a, **kw):
        posted.append(kw["json"])
        return Response({"data": formal, "private_daily_shadow_samples": {
            "AAPL": {"private_only": True}, "UNREQUESTED": {"raw_bar": "discard"}}})
    monkeypatch.setattr("us_market_data.requests.post", post)
    collector = {}
    assert fetch_us_sip_snapshots(["AAPL"], daily_shadow_samples=collector) == formal
    assert posted[-1]["include_daily_shadow"] is True
    assert collector == {"AAPL": {"private_only": True}}
    assert fetch_us_sip_snapshots(["AAPL"]) == formal
    assert "include_daily_shadow" not in posted[-1]


def test_internal_route_sidecar_does_not_leak_into_data_or_default_requests(tmp_path, monkeypatch):
    responses = []
    cal = calendar(tmp_path)
    def fetch(symbols, *, daily_shadow_samples=None, calendar=None):
        if daily_shadow_samples is not None:
            daily_shadow_samples["AAPL"] = build_us_daily_shadow_sample("AAPL", snapshot(), feed="sip",
                calendar=calendar, observed_at=at("2026-10-09T16:00:00Z"))
        return {"AAPL": {"us_live_price": 110}}
    monkeypatch.setattr(live_api, "fetch_us_sip_snapshots", fetch)
    monkeypatch.setattr(live_api, "verify_github_oidc_token", lambda *a: {})
    payload = {"kind": "sip", "symbols": ["AAPL"], "include_daily_shadow": True}
    handler = SimpleNamespace(path="/api/internal/market-data", headers={"Authorization": "Bearer test"},
        rate_limiter=SimpleNamespace(allow=lambda: True), _read_json=lambda: payload,
        _send=lambda status, result: responses.append((status, result)),
        large_buy_service=SimpleNamespace(weight_shadow=SimpleNamespace(calendar=cal)))
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 200
    assert "raw_bar" not in json.dumps(responses[-1][1]["data"])
    assert responses[-1][1]["private_daily_shadow_samples"]["AAPL"]["selected"]
    payload.pop("include_daily_shadow")
    live_api.LiveRequestHandler.do_POST(handler)
    assert "private_daily_shadow_samples" not in responses[-1][1]
    def deny(*args): raise PermissionError("test denied")
    monkeypatch.setattr(live_api, "verify_github_oidc_token", deny)
    handler._read_json = lambda: pytest.fail("Denied request must not reach payload/source")
    live_api.LiveRequestHandler.do_POST(handler)
    assert responses[-1][0] == 403
