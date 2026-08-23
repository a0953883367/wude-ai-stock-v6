import json

from config import SETTINGS
from data_fetcher import load_search_universe, load_taiwan_universe


def test_search_universe_loads_all_maintained_markets():
    payload = json.loads(SETTINGS.search_data_path.read_text(encoding="utf-8"))
    rows = load_search_universe()
    symbols = {row["symbol"] for row in rows}
    assert len(rows) == payload["total"]
    assert len(symbols) == len(rows)
    assert {row["market"] for row in rows} == {"TW", "US"}
    assert any(row["type"] == "ETF" for row in rows)
    assert len(load_taiwan_universe()) == payload["summary"]["台灣個股"] + payload["summary"]["台灣ETF"]
    assert {"SPCX", "SKHY", "UMC", "HNHPF"} <= symbols
