import json

from capital_flow_shadow import CapitalFlowConfig, CapitalFlowShadow
from large_buy_monitor import StockBaseline, load_stock_baselines


def baselines():
    return {
        "2330.TW": StockBaseline("2330.TW", "台積電", "TW", 1000, 10_000, "半導體", "個股"),
        "2303.TW": StockBaseline("2303.TW", "聯電", "TW", 100, 100_000, "半導體", "個股"),
        "2344.TW": StockBaseline("2344.TW", "華邦電", "TW", 50, 200_000, "半導體", "個股"),
        "0050.TW": StockBaseline("0050.TW", "台灣50", "TW", 100, 500_000, "大盤ETF", "ETF"),
        "NVDA": StockBaseline("NVDA", "NVIDIA", "US", 200, 1_000_000, "AI半導體", "個股"),
        "VOO": StockBaseline("VOO", "S&P 500 ETF", "US", 700, 1_000_000, "大盤ETF", "ETF"),
    }


def test_taiwan_and_us_are_reported_independently():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 1_000)
    flow.process_trade("2330.TW", price=1000, size=100, ask=1000, timestamp=990)
    flow.process_trade("2303.TW", price=99, size=100, bid=99, timestamp=991)
    flow.process_trade("NVDA", price=200, size=1000, ask=200, timestamp=992)

    snapshot = flow.snapshot(now=1_000)
    tw = snapshot["markets"]["TW"]["windows"]["15m"]
    us = snapshot["markets"]["US"]["windows"]["15m"]
    assert snapshot["policy"]["markets_separate"] is True
    assert {row["symbol"] for row in tw["top_inflows"]} == {"2330.TW"}
    assert {row["symbol"] for row in tw["top_outflows"]} == {"2303.TW"}
    assert {row["symbol"] for row in us["top_inflows"]} == {"NVDA"}
    assert all(row["market"] == "TW" for row in tw["top_inflows"] + tw["top_outflows"])
    assert all(row["market"] == "US" for row in us["top_inflows"] + us["top_outflows"])


def test_three_symbol_theme_resonance_requires_real_breadth():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 2_000)
    for index, symbol in enumerate(("2330.TW", "2303.TW", "2344.TW")):
        baseline = baselines()[symbol]
        flow.process_trade(
            symbol,
            price=baseline.price,
            size=100,
            ask=baseline.price,
            timestamp=1_990 + index,
        )
    themes = flow.snapshot(now=2_000)["markets"]["TW"]["windows"]["15m"]["theme_inflows"]
    semiconductor = next(row for row in themes if row["theme"] == "半導體")
    assert semiconductor["positive_symbols"] == 3
    assert semiconductor["resonance"] is True


def test_cosmetic_theme_prefixes_are_merged_into_one_rank():
    rows = baselines()
    rows["2303.TW"] = StockBaseline("2303.TW", "聯電", "TW", 100, 100_000, "💾 半導體", "個股")
    flow = CapitalFlowShadow(rows, clock=lambda: 2_000)
    flow.process_trade("2330.TW", price=1000, size=100, ask=1000, timestamp=1_990)
    flow.process_trade("2303.TW", price=100, size=100, ask=100, timestamp=1_991)
    themes = flow.snapshot(now=2_000)["markets"]["TW"]["windows"]["15m"]["theme_inflows"]
    semiconductor = [row for row in themes if row["theme"] == "半導體"]
    assert len(semiconductor) == 1
    assert semiconductor[0]["member_count"] == 2


def test_special_sip_print_is_counted_but_not_used_as_directional_flow():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 3_000)
    result = flow.process_trade(
        "NVDA", price=200, size=1_000_000, ask=200, conditions=["W"], timestamp=2_999
    )
    assert result["eligible"] is False
    window = flow.snapshot(now=3_000)["markets"]["US"]["windows"]["1m"]
    assert window["trade_count"] == 1
    assert window["filtered_trade_count"] == 1
    assert window["net_flow"] == 0
    assert not window["top_inflows"]


def test_trade_correction_reverses_original_contribution():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 4_000)
    flow.process_trade(
        "NVDA", price=200, size=100, ask=200, trade_id=1, timestamp=3_990
    )
    flow.correct_trade(
        "NVDA",
        original_trade_id=1,
        corrected_trade_id=2,
        price=199,
        size=100,
        timestamp=3_991,
        market="US",
    )
    window = flow.snapshot(now=4_000)["markets"]["US"]["windows"]["1m"]
    assert window["buy_value"] == 0
    assert window["sell_value"] == 19_900
    assert window["net_flow"] == -19_900
    assert [row["symbol"] for row in window["top_outflows"]] == ["NVDA"]


def test_duplicate_trade_id_is_ignored():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 5_000)
    assert flow.process_trade("VOO", price=700, size=10, ask=700, trade_id=5, timestamp=4_999)
    assert flow.process_trade("VOO", price=700, size=10, ask=700, trade_id=5, timestamp=4_999) is None
    window = flow.snapshot(now=5_000)["markets"]["US"]["windows"]["1m"]
    assert window["trade_count"] == 1
    assert window["buy_value"] == 7_000


def test_shadow_state_survives_service_restart(tmp_path):
    state = tmp_path / "capital-flow.json"
    config = CapitalFlowConfig(persist_interval_seconds=0)
    first = CapitalFlowShadow(baselines(), state_path=state, config=config, clock=lambda: 6_000)
    first.process_trade("0050.TW", price=100, size=100, ask=100, timestamp=5_999)
    first.snapshot(now=6_000)

    restored = CapitalFlowShadow(baselines(), state_path=state, config=config, clock=lambda: 6_001)
    window = restored.snapshot(now=6_001)["markets"]["TW"]["windows"]["1m"]
    assert window["buy_value"] == 10_000
    assert window["asset_groups"]["ETF"]["net_flow"] == 10_000


def test_baseline_loader_keeps_theme_and_asset_type(tmp_path):
    report = tmp_path / "all_analysis.json"
    report.write_text(json.dumps({"data": [{
        "symbol": "0050.TW", "name": "台灣50", "market": "TW", "price": 100,
        "avg_volume20": 1000, "theme": "大盤ETF", "type": "ETF",
    }]}), encoding="utf-8")
    result = load_stock_baselines(report)["0050.TW"]
    assert result.theme == "大盤ETF"
    assert result.asset_type == "ETF"
