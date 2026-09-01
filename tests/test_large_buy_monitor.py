from pathlib import Path

from large_buy_monitor import (
    JsonAlertStore,
    LargeBuyAlertService,
    LargeBuyConfig,
    LargeBuyDetector,
    StockBaseline,
    format_large_buy_telegram,
)
from capital_flow_shadow import CapitalFlowShadow


def baselines():
    return {
        "2330.TW": StockBaseline("2330.TW", "台積電", "TW", 1000, 10_000_000),
        "NVDA": StockBaseline("NVDA", "NVIDIA", "US", 200, 100_000_000),
    }


def config():
    return LargeBuyConfig(
        single_min_twd=500_000,
        major_single_min_twd=3_000_000,
        cluster_min_twd=5_000_000,
        single_min_usd=None,
        cluster_min_usd=250_000,
        block_single_min_twd=10_000_000,
        block_single_min_usd=1_000_000,
        single_daily_value_ratio=0,
        cluster_daily_value_ratio=0,
        cooldown_seconds=180,
    )


def test_one_large_aggressive_buy_triggers_immediately():
    detector = LargeBuyDetector(baselines(), config=config())
    alert = detector.process_trade(
        "2330.TW", price=1000, size=3000, bid=999, ask=1000, timestamp=100
    )
    assert alert["trigger_type"] == "single"
    assert alert["trade_count"] == 1
    assert alert["buy_value"] == 3_000_000
    assert alert["is_block_trade"] is False
    assert alert["threshold_level"] == "major"
    assert alert["trigger_label"] == "300萬級單筆大買"


def test_tw_500k_single_trade_is_the_fixed_general_floor():
    detector = LargeBuyDetector(baselines(), config=config())
    assert detector.process_trade(
        "2330.TW", price=1000, size=499, bid=999, ask=1000, timestamp=100
    ) is None
    alert = detector.process_trade(
        "2330.TW", price=1000, size=500, bid=999, ask=1000, timestamp=101
    )
    assert alert["buy_value"] == 500_000
    assert alert["threshold_level"] == "general"
    assert alert["threshold_level_label"] == "50萬級"


def test_higher_tw_tier_bypasses_lower_tier_cooldown_without_duplicate_spam():
    detector = LargeBuyDetector(baselines(), config=config())
    general = detector.process_trade(
        "2330.TW", price=1000, size=500, ask=1000, timestamp=100
    )
    assert general["threshold_level"] == "general"
    assert detector.process_trade(
        "2330.TW", price=1000, size=600, ask=1000, timestamp=105
    ) is None
    major = detector.process_trade(
        "2330.TW", price=1000, size=3000, ask=1000, timestamp=110
    )
    assert major["threshold_level"] == "major"
    block = detector.process_trade(
        "2330.TW", price=1000, size=10_000, ask=1000, timestamp=115
    )
    assert block["threshold_level"] == "block"


def test_single_block_trade_is_flagged_without_replacing_existing_alerts():
    detector = LargeBuyDetector(baselines(), config=config())
    tw = detector.process_trade(
        "2330.TW", price=1000, size=10_000, bid=999, ask=1000, timestamp=100
    )
    us = detector.process_trade(
        "NVDA", price=200, size=5000, bid=199.9, ask=200, timestamp=101
    )
    assert tw["trigger_type"] == "single"
    assert tw["is_block_trade"] is True
    assert tw["block_trade_threshold"] == 10_000_000
    assert tw["block_trade_label"] == "1,000萬級單筆巨額大買"
    assert us["trigger_type"] == "single"
    assert us["is_block_trade"] is True
    assert us["block_trade_threshold"] == 1_000_000


def test_us_general_single_100k_is_removed_but_one_million_block_remains():
    detector = LargeBuyDetector(baselines(), config=config())
    assert detector.process_trade(
        "NVDA", price=200, size=500, ask=200, timestamp=100
    ) is None
    assert detector.process_trade(
        "NVDA", price=200, size=4999, ask=200, timestamp=120
    ) is None
    alert = detector.process_trade(
        "NVDA", price=200, size=5000, ask=200, timestamp=140
    )
    assert alert["trigger_type"] == "single"
    assert alert["is_block_trade"] is True
    assert alert["general_single_threshold"] is None
    assert alert["block_trade_threshold"] == 1_000_000


def test_us_three_trade_cluster_rule_is_unchanged():
    detector = LargeBuyDetector(baselines(), config=config())
    assert detector.process_trade("NVDA", price=200, size=450, ask=200, timestamp=100) is None
    assert detector.process_trade("NVDA", price=200, size=450, ask=200, timestamp=103) is None
    alert = detector.process_trade("NVDA", price=200, size=450, ask=200, timestamp=108)
    assert alert["trigger_type"] == "cluster"
    assert alert["trade_count"] == 3
    assert alert["buy_value"] == 270_000


def test_three_to_five_aggressive_buys_trigger_inside_ten_seconds():
    detector = LargeBuyDetector(baselines(), config=config())
    first = detector.process_trade("2330.TW", price=1000, size=1800, ask=1000, timestamp=100)
    assert first["threshold_level"] == "general"
    assert detector.process_trade("2330.TW", price=1000, size=1800, ask=1000, timestamp=103) is None
    alert = detector.process_trade("2330.TW", price=1000, size=1800, ask=1000, timestamp=108)
    assert alert["trigger_type"] == "cluster"
    assert alert["trade_count"] == 3
    assert alert["buy_value"] == 5_400_000
    assert alert["aggressive_buy_ratio_pct"] == 100


def test_cluster_requires_seventy_percent_aggressive_buy_value():
    detector = LargeBuyDetector(baselines(), config=config())
    detector.process_trade("2330.TW", price=999, size=5000, bid=999, ask=1000, timestamp=100)
    detector.process_trade("2330.TW", price=1000, size=1800, bid=999, ask=1000, timestamp=101)
    detector.process_trade("2330.TW", price=1000, size=1800, bid=999, ask=1000, timestamp=102)
    assert detector.process_trade("2330.TW", price=1000, size=1800, bid=999, ask=1000, timestamp=103) is None


def test_old_prints_expire_and_cooldown_prevents_duplicate_alerts():
    detector = LargeBuyDetector(baselines(), config=config())
    first = detector.process_trade("NVDA", price=200, size=5000, ask=200, timestamp=100)
    assert first["trigger_type"] == "single"
    assert detector.process_trade("NVDA", price=200, size=5000, ask=200, timestamp=105) is None
    again = detector.process_trade("NVDA", price=200, size=5000, ask=200, timestamp=281)
    assert again["trigger_type"] == "single"


def test_trade_at_bid_is_not_mislabelled_as_buy():
    detector = LargeBuyDetector(baselines(), config=config())
    alert = detector.process_trade(
        "2330.TW", price=999, size=10_000, bid=999, ask=1000, timestamp=100
    )
    assert alert["alert_side"] == "sell"
    assert alert["trigger_label"] == "300萬級單筆大賣"
    assert alert["sell_value"] == 9_990_000
    assert alert["aggressive_sell_ratio_pct"] == 100


def test_buy_and_sell_have_independent_cooldowns():
    detector = LargeBuyDetector(baselines(), config=config())
    buy = detector.process_trade(
        "2330.TW", price=1000, size=3000, bid=999, ask=1000, timestamp=100
    )
    sell = detector.process_trade(
        "2330.TW", price=999, size=4000, bid=999, ask=1000, timestamp=101
    )
    assert buy["alert_side"] == "buy"
    assert sell["alert_side"] == "sell"


def test_special_condition_trade_does_not_trigger_large_buy_alert():
    detector = LargeBuyDetector(baselines(), config=config())
    assert detector.process_trade(
        "NVDA", price=200, size=1_000_000, ask=200, conditions=["W"], timestamp=100
    ) is None


def test_json_store_persists_sequence_and_filters(tmp_path: Path):
    path = tmp_path / "alerts.json"
    store = JsonAlertStore(path)
    one = store.append({"symbol": "2330.TW", "detected_at": "one"})
    two = store.append({"symbol": "NVDA", "detected_at": "two"})
    assert one["sequence"] == 1
    assert two["sequence"] == 2
    restored = JsonAlertStore(path)
    assert restored.latest_sequence == 2
    assert [row["symbol"] for row in restored.list_after(1)] == ["NVDA"]


def test_service_stores_and_notifies_without_broker_actions(tmp_path: Path):
    messages = []
    service = object.__new__(LargeBuyAlertService)
    service.config = config()
    service.baselines = baselines()
    service.detector = LargeBuyDetector(service.baselines, config=service.config)
    service.flow = CapitalFlowShadow(service.baselines, clock=lambda: 100)
    service.store = JsonAlertStore(tmp_path / "alerts.json")
    service.notifier = messages.append
    service._status = {market: {"state": "waiting", "subscribed": 0, "error": None} for market in ("TW", "US")}
    import threading
    service._lock = threading.RLock()

    alert = service.process_trade("2330.TW", price=1000, size=3000, ask=1000, timestamp=100)
    assert alert["sequence"] == 1
    assert "單筆大買" in messages[0]
    snapshot = service.snapshot()
    assert snapshot["policy"]["broker_orders"] is False
    assert snapshot["universe"] == {"TW": 1, "US": 1}
    assert snapshot["capital_flow"]["policy"]["markets_separate"] is True
    assert snapshot["capital_flow"]["markets"]["TW"]["windows"]["1m"]["trade_count"] == 1
    assert snapshot["capital_flow"]["markets"]["US"]["windows"]["1m"]["trade_count"] == 0
    assert snapshot["inverse_etf_live_shadow"]["policy"]["formal_ranking_locked"] is True
    assert snapshot["inverse_etf_live_shadow"]["policy"]["broker_orders"] is False
    resources = snapshot["policy"]["resource_policy"]
    assert resources["alpaca_shared_websocket"] is True
    assert resources["raw_trade_window_seconds"] == 10
    assert resources["aggregate_retention_minutes"] == 60
    assert resources["alerts_keep_summary_only"] is True


def test_notifications_cover_every_stock_without_selected_symbol_filter(tmp_path: Path):
    received = []
    service = object.__new__(LargeBuyAlertService)
    service.config = config()
    service.baselines = baselines()
    service.detector = LargeBuyDetector(service.baselines, config=service.config)
    service.flow = CapitalFlowShadow(service.baselines, clock=lambda: 100)
    service.store = JsonAlertStore(tmp_path / "alerts.json")
    service.notifier = None
    service.alert_notifier = received.append
    service._status = {market: {"state": "waiting", "subscribed": 0, "error": None} for market in ("TW", "US")}
    import threading
    service._lock = threading.RLock()

    service.process_trade("2330.TW", price=1000, size=3000, ask=1000, timestamp=100)
    service.process_trade("NVDA", price=200, size=5000, ask=200, timestamp=101)

    assert [row["symbol"] for row in received] == ["2330.TW", "NVDA"]
    snapshot = service.snapshot()
    assert snapshot["policy"]["notification_scope"] == "all_site_stocks"
    assert snapshot["policy"]["selected_symbol_filter"] is False
    assert snapshot["policy"]["general_single_thresholds"] == {"TW": 500_000, "US": None}
    assert snapshot["policy"]["major_single_thresholds"] == {"TW": 3_000_000, "US": None}
    assert snapshot["policy"]["block_single_thresholds"] == {"TW": 10_000_000, "US": 1_000_000}
    assert snapshot["policy"]["single_threshold_tiers"] == {
        "TW": [500_000, 3_000_000, 10_000_000],
        "US": [1_000_000],
    }


def test_telegram_text_states_information_only():
    text = format_large_buy_telegram({
        "trigger_label": "3筆連續大買", "name": "台積電", "symbol": "2330.TW",
        "market": "TW", "price": 1000, "trade_count": 3, "buy_value": 5_000_000,
        "aggressive_buy_ratio_pct": 80, "detected_at": "now",
    })
    assert "10秒大量主動買進" in text
    assert "不代表主力身分或交易建議" in text


def test_telegram_formats_large_sell_alert():
    text = format_large_buy_telegram({
        "alert_side": "sell", "trigger_label": "單筆大賣", "name": "聯電",
        "symbol": "2303.TW", "market": "TW", "price": 50, "trade_count": 1,
        "sell_value": 6_000_000, "aggressive_sell_ratio_pct": 91, "detected_at": "now",
    })
    assert "大量主動賣出" in text
    assert "賣出占比 91.0%" in text
    assert "不代表主力身分或交易建議" in text


def test_telegram_identifies_us_premarket_alert():
    text = format_large_buy_telegram({
        "alert_side": "buy", "trigger_label": "單筆大買", "name": "NVIDIA",
        "symbol": "NVDA", "market": "US", "price": 200, "trade_count": 1,
        "buy_value": 250_000, "aggressive_buy_ratio_pct": 100,
        "session_phase": "premarket", "detected_at": "now",
    })
    assert "交易時段｜盤前" in text


def test_us_premarket_notifies_but_never_updates_formal_weight(tmp_path: Path):
    class WeightShadow:
        calendar = None

        def __init__(self):
            self.observed = []
            self.alerts = []

        def observe_trade(self, *args, **kwargs):
            self.observed.append((args, kwargs))

        def record_alert(self, alert):
            self.alerts.append(alert)

    received = []
    zone = __import__("zoneinfo").ZoneInfo("America/New_York")
    premarket = __import__("datetime").datetime(2026, 8, 31, 8, 0, tzinfo=zone).timestamp()
    regular = __import__("datetime").datetime(2026, 8, 31, 9, 30, tzinfo=zone).timestamp()
    service = object.__new__(LargeBuyAlertService)
    service.config = config()
    service.baselines = baselines()
    service.detector = LargeBuyDetector(service.baselines, config=service.config)
    service.flow = CapitalFlowShadow(service.baselines, clock=lambda: regular)
    service.weight_shadow = WeightShadow()
    service.store = JsonAlertStore(tmp_path / "alerts.json")
    service.notifier = None
    service.alert_notifier = received.append
    service._session_phases = {"TW": "closed", "US": "closed"}

    alert = service.process_trade("NVDA", price=200, size=5000, ask=200, timestamp=premarket)
    assert alert["session_phase"] == "premarket"
    assert received[0]["symbol"] == "NVDA"
    assert service.weight_shadow.observed == []
    assert service.weight_shadow.alerts == []

    service.process_trade("NVDA", price=200, size=5000, ask=200, timestamp=regular)
    assert len(service.weight_shadow.observed) == 1
    assert len(service.weight_shadow.alerts) == 1


def test_railway_image_keeps_the_full_large_buy_universe(tmp_path: Path):
    ignore_rules = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "reports" not in ignore_rules
    assert "!reports/all_analysis.json" in ignore_rules
    assert "!reports/inverse_etf_database.json" in ignore_rules
    assert "!reports/inverse_etf_shadow.json" in ignore_rules
    service = LargeBuyAlertService(
        Path("reports/all_analysis.json"), tmp_path / "alerts.json", config=config()
    )
    snapshot = service.snapshot()
    assert snapshot["universe"]["TW"] > 0
    assert snapshot["universe"]["US"] > 0
    assert snapshot["universe"]["TW"] + snapshot["universe"]["US"] >= 300
    assert snapshot["inverse_etf_live_shadow"]["mode"] == "isolated_inverse_etf_live_overlay"
    assert snapshot["inverse_etf_live_shadow"]["policy"]["flow_weight_shadow_unchanged"] is True
