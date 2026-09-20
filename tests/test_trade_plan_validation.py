import json
from pathlib import Path

from trade_plan_validation import update_trade_plan_validation


def _plan(session_date: str, price: float) -> dict:
    return {
        "updated_at": session_date,
        "plans": [
            {
                "symbol": "2330.TW",
                "name": "台積電",
                "market": "TW",
                "session_date": session_date,
                "price": price,
                "plans": {
                    "short": {
                        "active_entry_plan": True,
                        "plan_quality": {"entry_eligible": True},
                        "entry_low": 98,
                        "entry_high": 102,
                        "stop": 94,
                        "target1": 108,
                        "target2": 115,
                        "buy_window_sessions": 2,
                        "max_hold_sessions": 5,
                        "target1_pct": 25,
                        "target2_pct": 25,
                        "runner_pct": 50,
                        "score": 75,
                        "confidence": 75,
                        "data_quality_pct": 80,
                    },
                    "medium": {"active_entry_plan": False, "plan_quality": {"entry_eligible": False}},
                    "long": {"active_entry_plan": False, "plan_quality": {"entry_eligible": False}},
                },
            }
        ],
    }


def test_signal_is_recorded_before_future_outcome_and_triggers_only_later(tmp_path: Path):
    reports = tmp_path
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-20", 105)), encoding="utf-8")
    first = update_trade_plan_validation(reports)
    assert first["summary"]["signal_count"] == 1
    signal = first["signals"][0]
    assert signal["status"] == "waiting_entry"
    assert signal["entry_session_date"] is None

    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-21", 100)), encoding="utf-8")
    second = update_trade_plan_validation(reports)
    original = next(s for s in second["signals"] if s["source_session_date"] == "2026-09-20")
    assert original["status"] == "active"
    assert original["entry_session_date"] == "2026-09-21"
    assert original["entry_price"] == 100


def test_same_session_never_counts_as_outcome(tmp_path: Path):
    reports = tmp_path
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-20", 100)), encoding="utf-8")
    one = update_trade_plan_validation(reports)
    signal = one["signals"][0]
    assert signal["entry_wait_sessions"] == 0
    assert signal["entry_session_date"] is None

    two = update_trade_plan_validation(reports)
    signal = two["signals"][0]
    assert signal["entry_wait_sessions"] == 0
    assert signal["entry_session_date"] is None


def test_signal_expires_if_close_never_enters_zone(tmp_path: Path):
    reports = tmp_path
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-20", 110)), encoding="utf-8")
    update_trade_plan_validation(reports)
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-21", 109)), encoding="utf-8")
    update_trade_plan_validation(reports)
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-22", 108)), encoding="utf-8")
    result = update_trade_plan_validation(reports)
    old = next(s for s in result["signals"] if s["source_session_date"] == "2026-09-20")
    assert old["status"] == "expired_untriggered"
    assert old["matured"] is True


def test_stop_is_settled_from_later_close_only(tmp_path: Path):
    reports = tmp_path
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-20", 105)), encoding="utf-8")
    update_trade_plan_validation(reports)
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-21", 100)), encoding="utf-8")
    update_trade_plan_validation(reports)
    (reports / "trade_plan_shadow.json").write_text(json.dumps(_plan("2026-09-22", 93)), encoding="utf-8")
    result = update_trade_plan_validation(reports)
    old = next(s for s in result["signals"] if s["source_session_date"] == "2026-09-20")
    assert old["status"] == "closed"
    assert old["stop_hit"] is True
    assert old["matured"] is True
    assert old["final_return_pct"] == -7.0
