from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import fubon_daily_history as f
from tools.probe_fubon_daily_history import sanitize_status, run_probe

NOW = datetime(2026, 10, 10, 9, tzinfo=f.TAIPEI)
SESSIONS = [(NOW.date() - timedelta(days=i)).isoformat() for i in range(100, 0, -1)
            if (NOW.date() - timedelta(days=i)).weekday() < 5]

class Calendar:
    def lookup(self, *args):
        return {"available": True, "status": "verified", "source_statuses": ["verified_twse_tpex"], "sessions": SESSIONS}


def payload(symbol="2330.TW"):
    exchange, market, _ = f.PILOT[symbol]
    return {"symbol": symbol.split(".")[0], "type": "EQUITY", "exchange": exchange, "market": market, "timeframe": "D",
            "data": [{"date": d, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 1234} for d in SESSIONS]}


def rest(method):
    return SimpleNamespace(historical=SimpleNamespace(candles=method))


def sdk(method):
    return SimpleNamespace(marketdata=SimpleNamespace(rest_client=SimpleNamespace(stock=rest(method))))


def test_fixed_contract_both_venues_private_rows_status_safe():
    calls = []
    def candles(**kwargs):
        calls.append(kwargs)
        return payload("2330.TW" if kwargs["symbol"] == "2330" else "6290.TWO")
    status, private = f.collect_private(rest(candles), list(f.PILOT), Calendar(), now=NOW)
    assert status["validated_count"] == 2
    assert sanitize_status(status)["status"] == "validated_in_memory"
    assert all(c["adjusted"] == "false" and c["timeframe"] == "D" for c in calls)
    assert set(calls[0]) == {"symbol", "from", "to", "timeframe", "adjusted", "fields", "sort"}
    assert private["6290.TWO"]["volume_unit"] == "shares"
    assert private["6290.TWO"]["bars"][0]["volume"] == 1234
    assert private["6290.TWO"]["adjustment_echo_verified"] is False
    assert "bars" not in json.dumps(status) and "close" not in json.dumps(status)


@pytest.mark.parametrize("symbols", [[], ["3293.TWO"], ["2330.TW"] * 2, list(f.PILOT) * 2, "2330.TW", ["../../x"]])
def test_scope_rejects_before_call(symbols):
    with pytest.raises(ValueError):
        f.collect_private(rest(lambda **k: pytest.fail("called")), symbols, Calendar(), now=NOW)


@pytest.mark.parametrize("mutate,reason", [
    (lambda p: p.update(symbol="9999"), "invalid_provider_identity"),
    (lambda p: p.update(market="ESB"), "invalid_provider_identity"),
    (lambda p: p.update(timeframe="1"), "invalid_provider_identity"),
    (lambda p: p.update(adjusted=True), "adjustment_conflict"),
    (lambda p: p["data"].append(p["data"][0]), "duplicate_provider_session"),
    (lambda p: p["data"].pop(), "incomplete_session_coverage"),
    (lambda p: p["data"][0].update(date="2026-10-11"), "invalid_bar_session"),
    (lambda p: p["data"][0].update(close=float("nan")), "invalid_ohlcv"),
    (lambda p: p["data"][0].update(volume=-1), "invalid_ohlcv"),
    (lambda p: p["data"][0].update(volume=1.5), "invalid_ohlcv"),
    (lambda p: p["data"][0].update(volume=True), "invalid_ohlcv"),
    (lambda p: p["data"][0].update(high=8), "invalid_ohlcv"),
])
def test_fail_closed_payload(mutate, reason):
    p = payload(); mutate(p)
    with pytest.raises(f.HistoryBlocked, match=reason):
        f.normalize(p, "2330.TW", SESSIONS, NOW)


@pytest.mark.parametrize("code,reason", [(401,"provider_authentication_denied"), (403,"provider_entitlement_denied"), (429,"provider_rate_limited"), (500,"provider_failure")])
def test_errors_stop_batch_no_raw_error(code, reason):
    count = []
    def fail(**kwargs):
        count.append(1)
        error = RuntimeError("secret-provider-response")
        error.status_code = code
        raise error
    status, private = f.collect_private(rest(fail), list(f.PILOT), Calendar(), now=NOW)
    assert status["reason"] == reason and len(count) == 1 and private == {}
    assert "secret" not in json.dumps(status)


def test_calendar_missing_or_one_venue_never_guessed():
    calendar = SimpleNamespace(lookup=lambda *args: {"available":True, "status":"verified", "source_statuses":["verified_twse_only"], "sessions":SESSIONS})
    status, _ = f.collect_private(rest(lambda **k: pytest.fail("called")), list(f.PILOT), calendar, now=NOW)
    assert status["reason"] == "official_calendar_unverified"


def test_publication_cutoff_uses_exact_calendar():
    requests = []
    calendar = SimpleNamespace(lookup=lambda *args: requests.append(args) or {"available":True,"status":"verified","source_statuses":["verified_twse_tpex"],"sessions":SESSIONS})
    f._sessions(calendar, NOW.replace(hour=16, minute=29))
    assert requests[-1][-1] == "2026-10-09"
    f._sessions(calendar, NOW.replace(hour=16, minute=30))
    assert requests[-1][-1] == "2026-10-10"


def test_deadline_no_overlap_no_second_symbol(monkeypatch):
    started, release = threading.Event(), threading.Event()
    count = []
    def slow(**kwargs):
        count.append(1); started.set(); release.wait(2)
        return payload()
    monkeypatch.setattr(f, "_sessions", lambda *a: SESSIONS)
    pilot = f.DailyPilot()
    first = pilot.status(sdk(slow), list(f.PILOT), Calendar(), timeout=.01)
    assert started.is_set() and first["reason"] == "request_time_budget"
    assert first["request_count"] is None and first["request_count_known"] is False
    assert sanitize_status(first)["request_count"] is None
    second = pilot.status(sdk(slow), list(f.PILOT), Calendar(), timeout=.01)
    assert second["reason"] == "request_in_progress"
    release.set()
    assert count == [1]


def test_no_session_no_login():
    status = f.DailyPilot().status(None, list(f.PILOT), Calendar())
    assert status["reason"] == "existing_session_unavailable"


def test_untrusted_metadata_is_stripped_and_invalid_count_blocked():
    status, _ = f.collect_private(rest(lambda **k: payload("2330.TW" if k["symbol"]=="2330" else "6290.TWO")), list(f.PILOT), Calendar(), now=NOW)
    status["raw"] = "secret"
    status["symbols"]["2330.TW"]["bars"] = payload()["data"]
    clean = sanitize_status(status)
    assert "secret" not in json.dumps(clean) and "bars" not in json.dumps(clean)
    status["request_count"] = 3
    assert sanitize_status(status)["reason"] == "invalid_or_unavailable_relay_status"


def test_existing_auth_and_workflow_bounds():
    root = Path(__file__).resolve().parents[1]
    auth = (root / "github_oidc_auth.py").read_text()
    assert 'DEFAULT_WORKFLOW = ".github/workflows/stock-briefing.yml"' in auth
    assert 'claims.get("ref") != "refs/heads/main"' in auth
    api = (root / "live_api.py").read_text()
    route = api[api.index('if parsed.path == "/api/internal/market-data":'):]
    assert route.index("verify_github_oidc_token(token)") < route.index('elif kind == "tw_daily_history_status"')
    method = api[api.index("    def daily_history_status"):api.index("    def ownership")]
    assert "login" not in method.split('"""')[-1] and "configure_fubon_certificate" not in method
    workflow = (root / ".github/workflows/stock-briefing.yml").read_text()
    assert workflow.index("Probe existing Fubon daily history") < workflow.index("Keep reports and recent archive")
    assert workflow.index("Probe existing Fubon daily history") < workflow.index("Refresh read-only Fubon ownership")


def test_later_provider_failure_clears_partial_success_metadata():
    def candles(**kwargs):
        if kwargs["symbol"] == "2330":
            return payload()
        err = RuntimeError("secret")
        err.status_code = 403
        raise err
    status, private = f.collect_private(rest(candles), list(f.PILOT), Calendar(), now=NOW)
    assert status["reason"] == "provider_entitlement_denied"
    assert status["symbols"] == {} and status["validated_count"] == 0 and private == {}


def test_observed_time_is_after_response_not_request_start():
    observed = NOW + timedelta(seconds=3)
    status, private = f.collect_private(rest(lambda **k: payload()), ["2330.TW"], Calendar(),
                                        now=NOW, observed_clock=lambda: observed)
    assert status["requested_at"] == NOW.isoformat()
    assert status["observed_at"] == observed.isoformat()
    assert private["2330.TW"]["requested_at"] == NOW.isoformat()
    assert private["2330.TW"]["observed_at"] == observed.isoformat()


def test_source_lifecycle_precedes_all_report_commit_paths():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/stock-briefing.yml").read_text()
    probe = workflow.index("      - name: Probe existing Fubon daily history for two pilot symbols")
    first_push = workflow.index("git push origin HEAD:main")
    shadow_push = workflow.index("python tools/publish_shadow_report_batch.py")
    full_ownership = workflow.index("      - name: Refresh read-only Fubon ownership supplement")
    assert workflow.index("      - name: Generate report") < probe < first_push < shadow_push < full_ownership
    assert "git push" not in workflow[:probe] and "publish_shadow_report_batch.py" not in workflow[:probe]
    assert workflow.count("run: python fubon_ownership_relay.py") == 1
    # Source-only and ordinary briefing jobs are mutually exclusive.
    assert workflow.split("  briefing:\n", 1)[1].count("run: python tools/probe_fubon_daily_history.py") == 1
    source_step = workflow[probe:workflow.index("      - name: Keep reports and recent archive")]
    assert "continue-on-error: true" in source_step and "timeout-minutes: 2" in source_step
    assert "steps.generate.outcome == 'success'" in source_step
    assert "workflow_dispatch" not in source_step and "id-token" not in source_step


@pytest.mark.parametrize("warm", [{}, None, {"2330.TW": {}},
    {s: {"institutional_trades": {"status":"rate_limited"}} for s in f.PILOT},
    {s: {"institutional_trades": {"status":"fetch_error", "error_code":403}} for s in f.PILOT},
    {s: {k: {} for k in ("institutional_trades", "tdcc_distribution", "director_holdings")} for s in f.PILOT},
    {s: {k: {"status":"invented"} for k in ("institutional_trades", "tdcc_distribution", "director_holdings")} for s in f.PILOT}])
def test_failed_or_timed_out_warmup_stops_without_retry(warm):
    calls = []
    def relay(kind, payload, timeout):
        calls.append((kind, payload, timeout))
        return f.empty_status(list(f.PILOT), NOW, "existing_session_unavailable") if kind == "tw_daily_history_status" else warm
    result = run_probe(relay)
    assert result["reason"] == "existing_session_warmup_unavailable"
    assert calls == [("tw_daily_history_status", {"symbols": list(f.PILOT)}, 30), ("ownership", {"symbols": list(f.PILOT)}, 20)]


def test_one_fixed_warmup_then_probe_no_raw_output():
    calls = []
    status, _ = f.collect_private(rest(lambda **k: payload("2330.TW" if k["symbol"] == "2330" else "6290.TWO")), list(f.PILOT), Calendar(), now=NOW)
    def relay(kind, data, timeout):
        calls.append(kind)
        assert data == {"symbols": list(f.PILOT)}
        if len(calls) == 1:
            return f.empty_status(list(f.PILOT), NOW, "existing_session_unavailable")
        if kind == "ownership":
            return {s: {k: {"status":"available", "rows":[{"private_provider_row":"never log"}]} for k in ("institutional_trades", "tdcc_distribution", "director_holdings")} for s in f.PILOT}
        return status
    result = run_probe(relay)
    assert calls == ["tw_daily_history_status", "ownership", "tw_daily_history_status"]
    assert result["status"] == "validated_in_memory" and "never log" not in json.dumps(result)


def test_missing_boundary_diagnostics_without_market_values():
    def candles(**kwargs):
        p = payload("2330.TW" if kwargs["symbol"] == "2330" else "6290.TWO")
        p["data"] = p["data"][1:]
        return p
    status, private = f.collect_private(rest(candles), list(f.PILOT), Calendar(), now=NOW)
    assert status["status"] == "blocked" and private == {}
    clean = sanitize_status(status)
    assert clean["request_from"] == SESSIONS[0] and clean["request_to"] == SESSIONS[-1]
    assert clean["request_timeframe"] == "D" and clean["request_adjusted"] == "false"
    for row in clean["symbols"].values():
        assert row["reason"] == "incomplete_session_coverage" and row["bar_count"] == 0
        assert row["observed_date_count"] == len(SESSIONS) - 1
        assert row["expected_session_count"] == len(SESSIONS)
        assert row["missing_session_dates"] == SESSIONS[:1]
        assert row["unexpected_session_dates"] == []
        assert row["first_returned_date"] == SESSIONS[1] and row["last_returned_date"] == SESSIONS[-1]
        assert not {"open", "high", "low", "close", "volume", "bars", "rows"}.intersection(row)


def test_unexpected_date_is_reported_but_remains_blocked():
    p = payload()
    p["data"][0]["date"] = "2026-10-11"
    with pytest.raises(f.HistoryBlocked) as result:
        f.normalize(p, "2330.TW", SESSIONS, NOW)
    assert result.value.reason == "invalid_bar_session"
    assert result.value.diagnostics["unexpected_session_dates"] == ["2026-10-11"]
    assert result.value.diagnostics["missing_session_dates"] == SESSIONS[:1]


def test_bad_date_metadata_or_values_cannot_escape_sanitizer():
    status, _ = f.collect_private(rest(lambda **k: payload("2330.TW" if k["symbol"] == "2330" else "6290.TWO")), list(f.PILOT), Calendar(), now=NOW)
    status["symbols"]["2330.TW"]["missing_session_dates"] = ["secret-provider-message"]
    assert sanitize_status(status)["reason"] == "invalid_or_unavailable_relay_status"
    status["symbols"]["2330.TW"]["missing_session_dates"] = [SESSIONS[0]] * 121
    assert sanitize_status(status)["reason"] == "invalid_or_unavailable_relay_status"


def test_date_diagnostic_excludes_invalid_provider_strings():
    p = payload(); p["data"][0]["date"] = "secret-provider-message"
    with pytest.raises(f.HistoryBlocked) as result:
        f.normalize(p, "2330.TW", SESSIONS, NOW)
    assert "secret" not in json.dumps(result.value.diagnostics)


def test_query_calendar_inclusivity_and_utc_cutoff():
    from datetime import timezone
    requests = []
    calendar = SimpleNamespace(lookup=lambda *args: requests.append(args) or {"available":True,"status":"verified","source_statuses":["verified_twse_tpex"],"sessions":SESSIONS})
    f._sessions(calendar, datetime(2026, 10, 10, 8, 29, tzinfo=timezone.utc))
    assert requests[-1] == ("TW", "2026-06-11", "2026-10-09")
    f._sessions(calendar, datetime(2026, 10, 10, 8, 30, tzinfo=timezone.utc))
    assert requests[-1] == ("TW", "2026-06-12", "2026-10-10")


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(expected_session_count=1),
    lambda r: r.update(observed_date_count=0, expected_session_count=0, first_returned_date=SESSIONS[0], last_returned_date=None),
    lambda r: r.update(missing_session_dates=[SESSIONS[0]], unexpected_session_dates=[SESSIONS[0]]),
    lambda r: r.update(first_returned_date=SESSIONS[1]),
    lambda r: r.update(bar_count=len(SESSIONS)-1),
])
def test_contradictory_diagnostic_metadata_blocks(mutate):
    status, _ = f.collect_private(rest(lambda **k: payload("2330.TW" if k["symbol"] == "2330" else "6290.TWO")), list(f.PILOT), Calendar(), now=NOW)
    mutate(status["symbols"]["2330.TW"])
    assert sanitize_status(status)["reason"] == "invalid_or_unavailable_relay_status"


def test_request_end_must_match_official_session():
    status, _ = f.collect_private(rest(lambda **k: payload("2330.TW" if k["symbol"] == "2330" else "6290.TWO")), list(f.PILOT), Calendar(), now=NOW)
    status["request_to"] = "2026-10-10"
    assert sanitize_status(status)["reason"] == "invalid_or_unavailable_relay_status"


def test_ready_session_direct_probe_skips_ownership():
    calls = []
    status, _ = f.collect_private(rest(lambda **k: payload("2330.TW" if k["symbol"] == "2330" else "6290.TWO")), list(f.PILOT), Calendar(), now=NOW)
    def relay(kind, data, timeout):
        calls.append(kind)
        return status
    assert run_probe(relay)["validated_count"] == 2
    assert calls == ["tw_daily_history_status"]


@pytest.mark.parametrize("reason", sorted(f.REASONS - {"existing_session_unavailable"}))
def test_direct_failure_never_warms_or_retries(reason):
    calls = []
    status = f.empty_status(list(f.PILOT), NOW, reason)
    def relay(kind, data, timeout):
        calls.append(kind)
        return status
    run_probe(relay)
    assert calls == ["tw_daily_history_status"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(request_count=1),
    lambda r: r.update(request_count_known=False, request_count=None),
    lambda r: r.update(validated_count=1),
    lambda r: r.update(session_date="2026-10-08"),
    lambda r: r.update(raw="untrusted"),
    lambda r: r.pop("observed_at"),
    lambda r: r.clear(),
])
def test_inconsistent_session_failure_cannot_authorize_warmup(mutate):
    calls = []
    status = f.empty_status(list(f.PILOT), NOW, "existing_session_unavailable")
    mutate(status)
    def relay(kind, data, timeout):
        calls.append(kind)
        return status
    run_probe(relay)
    assert calls == ["tw_daily_history_status"]


def test_absent_sdk_is_metadata_only_before_calendar_or_upstream():
    calendar = SimpleNamespace(lookup=lambda *a: pytest.fail("calendar called without session"))
    result = f.DailyPilot().status(None, list(f.PILOT), calendar)
    assert result["request_count"] == 0 and result["request_count_known"] is True
    assert result["symbols"] == {} and result["validated_count"] == 0


@pytest.mark.parametrize('reason', ['provider_authentication_denied', 'provider_entitlement_denied', 'request_time_budget', 'incomplete_session_coverage', 'existing_session_unavailable'])
def test_post_warmup_failure_never_repeats(reason):
    calls = []
    def relay(kind, data, timeout):
        calls.append(kind)
        if len(calls) == 1:
            return f.empty_status(list(f.PILOT), NOW, 'existing_session_unavailable')
        if kind == 'ownership':
            return {s: {k: {'status':'available'} for k in ('institutional_trades','tdcc_distribution','director_holdings')} for s in f.PILOT}
        return f.empty_status(list(f.PILOT), NOW, reason)
    assert run_probe(relay)['reason'] == reason
    assert calls == ['tw_daily_history_status', 'ownership', 'tw_daily_history_status']
