import json

from stock_growth_control import build_stock_growth_control, update_stock_growth_control


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_control_tower_builds_seven_stages_and_six_layers(tmp_path):
    _write(tmp_path / "system_guard.json", {"status": "ok", "counts": {"warning": 0, "critical": 0}})
    _write(tmp_path / "validation_60d.json", {"trading_days_collected": 26})
    _write(tmp_path / "prediction_engine_health.json", {"status": "ok"})
    _write(tmp_path / "trade_plan_validation.json", {"summary": {"matured": 405}})
    _write(tmp_path / "model_graduation.json", {"models": [{
        "model_id": "next_session_v7", "label": "隔日方向影子模型",
        "status": "collecting", "current": 26, "target": 60, "reason": "累積中",
    }]})
    payload = build_stock_growth_control(tmp_path, updated_at="now")
    assert len(payload["workflow_stages"]) == 7
    assert len(payload["monitoring_layers"]) == 6
    assert payload["models"][0]["phase"] == "preliminary_review"
    assert payload["summary"]["validation_days"] == 26
    assert payload["safety"]["formal_v6_locked"] is True
    assert payload["safety"]["automatic_orders"] is False


def test_control_tower_never_exposes_private_billing_or_auto_promotes(tmp_path):
    _write(tmp_path / "model_graduation.json", {"models": [{
        "model_id": "ready", "label": "候選", "status": "eligible_for_manual_graduation",
        "current": 60, "target": 60, "reason": "達標",
    }]})
    payload = update_stock_growth_control(tmp_path, updated_at="now")
    model = payload["models"][0]
    assert model["phase"] == "waiting_owner"
    assert model["formal_promotion"] == "manual_only"
    assert payload["safety"]["stores_private_billing_details"] is False
    text = (tmp_path / "stock_growth_control.json").read_text(encoding="utf-8")
    assert "99美元" not in text
    assert "3300" not in text


def test_red_system_guard_blocks_data_and_system_layers(tmp_path):
    _write(tmp_path / "system_guard.json", {"status": "critical", "counts": {"critical": 1}})
    payload = build_stock_growth_control(tmp_path)
    by_id = {row["id"]: row for row in payload["monitoring_layers"]}
    assert payload["status"] == "blocked"
    assert by_id["data"]["light"] == "red"
    assert by_id["system"]["light"] == "red"
