import json

from tools.publish_owner_data import CHUNK_PROTOCOL, build_chunked_bodies, build_payload


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
    (reports / "latest.json").write_text(json.dumps({
        "unavailable": [{"symbol": "6806.TW", "name": "森崴能源"}],
        "data_status": {"broker_count": 0, "financial_quality_count": 59},
    }), encoding="utf-8")
    (reports / "system_guard.json").write_text(json.dumps({
        "status": "warning", "checks": [{"code": "broker_data", "level": "warning"}],
    }), encoding="utf-8")
    monkeypatch.setenv("OWNER_SITE_BYPASS_TOKEN", "must-never-enter-payload")

    encoded, count = build_payload(reports)
    payload = json.loads(encoded)

    assert count == 1
    assert payload["rotation"]["markets"]["TW"]["status"] == "collecting"
    assert payload["integrity"]["report"]["data_status"]["broker_count"] == 0
    assert payload["integrity"]["report"]["unavailable"][0]["symbol"] == "6806.TW"
    assert payload["integrity"]["guard"]["status"] == "warning"
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


def test_owner_payload_appends_searchable_unranked_candidate(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "all_analysis.json").write_text(json.dumps({
        "data": [{"symbol": "2330.TW", "overall_rank": 1}],
        "candidate_count": 2, "analyzed_count": 1, "unavailable_count": 1,
    }), encoding="utf-8")
    (tmp_path / "search_data.json").write_text(json.dumps({"data": [{
        "股票": "中光電投控", "代號": "3718.TWO", "市場": "🇹🇼 台灣",
        "類型": "個股", "主題": "🚁 無人機", "次產業": "無人機",
    }]}), encoding="utf-8")
    (reports / "tw_official_cache.json").write_text(json.dumps({
        "data": {"prices": {"3718": {
            "close": 70.9, "date": "2026-09-07",
            "tw_official_price_available": True,
            "tw_price_source": "TPEx OpenAPI", "tw_price_unit": "TWD/shares",
        }}},
    }), encoding="utf-8")

    encoded, count = build_payload(reports)
    payload = json.loads(encoded)

    assert count == 2
    pending = payload["data"][1]
    assert pending["symbol"] == "3718.TWO"
    assert pending["price"] == 70.9
    assert pending["ranking_pending"] is True
    assert pending["overall_rank"] is None
    assert pending["trade_guard_blocked"] is True
    assert payload["data"][0] == {"symbol": "2330.TW", "overall_rank": 1}


def test_owner_payload_is_chunked_and_committed_only_after_all_rows(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    rows = [{"symbol": f"TW{index:03d}"} for index in range(45)]
    (reports / "all_analysis.json").write_text(json.dumps({
        "data": rows, "updated_at": "2026-08-30 20:00:00", "period": "evening",
    }), encoding="utf-8")

    encoded, _ = build_payload(reports)
    chunks, commit = build_chunked_bodies(encoded, generation=1788062400000, chunk_size=20)

    assert [len(item["data"]) for item in chunks] == [20, 20, 5]
    assert all(item["protocol"] == CHUNK_PROTOCOL and item["action"] == "chunk" for item in chunks)
    assert commit["action"] == "commit"
    assert commit["chunk_count"] == 3
    assert commit["total_count"] == 45
    assert "data" not in commit
    assert [row for item in chunks for row in item["data"]] == rows


def test_owner_payload_carries_private_holding_only_in_commit(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "all_analysis.json").write_text(json.dumps({
        "data": [{"symbol": "MSFT"}], "period": "morning",
    }), encoding="utf-8")
    private = {
        "updated_at": "2026-08-31 06:00:00",
        "medium": {"US": {"positions": [{
            "symbol": "MSFT", "last_price_source": "Tiingo_after_close_close_only",
        }]}},
        "long": {"positions": []},
    }
    (reports / "owner_private_holding_simulation.json").write_text(
        json.dumps(private), encoding="utf-8"
    )
    monkeypatch.setenv("TIINGO_API_KEY", "must-never-enter-payload")

    encoded, _ = build_payload(reports)
    chunks, commit = build_chunked_bodies(encoded, generation=1788062400001)

    assert commit["private_holding"] == private
    assert all("private_holding" not in chunk for chunk in chunks)
    assert b"must-never-enter-payload" not in encoded
