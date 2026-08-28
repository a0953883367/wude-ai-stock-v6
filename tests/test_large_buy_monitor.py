from pathlib import Path

from large_buy_monitor import (
    JsonAlertStore,
    LargeBuyAlertService,
    LargeBuyConfig,
    LargeBuyDetector,
    StockBaseline,
    format_large_buy_telegram,
)


def baselines():
    return {
        "2330.TW": StockBaseline("2330.TW", "台積電", "TW", 1000, 10_000_000),
        "NVDA": StockBaseline("NVDA", "NVIDIA", "US", 200, 100_000_000),
    }


def config():
    return LargeBuyConfig(
        single_min_twd=3_000_000,
        cluster_min_twd=5_000_000,
        single_min_usd=100_000,
        cluster_min_usd=250_000,
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


def test_three_to_five_aggressive_buys_trigger_inside_ten_seconds():
    detector = LargeBuyDetector(baselines(), config=config())
    assert detector.process_trade("2330.TW", price=1000, size=1800, ask=1000, timestamp=100) is None
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
    first = detector.process_trade("NVDA", price=200, size=500, ask=200, timestamp=100)
    assert first["trigger_type"] == "single"
    assert detector.process_trade("NVDA", price=200, size=500, ask=200, timestamp=105) is None
    again = detector.process_trade("NVDA", price=200, size=500, ask=200, timestamp=281)
    assert again["trigger_type"] == "single"


def test_trade_at_bid_is_not_mislabelled_as_buy():
    detector = LargeBuyDetector(baselines(), config=config())
    assert detector.process_trade(
        "2330.TW", price=999, size=10_000, bid=999, ask=1000, timestamp=100
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


def test_telegram_text_states_information_only():
    text = format_large_buy_telegram({
        "trigger_label": "3筆連續大買", "name": "台積電", "symbol": "2330.TW",
        "market": "TW", "price": 1000, "trade_count": 3, "buy_value": 5_000_000,
        "aggressive_buy_ratio_pct": 80, "detected_at": "now",
    })
    assert "10秒大量主動買進" in text
    assert "不代表主力身分或買進建議" in text


def test_railway_image_keeps_the_full_large_buy_universe(tmp_path: Path):
    ignore_rules = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "reports" not in ignore_rules
    assert "!reports/all_analysis.json" in ignore_rules
    service = LargeBuyAlertService(
        Path("reports/all_analysis.json"), tmp_path / "alerts.json", config=config()
    )
    snapshot = service.snapshot()
    assert snapshot["universe"]["TW"] > 0
    assert snapshot["universe"]["US"] > 0
    assert snapshot["universe"]["TW"] + snapshot["universe"]["US"] >= 300
