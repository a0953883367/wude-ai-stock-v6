from __future__ import annotations

import sqlite3
from pathlib import Path

from model_unit_learning import (
    UNIT_SPECS,
    _evaluate_control,
    build_unit_learning_report,
    record_unit_signals,
    refresh_unit_learning,
)
from prediction_engine.storage import PredictionStore


def _row(session_date: str, price: float = 100.0) -> dict:
    return {
        "symbol": "2330.TW", "name": "台積電", "market": "TW", "type": "個股",
        "official_session_date": session_date, "official_close_price": price,
        "market_contract_valid": True, "market_data_quality_score": 90,
        "technical_score": 70, "volume_score": 65, "macro_score": 60,
        "credit_available": True, "credit_score": 55,
        "tw_accumulation_available": True, "tw_accumulation_score": 62,
        "fundamental_available": True, "financial_quality_score": 70,
        "growth_score": 65, "fundamental_score": 68,
        "news_data_available": True, "news_verified": True, "news_penalty": 0,
    }


def _decision() -> dict:
    return {
        "symbol": "2330.TW", "market": "TW",
        "final": {"recommendation": "can_scale", "confidence": 70},
        "capital_flow_shadow": {
            "direction": "support", "validated_for_decision": True, "confidence": 70,
        },
    }


def test_unit_ledgers_freeze_only_at_completed_market_checkpoint(tmp_path: Path) -> None:
    store = PredictionStore(tmp_path / "engine.sqlite3")
    store.record_session("TW", "2026-09-01", "now")
    store.record_prices([_row("2026-09-01")], "2026-09-01")
    assert record_unit_signals(
        store, [_row("2026-09-01")], [_decision()],
        period="noon", updated_at="noon", intraday=False,
    ) == 0
    inserted = record_unit_signals(
        store, [_row("2026-09-01")], [_decision()],
        period="evening", updated_at="close", intraday=False,
    )
    assert inserted == len(UNIT_SPECS)
    assert record_unit_signals(
        store, [_row("2026-09-01")], [_decision()],
        period="evening", updated_at="reload", intraday=False,
    ) == 0


def test_unit_ledgers_settle_on_next_completed_session_only(tmp_path: Path) -> None:
    store = PredictionStore(tmp_path / "engine.sqlite3")
    first = _row("2026-09-01", 100)
    store.record_session("TW", "2026-09-01", "first")
    store.record_prices([first], "2026-09-01")
    record_unit_signals(
        store, [first], [_decision()], period="evening", updated_at="first", intraday=False,
    )
    _, refresh = refresh_unit_learning(store, updated_at="same", intraday=False)
    assert refresh["settled_rows"] == 0
    second = _row("2026-09-02", 102)
    store.record_session("TW", "2026-09-02", "second")
    store.record_prices([second], "2026-09-02")
    _, refresh = refresh_unit_learning(store, updated_at="second", intraday=False)
    assert refresh["settled_rows"] == len(UNIT_SPECS)
    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "SELECT outcome_session_date,direction_correct FROM unit_learning_predictions "
            "WHERE unit_id='technical_kline'"
        ).fetchone()
        quality = db.execute(
            "SELECT direction_correct FROM unit_learning_predictions WHERE unit_id='data_quality'"
        ).fetchone()
    assert row == ("2026-09-02", 1)
    assert quality == (1,)


def test_shadow_trust_promotes_after_three_wins_and_rolls_back_after_two_failures(tmp_path: Path) -> None:
    store = PredictionStore(tmp_path / "engine.sqlite3")
    base = {"session_count": 20, "scored_samples": 100, "holdout_hit_pct": 60.0}
    state = None
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        state = _evaluate_control(
            store, "technical_kline", "TW_STOCK",
            {**base, "latest_session": day}, updated_at=day,
        )
    assert state is not None
    assert state["status"] == "shadow_trust_active"
    assert state["active_multiplier"] == 1.1
    weak = {"session_count": 22, "scored_samples": 110, "holdout_hit_pct": 40.0}
    for day in ("2026-09-04", "2026-09-05"):
        state = _evaluate_control(
            store, "technical_kline", "TW_STOCK",
            {**weak, "latest_session": day}, updated_at=day,
        )
    assert state["status"] == "rolled_back"
    assert state["active_multiplier"] == 1.0
    reports = tmp_path / "reports"
    reports.mkdir()
    _, refresh = refresh_unit_learning(store, updated_at="2026-09-05", intraday=True)
    report = build_unit_learning_report(
        reports, store, refresh, inserted_rows=0, updated_at="2026-09-05"
    )
    assert report["pending_notifications"]
    assert "自動退版" in report["pending_notifications"][0]["message"]
    assert "正式V6未變更" in report["pending_notifications"][0]["message"]


def test_public_unit_report_exposes_summary_not_raw_predictions(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    store = PredictionStore(tmp_path / "engine.sqlite3")
    _, refresh = refresh_unit_learning(store, updated_at="now", intraday=True)
    report = build_unit_learning_report(
        reports, store, refresh, inserted_rows=0, updated_at="now"
    )
    assert report["summary"]["dedicated_ledger_units"] == 11
    assert report["policy"]["formal_v6_unchanged"] is True
    assert report["policy"]["automatic_orders"] is False
    assert len(report["units"]) == 11
    assert "predictions" not in report
