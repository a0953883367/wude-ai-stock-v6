from tools.publish_friend_data import sanitize, sanitize_accuracy


def test_friend_output_keeps_public_ranking_but_excludes_private_fields():
    row = {
        "symbol": "2330.TW", "name": "台灣公司", "market": "TW",
        "type": "個股", "theme": "半導體", "industry": "半導體",
        "score": 72.5, "entry_score": 64.0, "price": 1000,
        "overall_display_rank": 3, "overall_rank": 2,
        "overall_ranking_score": 61.2, "overall_rank_tier": 2,
        "overall_eligible": True, "action": "觀察", "risk": "一般波動",
        "buy_zone_low": 980, "buy_zone_high": 990,
        "support1": 970, "resistance1": 1030,
        "account": "private-account", "inventory": ["secret"],
        "api_key": "secret", "certificate": "secret",
    }
    result = sanitize(row)
    assert result["rank"] == 3
    assert result["qualifiedRank"] == 2
    assert result["score"] == 72.5
    assert result["timing"] == 64.0
    assert not ({"account", "inventory", "api_key", "certificate"} & result.keys())


def test_friend_accuracy_only_contains_aggregate_metrics():
    value = {
        "methodology_version": 5,
        "updated_at": "2026-08-22 20:00:00",
        "immutable_rule": "固定後不覆寫",
        "integrity": {"verified": 1},
        "calibration": {"trading_days_collected": 2},
        "groups": {
            "TW_STOCK": {
                "tracks": {"full_day": {"samples": 3}},
                "top_k": {"5": {"tracks": {"full_day": {"samples": 2}}}},
                "models": {"private": "omit"},
            }
        },
        "recent": [{"symbol": "2330.TW", "prediction": "private-row"}],
    }
    result = sanitize_accuracy(value)
    assert result["groups"]["TW_STOCK"]["tracks"]["full_day"]["samples"] == 3
    assert "recent" not in result
    assert "models" not in result["groups"]["TW_STOCK"]
