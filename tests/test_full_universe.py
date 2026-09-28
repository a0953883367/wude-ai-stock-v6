import json

from config import SETTINGS
from data_fetcher import load_analysis_universe, load_search_universe, load_taiwan_universe


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
    assert "ARTY" in symbols
    assert "IRBO" not in symbols
    hn = next(row for row in rows if row["symbol"] == "HNHPF")
    arty = next(row for row in rows if row["symbol"] == "ARTY")
    assert hn["ranking_mode"] == "reference_only"
    assert hn["primary_symbol"] == "2317.TW"
    assert arty["legacy_symbol"] == "IRBO"
    assert arty["symbol_effective_date"] == "2024-08-12"
    assert "3718.TWO" in symbols
    assert "5371.TWO" not in symbols
    assert next(row for row in rows if row["symbol"] == "3718.TWO")["name"] == "中光電投控"



def test_analysis_universe_automatically_includes_every_watchlist_addition():
    search_symbols = {row["symbol"] for row in load_search_universe()}
    rows = load_analysis_universe()
    by_symbol = {row["symbol"]: row for row in rows}

    # These user additions are intentionally absent from the broad catalogue;
    # the canonical loader must still expose them to every formal feature.
    assert {"6530.TWO", "6841.TWO", "TEM", "IONQ", "RXRX"} <= by_symbol.keys()
    assert len(rows) == len(by_symbol)
    assert len(rows) >= len(search_symbols)
    assert by_symbol["6530.TWO"]["market"] == "TW"
    assert by_symbol["TEM"]["market"] == "US"
