import json

from validation_60d import update_validation_60d


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_validation_counts_only_real_forward_days(tmp_path):
    _write(tmp_path / "accuracy.json", {"calibration": {
        "trading_days_collected": 5, "eligible_one_day_samples": 126,
        "minimum_consensus_samples": 200, "ready_for_model_selection": False,
    }})
    _write(tmp_path / "million_simulation.json", {"markets": {"TW": {"completed_days": 5}, "US": {"completed_days": 4}}})
    payload = update_validation_60d(tmp_path, updated_at="2026-08-29")
    assert payload["trading_days_collected"] == 5
    assert payload["remaining_trading_days"] == 55
    assert payload["ready_for_model_selection"] is False
    assert payload["rules"]["future_data_forbidden"] is True
    assert payload["tracks"]["million_forward_60d"]["TW"] == {
        "completed_days": 5, "target_days": 60,
    }
    assert (tmp_path / "validation_60d.json").exists()


def test_validation_requires_days_samples_and_quality_gate(tmp_path):
    _write(tmp_path / "accuracy.json", {"calibration": {
        "trading_days_collected": 60, "eligible_one_day_samples": 250,
        "minimum_consensus_samples": 200, "ready_for_model_selection": True,
    }})
    payload = update_validation_60d(tmp_path, updated_at="now")
    assert payload["status"] == "complete"
    assert payload["ready_for_model_selection"] is True
