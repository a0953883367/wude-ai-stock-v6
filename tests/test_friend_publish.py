import json

from tools.publish_friend_data import sanitize, sanitize_accuracy, sanitize_rotation


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
        "_industry_lifecycle": {"stage": "成長期", "source": "owner-only"},
    }
    result = sanitize(row)
    assert result["rank"] == 3
    assert result["qualifiedRank"] == 2
    assert result["score"] == 72.5
    assert result["timing"] == 64.0
    assert not ({"account", "inventory", "api_key", "certificate"} & result.keys())
    assert "industry_lifecycle" not in json.dumps(result, ensure_ascii=False)


def test_friend_accuracy_is_always_cleared():
    value = {
        "methodology_version": 5,
        "updated_at": "2026-08-22 20:00:00",
        "immutable_rule": "固定後不覆寫",
        "integrity": {"verified": 1},
        "calibration": {"trading_days_collected": 2},
        "tw_threshold_calibration": {"mode": "shadow_only", "cohorts": {}},
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
    assert result == {}
    assert "private-row" not in json.dumps(result, ensure_ascii=False)


def test_friend_rotation_only_contains_sector_summary():
    secret = "MUST_NOT_LEAK_OWNER_MODEL"
    value = {
        "updated_at": "2026-08-27 20:00:00",
        "policy": {"model_formula": secret},
        "markets": {
            "TW": {
                "pending": {"models": {"baseline": {"picks": [{"symbol": secret}]}}},
                "outcomes": [{"net_profit_twd": 12345, "owner": secret}],
                "summary": {"rotation_net_profit_twd": 99999},
                "snapshots": [{
                    "session_date": "2026-08-27",
                    "market_state_label": "多頭擴散",
                    "stock_count": 147,
                    "market_breadth_up_pct": 62.5,
                    "hot_sector_count": 2,
                    "integrity_sha256": secret,
                    "sectors": [{
                        "industry": "半導體", "stage": "expansion",
                        "eligible": True, "member_count": 8,
                        "up_ratio_pct": 75.0, "median_change_pct": 2.1,
                        "median_volume_ratio": 1.6,
                        "rotation_score": 88.8,
                        "market_specific_evidence_score": 95,
                        "strict_picks": [secret],
                    }],
                }],
            },
        },
        "account": secret,
        "broker_token": secret,
    }
    result = sanitize_rotation(value)
    encoded = json.dumps(result, ensure_ascii=False)
    tw = result["markets"]["TW"]
    sector = tw["sectors"][0]
    assert tw["marketState"] == "多頭擴散"
    assert sector == {
        "industry": "半導體", "stage": "expansion", "memberCount": 8,
        "upRatioPct": 75.0, "medianChangePct": 2.1, "volumeRatio": 1.6,
    }
    assert secret not in encoded
    assert "rotation_score" not in encoded
    assert "pending" not in encoded
    assert "outcomes" not in encoded
    assert "net_profit" not in encoded
