from briefing import _tw_intraday_enrichment


def test_intraday_snapshot_reuses_enrichment_without_stale_price_fields():
    previous = {
        "6116.TW": {
            "symbol": "6116.TW",
            "market": "TW",
            "price": 14.4,
            "rsi": 65,
            "available": 1.0,
            "foreign": -2_292_868,
            "institution_5d": -4_000_000,
            "credit_available": 1.0,
            "margin_1d_change": -387,
            "fundamental_available": 1.0,
            "per": 12.5,
            "revenue_yoy_pct": 7.2,
            "broker_available": True,
            "top_brokers_buy": [{"name": "A", "net": 10}],
        },
        "AAPL": {"symbol": "AAPL", "market": "US", "foreign": 999},
    }

    institutions, credit, fundamentals, brokers = _tw_intraday_enrichment(previous)

    assert institutions["6116"]["foreign"] == -2_292_868
    assert institutions["6116"]["institution_5d"] == -4_000_000
    assert credit["6116"]["margin_1d_change"] == -387
    assert fundamentals["6116"]["per"] == 12.5
    assert fundamentals["6116"]["revenue_yoy_pct"] == 7.2
    assert brokers["6116"]["broker_available"] is True
    assert "price" not in institutions["6116"]
    assert "rsi" not in institutions["6116"]
    assert "AAPL" not in institutions
