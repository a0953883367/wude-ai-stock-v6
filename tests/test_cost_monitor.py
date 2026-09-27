import json

from cost_monitor import update_cost_monitor


def test_cost_monitor_public_output_never_contains_raw_amounts(tmp_path):
    (tmp_path / "private_cost_usage.json").write_text(json.dumps({
        "providers": {
            "alpaca": {
                "budget_status": "near_limit",
                "renewal_days": 21,
                "monthly_price": 99,
                "account": "private-account",
            }
        }
    }), encoding="utf-8")
    payload = update_cost_monitor(tmp_path, updated_at="now")
    assert payload["status"] == "warning"
    assert payload["providers"][0]["budget_status"] == "near_limit"
    public = (tmp_path / "cost_monitor.json").read_text(encoding="utf-8")
    assert "monthly_price" not in public
    assert "private-account" not in public
    assert payload["privacy"]["raw_amounts_public"] is False


def test_cost_monitor_requests_private_setup_without_faking_usage(tmp_path):
    payload = update_cost_monitor(tmp_path, updated_at="now")
    assert payload["status"] == "setup_required"
    assert all(row["budget_status"] == "unknown" for row in payload["providers"])
