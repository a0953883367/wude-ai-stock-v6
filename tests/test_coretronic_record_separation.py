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


def test_retired_5371_is_not_reintroduced_into_current_outputs():
    for path in ["reports/all_analysis.json", "reports/rankings.json"]:
        current_symbols = {
            row.get("symbol")
            for row in _load(path)["data"]
            if isinstance(row, dict)
        }
        assert "5371.TWO" not in current_symbols, path

    financial_cache = _load("reports/tw_financial_official_cache.json")
    requested_symbols = set(financial_cache["requested_symbols"])
    assert "3718" in requested_symbols
    assert "5371" not in requested_symbols
