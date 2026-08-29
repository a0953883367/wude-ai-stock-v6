import json

from model_graduation import update_model_graduation


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_graduation_conclusions_are_automatic_but_promotion_is_manual(tmp_path):
    _write(tmp_path / "validation_60d.json", {"trading_days_collected": 5, "ready_for_model_selection": False})
    _write(tmp_path / "tw_weight_experiment.json", {"models": {"base": {"days": [{}, {}, {}, {}, {}]}}})
    payload = update_model_graduation(tmp_path, updated_at="now")
    assert payload["status"] == "ready"
    assert payload["policy"]["conclusion_generated_automatically"] is True
    assert payload["policy"]["promotion_requires_manual_decision"] is True
    assert payload["policy"]["automatic_weight_changes"] is False
    assert all(model["automatic_promotion"] is False for model in payload["models"])
    weight = next(model for model in payload["models"] if model["model_id"] == "tw_institution_weight")
    assert weight["current"] == 5
    assert weight["status"] == "collecting"
