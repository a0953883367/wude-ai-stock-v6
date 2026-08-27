import json

from tools.publish_owner_data import build_payload


def test_owner_payload_includes_rotation_without_environment_secrets(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "all_analysis.json").write_text(json.dumps({
        "data": [{"symbol": "2330.TW", "score": 80}],
        "updated_at": "2026-08-27 20:00:00", "period": "evening",
    }), encoding="utf-8")
    (reports / "market_rotation_shadow.json").write_text(json.dumps({
        "version": 1, "markets": {"TW": {"status": "collecting"}, "US": {}}
    }), encoding="utf-8")
    monkeypatch.setenv("OWNER_SITE_BYPASS_TOKEN", "must-never-enter-payload")

    encoded, count = build_payload(reports)
    payload = json.loads(encoded)

    assert count == 1
    assert payload["rotation"]["markets"]["TW"]["status"] == "collecting"
    assert b"must-never-enter-payload" not in encoded


def test_owner_payload_remains_compatible_when_rotation_is_missing(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "all_analysis.json").write_text(json.dumps({
        "data": [{"symbol": "AAPL"}], "period": "morning",
    }), encoding="utf-8")

    encoded, count = build_payload(reports)

    assert count == 1
    assert json.loads(encoded)["rotation"] is None
