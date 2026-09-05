import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_active_universe_uses_3718_but_legacy_snapshot_keeps_5371():
    active = _load("search_data.json")["data"]
    legacy = _load("stock_data.json")["data"]

    assert any(row.get("代號") == "3718.TWO" for row in active)
    assert not any(row.get("代號") == "5371.TWO" for row in active)
    assert any(row.get("代號") == "5371.TWO" for row in legacy)


def test_5371_historical_and_model_records_are_preserved():
    protected_paths = [
        "reports/all_analysis.json",
        "reports/rankings.json",
        "reports/tw_official_cache.json",
        "reports/tw_financial_official_cache.json",
        "reports/archive/2026-08-21-evening.json",
    ]

    for path in protected_paths:
        assert "5371" in (ROOT / path).read_text(encoding="utf-8"), path
