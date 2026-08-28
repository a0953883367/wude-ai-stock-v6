import json
from pathlib import Path

import pytest

from web_push import WebPushService


def subscription(endpoint="https://push.example.test/device-1"):
    return {"endpoint": endpoint, "keys": {"p256dh": "public-key", "auth": "auth-key"}}


def test_generates_persistent_public_key(tmp_path: Path):
    state = tmp_path / "subscriptions.json"
    key = tmp_path / "private.pem"
    first = WebPushService(state, key)
    second = WebPushService(state, key)
    assert len(first.public_key) == 87
    assert second.public_key == first.public_key
    assert key.stat().st_mode & 0o777 == 0o600


def test_subscription_is_validated_deduplicated_and_persisted(tmp_path: Path):
    state = tmp_path / "subscriptions.json"
    key = tmp_path / "private.pem"
    service = WebPushService(state, key)
    assert service.subscribe(subscription()) == 1
    assert service.subscribe(subscription()) == 1
    restored = WebPushService(state, key)
    assert restored.subscription_count == 1
    assert json.loads(state.read_text())["subscriptions"][0]["endpoint"].startswith("https://")
    with pytest.raises(ValueError):
        service.subscribe({"endpoint": "http://unsafe", "keys": {}})


def test_push_payload_names_stock_and_opens_matching_symbol(tmp_path: Path):
    service = WebPushService(tmp_path / "subscriptions.json", tmp_path / "private.pem")
    payload = service.payload({
        "name": "NVIDIA", "symbol": "NVDA", "market": "US", "trigger_label": "3筆連續大買",
        "buy_value": 320_000, "price": 201.5, "aggressive_buy_ratio_pct": 82.4,
    })
    assert payload["title"] == "🚨 NVIDIA・NVDA"
    assert "US$320,000" in payload["body"]
    assert payload["url"] == "/live?symbol=NVDA&market=US"
