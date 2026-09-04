from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from system_guard import build_guard


def test_system_guard_workflow_installs_http_dependency_before_running() -> None:
    workflow = Path(".github/workflows/system-guard.yml").read_text(encoding="utf-8")
    install = workflow.index('python -m pip install "requests>=2.32,<3"')
    inspect = workflow.index("python system_guard.py --state-change-only")
    assert install < inspect


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _healthy_reports(tmp_path: Path) -> None:
    for name in (
        "index.html", "live-flow.html", "decision-hub.html",
        "inverse-etf-shadow.html", "valuation-risk-shadow.html", "app_shell.js",
    ):
        (tmp_path.parent / name).write_text("ok", encoding="utf-8")
    timestamp = "2026-08-24 16:00:00"
    status = {
        "expected_tw_count": 67,
        "expected_financial_quality_count": 67,
        "tw_official_price_count": 175,
        "institutional_count": 176,
        "credit_count": 175,
        "broker_count": 10,
        "financial_quality_count": 67,
        "us_sec_company_count": 12,
        "us_sec_fallback_count": 3,
    }
    _write(tmp_path / "latest.json", {"updated_at": timestamp, "universe_count": 337, "analyzed_count": 330, "data_status": status})
    rows = [
        {"symbol": "2330.TW", "market": "TW", "type": "個股", "official_session_date": "2026-08-24"},
        {"symbol": "0050.TW", "market": "TW", "type": "ETF", "official_session_date": "2026-08-24"},
        {"symbol": "AAPL", "market": "US", "type": "個股", "official_session_date": "2026-08-23"},
        {"symbol": "VOO", "market": "US", "type": "ETF", "official_session_date": "2026-08-23"},
    ]
    _write(tmp_path / "rankings.json", {"updated_at": timestamp, "data": rows})
    _write(tmp_path / "all_analysis.json", {"updated_at": timestamp, "candidate_count": 337, "analyzed_count": 330, "data": rows})
    _write(tmp_path / "holding_simulation.json", {
        "updated_at": timestamp,
        "medium": {
            "TW": {
                "positions": [{"market": "TW", "last_valuation_date": "2026-08-24"}],
                "benchmark_positions": [{"market": "TW", "last_valuation_date": "2026-08-24"}],
                "last_valuation_date": "2026-08-24",
            },
            "US": {
                "positions": [{"market": "US", "last_valuation_date": "2026-08-23"}],
                "benchmark_positions": [{"market": "US", "last_valuation_date": "2026-08-23"}],
                "last_valuation_date": "2026-08-23",
            },
        },
        "long": {
            "positions": [
                {"market": "TW", "last_valuation_date": "2026-08-24"},
                {"market": "US", "last_valuation_date": "2026-08-23"},
            ],
            "benchmark_positions": [
                {"market": "TW", "last_valuation_date": "2026-08-24"},
                {"market": "US", "last_valuation_date": "2026-08-23"},
            ],
            "last_valuation_date": {"TW": "2026-08-24", "US": "2026-08-23"},
        },
    })
    _write(tmp_path / "market_rotation_shadow_health.json", {
        "status": "ok", "checked_at": timestamp, "last_success_at": timestamp,
        "formal_pipeline_continues": True, "changes_rankings": False,
    })
    _write(tmp_path / "valuation_risk_shadow_health.json", {
        "status": "ok", "checked_at": timestamp, "last_success_at": timestamp,
        "formal_pipeline_continues": True, "changes_rankings": False,
        "changes_weights": False,
    })
    _write(tmp_path / "decision_hub_health.json", {
        "status": "ok", "checked_at": timestamp, "last_success_at": timestamp,
        "formal_pipeline_continues": True, "changes_rankings": False,
        "changes_weights": False, "places_orders": False,
    })
    _write(tmp_path / "stockq_market_context.json", {
        "status": "ok", "indicator_count": 16, "updated_at": timestamp,
    })
    _write(tmp_path / "validation_60d.json", {
        "status": "collecting", "trading_days_collected": 5, "target_trading_days": 60,
    })
    _write(tmp_path / "model_graduation.json", {"status": "ready", "summary": {"collecting": 5}})
    _write(tmp_path / "model_learning.json", {
        "schema_version": 1,
        "error_learning": {"raw_error_rows": 10, "independent_events": 4},
        "shadow_candidates": [{"candidate_id": "shadow:test:v1"}],
        "policy": {
            "formal_v6_frozen": True,
            "automatic_merge": False,
            "broker_orders": False,
        },
    })
    _write(tmp_path / "model_unit_learning.json", {
        "status": "ready",
        "summary": {
            "dedicated_ledger_units": 11,
            "matured_rows": 0,
            "active_shadow_trust_streams": 0,
        },
        "policy": {"formal_v6_unchanged": True, "automatic_orders": False},
        "recent_events": [],
    })
    _write(tmp_path / "prediction_engine.json", {
        "status": "collecting",
        "run_summary": {"model_competition": {
            "controlled_shadow_promotions": 0,
            "controlled_shadow_rollbacks": 0,
            "formal_v6_promotions": 0,
        }},
    })
    _write(tmp_path / "forward_outcome_ledger.json", {
        "updated_at": timestamp,
        "markets": {"TW": {"cohort_count": 1}, "US": {"cohort_count": 1}},
        "policy": {
            "same_day_backfill_forbidden": True,
            "future_data_forbidden": True,
            "shadow_learning_only": True,
            "formal_v6_modified": False,
        },
    })
    _write(tmp_path / "unified_evidence.json", {"status": "ready", "evidence_count": 20, "invalid_count": 0})
    _write(tmp_path / "tw_financial_official_cache.json", {
        "requested_count": 67, "available_count": 66, "coverage_pct": 98.51,
    })


def _healthy_live_probe() -> dict:
    return {
        "probe_ok": True,
        "status_code": 200,
        "payload": {
            "ok": True,
            "service": "wude-live-api",
            "device_pairing_configured": True,
            "large_buy_monitor": {
                "enabled": True,
                "streams": {
                    "TW": {"state": "waiting_market_open", "subscribed": 0, "error": None},
                    "US": {"state": "premarket_connected", "subscribed": 186, "error": None},
                },
                "telegram_configured": True,
                "telegram_delivery": {
                    "configured": True,
                    "state": "delivered",
                    "last_success_at": "2026-08-24T08:01:00+00:00",
                    "last_error": None,
                },
            },
        },
    }


def test_healthy_guard_is_green(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(
        tmp_path,
        now=now,
        friend_publish="success",
        owner_publish="success",
        primary_app_probe={"probe_ok": True, "status_code": 200},
        live_runtime_probe=_healthy_live_probe(),
    )
    assert guard["status"] == "ok"
    assert guard["safety"]["places_orders"] is False
    sec = next(item for item in guard["checks"] if item["code"] == "us_sec_fundamentals")
    assert sec["level"] == "ok"
    assert "實際補值 3 檔" in sec["detail"]


def test_validation_progress_stall_is_visible_without_changing_models(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "validation_progress_monitor.json", {
        "status": "warning",
        "current": {"trading_days_collected": 5, "target_trading_days": 60},
        "markets": {
            "TW": {
                "status": "warning", "last_session_date": "2026-08-24",
                "last_completed_days": 5, "stalled_sessions": 1,
            },
            "US": {
                "status": "ok", "last_session_date": "2026-08-23",
                "last_completed_days": 4, "stalled_sessions": 0,
            },
        },
    })
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")

    check = next(item for item in guard["checks"] if item["code"] == "validation_60d")
    assert guard["status"] == "warning"
    assert check["level"] == "warning"
    assert "TW 連續 1 次" in check["detail"]
    assert guard["safety"]["changes_rankings"] is False
    assert guard["safety"]["places_orders"] is False


def test_legacy_owner_publish_failure_does_not_turn_primary_app_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="failure")

    owner = next(item for item in guard["checks"] if item["code"] == "publish_owner")
    app = next(item for item in guard["checks"] if item["code"] == "primary_app_output")
    assert guard["status"] == "ok"
    assert owner["level"] == "info"
    assert "已與主 App" in owner["detail"]
    assert app["level"] == "ok"
    assert guard["monitoring_boundaries"]["device_authorization"].startswith("單一手機")


def test_missing_primary_app_file_is_warning_but_does_not_change_rankings(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    (tmp_path.parent / "live-flow.html").unlink()
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")

    app = next(item for item in guard["checks"] if item["code"] == "primary_app_output")
    assert guard["status"] == "warning"
    assert app["level"] == "warning"
    assert guard["safety"]["changes_rankings"] is False


def test_runtime_components_are_reported_independently(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(
        tmp_path,
        now=now,
        friend_publish="success",
        owner_publish="failure",
        primary_app_probe={"probe_ok": True, "status_code": 200},
        live_runtime_probe=_healthy_live_probe(),
    )

    checks = {item["code"]: item for item in guard["checks"]}
    assert checks["primary_app_output"]["level"] == "ok"
    assert checks["live_backend"]["level"] == "ok"
    assert checks["stream_tw"]["level"] == "info"
    assert "未開盤" in checks["stream_tw"]["detail"]
    assert checks["stream_us"]["level"] == "ok"
    assert checks["telegram_delivery"]["level"] == "ok"
    assert checks["device_authorization"]["level"] == "ok"
    assert checks["publish_owner"]["level"] == "info"
    assert guard["status"] == "ok"


def test_railway_failure_is_red_without_guessing_subcomponent_failures(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "system_guard.json", {
        "checks": [{"code": "live_backend", "level": "warning", "consecutive_failures": 2}],
    })
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(
        tmp_path,
        now=now,
        friend_publish="success",
        owner_publish="success",
        primary_app_probe={"probe_ok": True, "status_code": 200},
        live_runtime_probe={"probe_ok": False, "error": "Timeout"},
    )

    checks = {item["code"]: item for item in guard["checks"]}
    assert guard["status"] == "critical"
    assert checks["live_backend"]["level"] == "critical"
    assert checks["stream_tw"]["level"] == "info"
    assert checks["stream_us"]["level"] == "info"
    assert checks["telegram_delivery"]["level"] == "info"
    assert checks["device_authorization"]["level"] == "info"


def test_first_railway_probe_timeout_is_warning_not_false_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(
        tmp_path,
        now=now,
        friend_publish="success",
        owner_publish="success",
        primary_app_probe={"probe_ok": True, "status_code": 200},
        live_runtime_probe={"probe_ok": False, "error": "Timeout"},
    )

    backend = next(item for item in guard["checks"] if item["code"] == "live_backend")
    assert guard["status"] == "warning"
    assert backend["level"] == "warning"
    assert backend["consecutive_failures"] == 1


def test_telegram_failure_and_phone_setup_do_not_stop_backend_or_rankings(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    probe = _healthy_live_probe()
    monitor = probe["payload"]["large_buy_monitor"]
    monitor["telegram_delivery"] = {
        "configured": True,
        "state": "delivery_failed",
        "last_success_at": "2026-08-23T08:01:00+00:00",
        "last_error": "HTTPError",
    }
    probe["payload"]["device_pairing_configured"] = False
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(
        tmp_path,
        now=now,
        friend_publish="success",
        owner_publish="success",
        primary_app_probe={"probe_ok": True, "status_code": 200},
        live_runtime_probe=probe,
    )

    checks = {item["code"]: item for item in guard["checks"]}
    assert guard["status"] == "warning"
    assert checks["live_backend"]["level"] == "ok"
    assert checks["stream_us"]["level"] == "ok"
    assert checks["telegram_delivery"]["level"] == "warning"
    assert checks["device_authorization"]["level"] == "info"
    assert guard["safety"]["changes_rankings"] is False


def test_stale_and_inconsistent_reports_are_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    rankings = json.loads((tmp_path / "rankings.json").read_text(encoding="utf-8"))
    rankings["updated_at"] = "2026-08-23 12:00:00"
    _write(tmp_path / "rankings.json", rankings)
    now = datetime(2026, 8, 25, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="failure", owner_publish="success")
    assert guard["status"] == "critical"
    codes = {item["code"]: item["level"] for item in guard["checks"]}
    assert codes["report_freshness"] == "critical"
    assert codes["report_consistency"] == "critical"
    assert codes["publish_friend"] == "warning"


def test_missing_optional_broker_data_is_information_not_system_warning(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    latest["data_status"]["broker_count"] = 0
    _write(tmp_path / "latest.json", latest)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    broker = next(item for item in guard["checks"] if item["code"] == "broker_data")
    assert broker["level"] == "info"
    assert "未以其他資料冒充" in broker["detail"]


def test_financial_quality_excludes_watchlist_etfs_from_expected_count(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    latest["data_status"].pop("expected_financial_quality_count", None)
    latest["data_status"]["expected_tw_count"] = 2
    latest["data_status"]["financial_quality_count"] = 1
    latest["watchlist"] = [
        {"symbol": "2330.TW", "market": "TW", "type": "個股"},
        {"symbol": "0050.TW", "market": "TW", "type": "ETF"},
    ]
    _write(tmp_path / "latest.json", latest)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")

    financial = next(item for item in guard["checks"] if item["code"] == "financial_quality")
    assert financial["level"] == "ok"
    assert "1/1 檔公司" in financial["detail"]
    assert "1 檔 ETF 不適用" in financial["detail"]


def test_closed_market_carried_official_snapshot_is_not_a_false_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    latest["data_status"]["expected_tw_count"] = 2
    latest["data_status"]["tw_official_price_count"] = 0
    latest["data_status"]["tw_official_institution_count"] = 0
    latest["data_status"]["tw_official_credit_count"] = 0
    _write(tmp_path / "latest.json", latest)
    analysis = json.loads((tmp_path / "all_analysis.json").read_text(encoding="utf-8"))
    for row in analysis["data"]:
        if row.get("market") == "TW":
            row.update({
                "official_close_price": 100,
                "institution_available": True,
                "credit_available": 1,
            })
    _write(tmp_path / "all_analysis.json", analysis)

    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    core = next(item for item in guard["checks"] if item["code"] == "tw_core_data")

    assert core["level"] == "ok"
    assert "沿用最近完整收盤" in core["detail"]


def test_rotation_failure_is_independent_warning(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "market_rotation_shadow_health.json", {
        "status": "warning",
        "checked_at": "2026-08-24 16:00:00",
        "last_success_at": "2026-08-23 20:00:00",
        "error_type": "ValueError",
        "formal_pipeline_continues": True,
        "changes_rankings": False,
    })
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    rotation = next(item for item in guard["checks"] if item["code"] == "rotation_shadow")
    assert guard["status"] == "warning"
    assert rotation["level"] == "warning"
    assert "正式排名與早中晚報仍繼續" in rotation["detail"]
    assert guard["safety"]["changes_rankings"] is False


def test_valuation_failure_is_independent_warning(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "valuation_risk_shadow_health.json", {
        "status": "warning",
        "checked_at": "2026-08-24 16:00:00",
        "last_success_at": "2026-08-23 20:00:00",
        "error_type": "RuntimeError",
        "formal_pipeline_continues": True,
        "changes_rankings": False,
        "changes_weights": False,
    })
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    valuation = next(item for item in guard["checks"] if item["code"] == "valuation_risk_shadow")
    assert guard["status"] == "warning"
    assert valuation["level"] == "warning"
    assert guard["safety"]["changes_rankings"] is False


def test_decision_hub_failure_is_independent_warning(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "decision_hub_health.json", {
        "status": "warning", "checked_at": "2026-08-24 16:00:00",
        "last_success_at": None, "error_type": "RuntimeError",
        "formal_pipeline_continues": True, "changes_rankings": False,
        "changes_weights": False, "places_orders": False,
    })
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    check = next(item for item in guard["checks"] if item["code"] == "decision_hub")
    assert guard["status"] == "warning"
    assert check["level"] == "warning"
    assert guard["safety"]["changes_rankings"] is False
    assert guard["safety"]["places_orders"] is False


def test_mixed_market_sessions_are_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    analysis = json.loads((tmp_path / "all_analysis.json").read_text(encoding="utf-8"))
    analysis["data"] = [
        {
            "symbol": f"TW{index:02d}.TW", "market": "TW", "type": "個股",
            "official_session_date": "2026-08-26" if index < 8 else "2026-08-27",
        }
        for index in range(10)
    ]
    _write(tmp_path / "all_analysis.json", analysis)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    check = next(item for item in guard["checks"] if item["code"] == "market_session_consistency")
    assert guard["status"] == "critical"
    assert check["level"] == "critical"
    assert "8/10" in check["detail"]


def test_mixed_holding_valuation_dates_are_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    holding = json.loads((tmp_path / "holding_simulation.json").read_text(encoding="utf-8"))
    holding["medium"]["TW"]["positions"].append({
        "market": "TW", "last_valuation_date": "2026-08-27"
    })
    _write(tmp_path / "holding_simulation.json", holding)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    check = next(item for item in guard["checks"] if item["code"] == "holding_valuation_consistency")
    assert guard["status"] == "critical"
    assert check["level"] == "critical"
    assert "估值日混雜" in check["detail"]


def test_holding_benchmark_date_mismatch_is_red(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    holding = json.loads((tmp_path / "holding_simulation.json").read_text(encoding="utf-8"))
    holding["medium"]["TW"]["benchmark_positions"] = [{
        "market": "TW", "last_valuation_date": "2026-08-27"
    }]
    holding["long"]["benchmark_positions"] = [
        {"market": "TW", "last_valuation_date": "2026-08-27"},
        {"market": "US", "last_valuation_date": "2026-08-23"},
    ]
    _write(tmp_path / "holding_simulation.json", holding)
    now = datetime(2026, 8, 24, 16, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    guard = build_guard(tmp_path, now=now, friend_publish="success", owner_publish="success")
    check = next(item for item in guard["checks"] if item["code"] == "holding_valuation_consistency")
    assert guard["status"] == "critical"
    assert check["level"] == "critical"
    assert "持倉與基準估值日混雜" in check["detail"]
