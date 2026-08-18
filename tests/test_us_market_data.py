from types import SimpleNamespace

from us_market_data import (
    fetch_us_sip_snapshots,
    normalize_option_chain,
    normalize_sip_snapshot,
)


def test_normalize_sip_snapshot_builds_nbbo_and_vwap_signals():
    result = normalize_sip_snapshot({
        "latestTrade": {"p": 183},
        "latestQuote": {"bp": 182.9, "ap": 183.1, "bs": 300, "as": 100},
        "dailyBar": {"c": 183, "v": 2_000_000, "vw": 180},
        "prevDailyBar": {"v": 3_000_000},
    })

    assert result["us_live_source"] == "Alpaca SIP"
    assert result["us_live_price"] == 183
    assert result["us_live_quote_imbalance_pct"] == 50
    assert result["us_live_vwap_distance_pct"] == 1.667
    assert result["us_live_spread_pct"] > 0


def test_normalize_opra_chain_uses_near_money_iv_and_skew():
    result = normalize_option_chain({"snapshots": {
        "NVDA260925C00180000": {"impliedVolatility": 0.42},
        "NVDA260925P00180000": {"impliedVolatility": 0.52},
        "NVDA260925C00250000": {"impliedVolatility": 2.0},
    }}, 180)

    assert result["us_option_source"] == "OPRA"
    assert result["us_option_contract_count"] == 2
    assert result["us_option_iv_pct"] == 47
    assert result["us_option_put_call_iv_skew_pct"] == 10
    assert result["us_option_safety_score"] < 65


def test_sip_fetch_is_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert fetch_us_sip_snapshots({"NVDA"}) == {}
