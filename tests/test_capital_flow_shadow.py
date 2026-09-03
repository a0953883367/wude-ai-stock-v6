import json
from datetime import datetime
from zoneinfo import ZoneInfo

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


def test_signal_quality_rankings_stay_primary_while_amount_lists_remain_available():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 1_000)

    def row(symbol, theme, buy, sell, confidence):
        return {
            "symbol": symbol,
            "theme": theme,
            "asset_type": "個股",
            "buy_value": buy,
            "sell_value": sell,
            "neutral_value": 0,
            "net_flow": buy - sell,
            "buy_ratio_pct": buy / (buy + sell) * 100,
            "confidence": confidence,
            "persistence_pct": confidence,
            "net_to_average_daily_value_pct": confidence,
            "trade_count": 1,
            "eligible_trade_count": 1,
            "filtered_trade_count": 0,
        }

    summary = flow._summarize_rows([
        row("SMALL_IN", "高比例小流入", 190, 90, 99),
        row("LARGE_IN", "低比例大流入", 600, 400, 10),
        row("SMALL_OUT", "高比例小流出", 10, 160, 99),
        row("LARGE_OUT", "低比例大流出", 400, 700, 10),
    ])

    assert summary["ranking_basis"] == "signal_quality"
    assert summary["display_ranking_basis"] == "signal_quality"
    assert [item["symbol"] for item in summary["amount_top_inflows"][:2]] == ["LARGE_IN", "SMALL_IN"]
    assert [item["symbol"] for item in summary["amount_top_outflows"][:2]] == ["LARGE_OUT", "SMALL_OUT"]
    assert [item["theme"] for item in summary["amount_theme_inflows"][:2]] == ["低比例大流入", "高比例小流入"]
    assert [item["theme"] for item in summary["amount_theme_outflows"][:2]] == ["低比例大流出", "高比例小流出"]
    assert [item["symbol"] for item in summary["top_inflows"][:2]] == ["SMALL_IN", "LARGE_IN"]
    assert [item["symbol"] for item in summary["top_outflows"][:2]] == ["SMALL_OUT", "LARGE_OUT"]


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
    assert window["direction"] == "暫無資金方向"
    assert not window["top_inflows"]


def test_empty_window_never_claims_bearish_flow():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 3_500)
    window = flow.snapshot(now=3_500)["markets"]["TW"]["windows"]["15m"]

    assert window["trade_count"] == 0
    assert window["net_flow"] == 0
    assert window["direction"] == "暫無資金方向"


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


def test_legacy_tw_unit_state_is_ignored_but_us_state_is_preserved(tmp_path):
    state = tmp_path / "capital-flow.json"
    config = CapitalFlowConfig(persist_interval_seconds=0)
    first = CapitalFlowShadow(baselines(), state_path=state, config=config, clock=lambda: 6_000)
    first.process_trade("0050.TW", price=100, size=100, ask=100, timestamp=5_999)
    first.process_trade("NVDA", price=200, size=100, ask=200, timestamp=5_999)
    first.snapshot(now=6_000)

    payload = json.loads(state.read_text(encoding="utf-8"))
    payload.pop("tw_value_unit_version")
    state.write_text(json.dumps(payload), encoding="utf-8")

    restored = CapitalFlowShadow(baselines(), state_path=state, config=config, clock=lambda: 6_001)
    snapshot = restored.snapshot(now=6_001)
    assert snapshot["markets"]["TW"]["trades_processed"] == 0
    assert snapshot["markets"]["TW"]["windows"]["1m"]["trade_count"] == 0
    assert snapshot["markets"]["US"]["trades_processed"] == 1
    assert snapshot["markets"]["US"]["windows"]["1m"]["buy_value"] == 20_000


def test_baseline_loader_keeps_theme_and_asset_type(tmp_path):
    report = tmp_path / "all_analysis.json"
    report.write_text(json.dumps({"data": [{
        "symbol": "0050.TW", "name": "台灣50", "market": "TW", "price": 100,
        "avg_volume20": 1000, "theme": "大盤ETF", "type": "ETF",
    }]}), encoding="utf-8")
    result = load_stock_baselines(report)["0050.TW"]
    assert result.theme == "大盤ETF"
    assert result.asset_type == "ETF"


def test_daily_flow_is_hidden_until_close_and_requires_full_session_coverage():
    rows = {
        f"{2000 + index}.TW": StockBaseline(
            f"{2000 + index}.TW", f"測試{index}", "TW", 100, 10_000,
            "測試族群", "個股",
        )
        for index in range(10)
    }
    zone = ZoneInfo("Asia/Taipei")
    opened = datetime(2026, 8, 31, 9, 5, tzinfo=zone).timestamp()
    late = datetime(2026, 8, 31, 13, 10, tzinfo=zone).timestamp()
    closed = datetime(2026, 8, 31, 13, 31, tzinfo=zone).timestamp()
    flow = CapitalFlowShadow(rows, clock=lambda: closed)
    for index, symbol in enumerate(rows):
        flow.process_trade(symbol, price=100, size=100, ask=100, timestamp=opened + index)
    flow.process_trade(next(iter(rows)), price=101, size=100, ask=101, timestamp=late)

    assert flow.closed_daily_snapshots(now=late)["markets"]["TW"] == []
    daily = flow.closed_daily_snapshots(now=closed)["markets"]["TW"][0]
    assert daily["session_date"] == "2026-08-31"
    assert daily["complete"] is True
    assert daily["quality"]["opening_coverage"] is True
    assert daily["quality"]["closing_coverage"] is True
    assert daily["active_symbols"] == 10
    assert daily["session_scope"] == "regular_hours_only"
    assert len(daily["symbol_flows"]) == 10
    assert set(daily["symbol_flows"][0]) == {
        "symbol", "buy_value", "sell_value", "net_flow",
    }
    assert flow.closed_daily_snapshots(now=closed)["policy"] == {
        "intraday_exposed": False,
        "regular_hours_only": True,
        "markets_separate": True,
        "symbol_summaries_only": True,
        "raw_trades_exposed": False,
        "formal_ranking_locked": True,
        "places_orders": False,
    }


def test_owner_snapshot_exposes_today_regular_session_before_and_after_close():
    zone = ZoneInfo("Asia/Taipei")
    active = datetime(2026, 8, 31, 10, 0, tzinfo=zone).timestamp()
    closed = datetime(2026, 8, 31, 13, 31, tzinfo=zone).timestamp()
    flow = CapitalFlowShadow(baselines(), clock=lambda: active)
    flow.process_trade("2330.TW", price=1000, size=100, ask=1000, timestamp=active)

    during = flow.snapshot(now=active)["markets"]["TW"]["session"]
    after = flow.snapshot(now=closed)["markets"]["TW"]["session"]
    assert during["session_date"] == "2026-08-31"
    assert during["session_scope"] == "regular_hours_only"
    assert during["closed"] is False
    assert during["trade_count"] == 1
    assert during["top_inflows"][0]["symbol"] == "2330.TW"
    assert after["closed"] is True
    assert after["trade_count"] == 1


def test_daily_flow_survives_restart_without_exposing_intraday(tmp_path):
    state = tmp_path / "capital-flow.json"
    zone = ZoneInfo("America/New_York")
    trade_at = datetime(2026, 8, 31, 10, 0, tzinfo=zone).timestamp()
    close_at = datetime(2026, 8, 31, 16, 1, tzinfo=zone).timestamp()
    config = CapitalFlowConfig(persist_interval_seconds=0)
    first = CapitalFlowShadow(baselines(), state_path=state, config=config, clock=lambda: close_at)
    first.process_trade("NVDA", price=200, size=100, ask=200, timestamp=trade_at)
    first.snapshot(now=close_at)

    restored = CapitalFlowShadow(baselines(), state_path=state, config=config, clock=lambda: close_at)
    payload = restored.closed_daily_snapshots(now=close_at)
    assert payload["policy"]["intraday_exposed"] is False
    assert payload["markets"]["US"][0]["trade_count"] == 1
    assert payload["markets"]["US"][0]["complete"] is False


def test_us_regular_open_resets_rolling_windows_and_preserves_premarket_summary():
    zone = ZoneInfo("America/New_York")
    premarket = datetime(2026, 8, 31, 9, 20, tzinfo=zone).timestamp()
    regular = datetime(2026, 8, 31, 9, 30, tzinfo=zone).timestamp()
    flow = CapitalFlowShadow(baselines(), clock=lambda: regular)

    flow.transition_market_phase("US", "premarket", now=premarket)
    flow.process_trade("NVDA", price=200, size=1_000, ask=200, timestamp=premarket)
    before = flow.snapshot(now=premarket)["markets"]["US"]
    assert before["session_phase"] == "premarket"
    assert before["windows"]["60m"]["buy_value"] == 200_000

    flow.transition_market_phase("US", "regular", now=regular)
    after = flow.snapshot(now=regular)["markets"]["US"]
    assert after["session_phase"] == "regular"
    assert after["windows"]["60m"]["trade_count"] == 0
    assert after["premarket_summary"]["closed"] is True
    assert after["premarket_summary"]["windows"]["60m"]["buy_value"] == 200_000

    flow.process_trade("NVDA", price=201, size=100, ask=201, timestamp=regular + 1)
    regular_window = flow.snapshot(now=regular + 1)["markets"]["US"]["windows"]["1m"]
    assert regular_window["buy_value"] == 20_100

    next_day = datetime(2026, 9, 1, 4, 0, tzinfo=zone).timestamp()
    flow.transition_market_phase("US", "closed", now=regular + 60)
    flow.transition_market_phase("US", "premarket", now=next_day)
    assert flow.snapshot(now=next_day)["markets"]["US"]["premarket_summary"] is None


def test_sixty_minute_flow_keeps_only_rolling_buckets():
    flow = CapitalFlowShadow(baselines(), clock=lambda: 4_001)
    flow.process_trade("NVDA", price=200, size=100, ask=200, timestamp=100)
    flow.process_trade("NVDA", price=201, size=100, ask=201, timestamp=4_000)
    window = flow.snapshot(now=4_001)["markets"]["US"]["windows"]["60m"]
    assert window["trade_count"] == 1
    assert window["buy_value"] == 20_100
