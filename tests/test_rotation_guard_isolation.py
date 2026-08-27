from __future__ import annotations

import json

import market_rotation_shadow
from briefing import _update_market_rotation_shadow_safely


def test_rotation_failure_is_quarantined_and_preserves_last_success(tmp_path, monkeypatch):
    health_path = tmp_path / "market_rotation_shadow_health.json"
    health_path.write_text(json.dumps({
        "status": "ok", "last_success_at": "2026-08-26 20:00:00",
    }), encoding="utf-8")

    def fail(*args, **kwargs):
        raise ValueError("測試用輪動失敗")

    monkeypatch.setattr(market_rotation_shadow, "update_market_rotation_shadow", fail)
    success = _update_market_rotation_shadow_safely(
        tmp_path, [], period="evening",
        updated_at="2026-08-27 20:00:00", intraday=False,
    )

    health = json.loads(health_path.read_text(encoding="utf-8"))
    assert success is False
    assert health["status"] == "warning"
    assert health["last_success_at"] == "2026-08-26 20:00:00"
    assert health["formal_pipeline_continues"] is True
    assert health["changes_rankings"] is False
    assert health["places_orders"] is False


def test_rotation_success_writes_independent_green_health(tmp_path, monkeypatch):
    monkeypatch.setattr(
        market_rotation_shadow, "update_market_rotation_shadow",
        lambda *args, **kwargs: {},
    )
    success = _update_market_rotation_shadow_safely(
        tmp_path, [], period="evening",
        updated_at="2026-08-27 20:00:00", intraday=False,
    )

    health = json.loads(
        (tmp_path / "market_rotation_shadow_health.json").read_text(encoding="utf-8")
    )
    assert success is True
    assert health["status"] == "ok"
    assert health["last_success_at"] == "2026-08-27 20:00:00"
    assert health["formal_pipeline_continues"] is True


def test_intraday_noop_does_not_erase_previous_warning(tmp_path, monkeypatch):
    health_path = tmp_path / "market_rotation_shadow_health.json"
    health_path.write_text(json.dumps({
        "status": "warning",
        "last_success_at": "2026-08-26 20:00:00",
        "error_type": "ValueError",
        "detail": "上次完整輪動失敗",
    }), encoding="utf-8")
    monkeypatch.setattr(
        market_rotation_shadow, "update_market_rotation_shadow",
        lambda *args, **kwargs: {},
    )

    success = _update_market_rotation_shadow_safely(
        tmp_path, [], period="noon",
        updated_at="2026-08-27 12:00:00", intraday=True,
    )

    health = json.loads(health_path.read_text(encoding="utf-8"))
    assert success is True
    assert health["status"] == "warning"
    assert health["last_success_at"] == "2026-08-26 20:00:00"
    assert health["detail"] == "上次完整輪動失敗"
