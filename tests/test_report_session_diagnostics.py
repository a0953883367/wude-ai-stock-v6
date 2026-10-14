import copy
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from briefing import _report_session_issues, _carry_forward_symbol_session_regressions
from report_session_diagnostics import build_session_diagnostic, save_session_diagnostic_safely


def inputs():
    universe = [{"symbol": f"{i}.TW", "market": "TW", "type": "個股"} for i in range(10)]
    before = [{**item, "official_session_date": "2026-10-08" if i < 8 else "2026-10-12",
               "close": 987654321, "access_token": "SECRET_SENTINEL"}
              for i, item in enumerate(universe)]
    previous = [{**item, "official_session_date": "2026-10-12"} for item in universe]
    history = {item["symbol"]: pd.DataFrame({"close": [987654321]},
                index=pd.to_datetime([before[i]["official_session_date"]]))
               for i, item in enumerate(universe)}
    return dict(universe=universe, history=history,
                official_prices={str(i): {"date": "2026-10-08", "close": 987654321,
                                          "source_url": "https://secret/token"} for i in range(10)},
                before_carry=before, after_carry=copy.deepcopy(before),
                previous_payload={"data": previous}, period="evening",
                checked_at=datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc))


def test_mixed_regression_evidence_is_date_only_and_nonmutating():
    args = inputs()
    saved_before = copy.deepcopy(args["before_carry"])
    issues = _report_session_issues(args["after_carry"], args["previous_payload"])
    report = build_session_diagnostic(**args)
    cohort = report["cohorts"]["TW_STOCK"]
    assert cohort["downloaded_daily"]["session_counts"] == {"2026-10-08": 8, "2026-10-12": 2}
    assert cohort["features_after_carry"]["dominant_coverage_pct"] == 80
    assert cohort["regressed_count_after_carry"] == 8
    assert report["official_daily"]["TWSE"]["dated_count"] == 10
    text = json.dumps(report)
    for forbidden in ("987654321", "SECRET_SENTINEL", "https://", "0.TW", "close", "access_token"):
        assert forbidden not in text
    assert args["before_carry"] == saved_before
    assert _report_session_issues(args["after_carry"], args["previous_payload"]) == issues
    assert len(issues) == 3


def test_carried_individual_keeps_before_and_after_separate():
    args = inputs()
    for row in args["before_carry"][1:]:
        row["official_session_date"] = "2026-10-12"
    args["after_carry"], carried = _carry_forward_symbol_session_regressions(
        args["before_carry"], args["previous_payload"])
    assert carried == ["0.TW"]
    report = build_session_diagnostic(**args)["cohorts"]["TW_STOCK"]
    assert report["features_before_carry"]["session_counts"]["2026-10-08"] == 1
    assert report["features_after_carry"]["session_counts"] == {"2026-10-12": 10}
    assert report["regressed_count_after_carry"] == 0


def test_invalid_dates_never_echo_arbitrary_provider_data():
    args = inputs()
    for row in args["before_carry"]:
        row["official_session_date"] = "SECRET_SENTINEL"
    args["official_prices"]["0"]["date"] = "https://secret/token"
    result = build_session_diagnostic(**args)
    assert "SECRET_SENTINEL" not in json.dumps(result)
    assert "https://" not in json.dumps(result)
    assert result["cohorts"]["TW_STOCK"]["features_before_carry"]["missing_or_invalid_count"] == 10


def test_history_uses_market_timezone_and_last_nonnull_close():
    args = inputs()
    args["universe"] = [{"symbol": "US_TEST", "market": "US", "type": "ETF"}]
    args["history"] = {"US_TEST": pd.DataFrame({"close": [1, None]}, index=pd.to_datetime(
        ["2026-10-13T00:00:00Z", "2026-10-14T00:00:00Z"]))}
    result = build_session_diagnostic(**args)
    assert result["cohorts"]["US_ETF"]["downloaded_daily"]["session_counts"] == {"2026-10-12": 1}
    assert result["cohorts"]["US_ETF"]["features_after_carry"]["missing_or_invalid_count"] == 1


def test_atomic_diagnostic_cannot_modify_formal_files(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    for name in ("all_analysis.json", "rankings.json", "latest.json"):
        (reports / name).write_bytes(b"formal-valid-sentinel")
    target = tmp_path / ".run_diagnostics"
    assert save_session_diagnostic_safely(target, **inputs())
    result = json.loads((target / "report_session_failure.json").read_text())
    assert result["formal_state_updated"] is False
    assert result["guard_relaxed"] is False
    assert not list(target.glob("*.tmp"))
    assert all(p.read_bytes() == b"formal-valid-sentinel" for p in reports.iterdir())


def test_failed_atomic_replace_preserves_previous_diagnostic_and_guard(tmp_path, monkeypatch):
    target = tmp_path / ".run_diagnostics"
    target.mkdir()
    old = target / "report_session_failure.json"
    old.write_bytes(b"last diagnostic")
    def fail(*args, **kwargs):
        raise OSError("SECRET_SENTINEL")
    monkeypatch.setattr(Path, "replace", fail)
    assert not save_session_diagnostic_safely(target, **inputs())
    assert old.read_bytes() == b"last diagnostic"
    assert len(list(target.iterdir())) == 1
    args = inputs()
    assert len(_report_session_issues(args["after_carry"], args["previous_payload"])) == 3


def test_projection_failure_does_not_escape_or_leak(tmp_path, monkeypatch, caplog):
    import report_session_diagnostics
    def fail(**kwargs):
        raise ValueError("SECRET_SENTINEL")
    monkeypatch.setattr(report_session_diagnostics, "build_session_diagnostic", fail)
    assert not save_session_diagnostic_safely(tmp_path, **inputs())
    assert "SECRET_SENTINEL" not in caplog.text
    assert not list(tmp_path.iterdir())


def test_workflow_preserves_only_exact_date_diagnostic_on_failure():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/stock-briefing.yml").read_text()
    block = workflow.split("- name: Preserve date-only report guard diagnostic", 1)[1].split("- name:", 1)[0]
    assert "failure()" in block and "steps.generate.outcome == 'failure'" in block
    assert "path: .run_diagnostics/report_session_failure.json" in block
    assert "retention-days: 7" in block
    assert "include-hidden-files: true" in block
    assert "reports/" not in block
    for file in (".gitignore", ".dockerignore"):
        assert ".run_diagnostics/" in (root / file).read_text()
    source = (root / "briefing.py").read_text()
    guard = source.split("if source_session_issues:", 1)[1].split('market = _stage(', 1)[0]
    assert guard.index("save_session_diagnostic_safely(") < guard.index("raise RuntimeError(")
    assert "行情交易日完整性檢查失敗；禁止更新任何正式狀態" in guard


def test_raw_tail_dates_identify_unpriced_alignment_without_exporting_values():
    args = inputs()
    for symbol, frame in args["history"].items():
        frame["dividends"] = 0.0
        frame.loc[pd.Timestamp("2026-10-13"), ["close", "dividends"]] = [None, 0.0]
    report = build_session_diagnostic(**args)
    cohort = report["cohorts"]["TW_STOCK"]
    assert cohort["downloaded_raw_tail_index"]["session_counts"] == {"2026-10-13": 10}
    assert cohort["downloaded_unpriced_tail_count"] == 10
    assert cohort["downloaded_daily"]["session_counts"] == {"2026-10-08": 8, "2026-10-12": 2}
    text = json.dumps(report)
    for forbidden in ("987654321", "SECRET_SENTINEL", "https://", "0.TW", "dividends"):
        assert forbidden not in text


def test_raw_tail_handles_absent_close_empty_and_timezone():
    args = inputs()
    args["history"]["0.TW"] = pd.DataFrame({"dividends": [0]}, index=pd.to_datetime(["2026-10-13"]))
    args["history"]["1.TW"] = pd.DataFrame()
    args["history"]["2.TW"] = pd.DataFrame({"close": [None]}, index=pd.to_datetime(["2026-10-12T17:00:00Z"]))
    cohort = build_session_diagnostic(**args)["cohorts"]["TW_STOCK"]
    assert cohort["downloaded_raw_tail_index"]["session_counts"]["2026-10-13"] == 2
    assert cohort["downloaded_raw_tail_index"]["missing_or_invalid_count"] == 1
    assert cohort["downloaded_unpriced_tail_count"] == 2


def test_source_breakdown_is_allowlisted_and_contains_only_dates_counts():
    args = inputs()
    args['official_prices']['0'].update(tw_price_source='TWSE Open Data', date='2026-10-13')
    args['official_prices']['1'].update(tw_price_source='TWSE OpenAPI')
    args['official_prices']['2'].update(tw_price_source='https://secret/token')
    report = build_session_diagnostic(**args)
    breakdown = report['official_daily_by_source']
    assert set(breakdown) == {'TWSE OpenAPI','TPEx OpenAPI','TWSE Open Data'}
    assert breakdown['TWSE Open Data']['session_counts'] == {'2026-10-13':1}
    assert breakdown['TWSE OpenAPI']['dated_count'] == 1
    assert 'https://' not in json.dumps(report)
