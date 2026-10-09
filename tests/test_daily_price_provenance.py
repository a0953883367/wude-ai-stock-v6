from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from daily_price_provenance import (
    attest_yahoo_daily, observed_daily_volume, tw_official_daily_metadata,
    us_daily_metadata,
)
from strategy import build_features, _promote_completed_us_intraday_session


def history():
    return pd.DataFrame({
        "open": [100.] * 30, "high": [103.] * 30, "low": [99.] * 30,
        "close": [102.] * 30, "volume": [1200.] * 30,
    }, index=pd.bdate_range(end="2026-08-27", periods=30))


def features(frame, intraday=None, item=None):
    return build_features(item or {"symbol": "AAPL", "market": "US"}, frame, intraday, None)


def test_daily_download_attests_batch_and_individual_retry(monkeypatch):
    from data_fetcher import download_history

    def download(*, tickers, interval, **kwargs):
        assert interval == "1d"
        return pd.concat({"AAPL": history()}, axis=1) if isinstance(tickers, list) else history()

    monkeypatch.setattr("data_fetcher.yf.download", download)
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    downloaded = download_history(["AAPL", "MSFT"])
    for symbol, frame in downloaded.items():
        row = features(frame, item={"symbol": symbol, "market": "US"})
        assert row["us_daily_source"] == "Yahoo Finance daily bars"
        assert row["us_daily_session_date"] == "2026-08-27"
        assert row["us_daily_price_unit"] == "USD/shares"
        assert row["us_daily_price_available"] is True
        assert row["source_daily_ohlcv_complete"] is True
        assert row["source_daily_ohlcv_session_date"] == row["official_session_date"]
        assert row["official_volume"] == 1200


def test_stale_retry_attests_replacement_not_old_session(monkeypatch):
    from data_fetcher import _retry_stale_us_daily_history

    current = history()
    old = history().iloc[:-1]
    symbols = [f"US{i}" for i in range(10)]
    frames = {symbol: current.copy() if i < 9 else old for i, symbol in enumerate(symbols)}
    monkeypatch.setattr("data_fetcher.yf.download", lambda **_: current.copy())
    monkeypatch.setattr("data_fetcher.time.sleep", lambda _: None)
    assert _retry_stale_us_daily_history(frames, symbols, now=datetime(
        2026, 8, 28, 16, 10, tzinfo=ZoneInfo("America/New_York"))) == ["US9"]
    assert us_daily_metadata(frames["US9"], "US9")["us_daily_session_date"] == "2026-08-27"


def test_synthetic_or_live_metadata_does_not_attest_daily_series():
    row = features(history(), item={
        "symbol": "AAPL", "market": "US", "us_live_source": "Alpaca SIP",
        "us_live_data_available": True, "us_daily_price_available": True,
        "us_daily_source": "Alpaca SIP daily bars",
    })
    assert row["us_daily_price_available"] is False
    assert row["source_daily_ohlcv_complete"] is False
    assert row["us_daily_source"] is None


@pytest.mark.parametrize("column", ["open", "high", "low", "volume"])
def test_missing_original_ohlcv_cannot_be_filled_into_attestation(column):
    frame = history().drop(columns=column)
    row = features(attest_yahoo_daily(frame, "AAPL"))
    assert row["us_daily_price_available"] is False
    assert row["source_daily_ohlcv_complete"] is False
    if column == "volume":
        assert row["official_volume"] is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, None])
def test_missing_or_invalid_volume_is_not_zero(value):
    frame = history()
    frame.loc[frame.index[-1], "volume"] = value
    assert observed_daily_volume(frame) is None
    row = features(attest_yahoo_daily(frame, "AAPL"))
    assert row["official_volume"] is None
    assert row["us_daily_price_available"] is False


def test_real_zero_volume_is_preserved():
    frame = history()
    frame.loc[frame.index[-1], "volume"] = 0
    row = features(attest_yahoo_daily(frame, "AAPL"))
    assert row["official_volume"] == 0
    assert row["us_daily_price_available"] is True


def test_changed_bar_or_symbol_cannot_reuse_attestation():
    frame = attest_yahoo_daily(history(), "AAPL")
    assert us_daily_metadata(frame, "MSFT")["us_daily_price_available"] is False
    frame.loc[frame.index[-1], "close"] = 101
    assert features(frame)["us_daily_price_available"] is False


def test_attestation_does_not_change_existing_feature_values():
    original = features(history())
    attested = features(attest_yahoo_daily(history(), "AAPL"))
    added = {"us_daily_source", "us_daily_session_date", "us_daily_price_available",
             "us_daily_price_unit", "source_daily_ohlcv_complete", "source_daily_ohlcv_session_date"}
    assert {key: value for key, value in original.items() if key not in added} == {
        key: value for key, value in attested.items() if key not in added}


def test_boolean_values_cannot_attest_prices_or_share_volume():
    for column in ("open", "volume"):
        frame = history()
        frame[column] = True
        assert us_daily_metadata(attest_yahoo_daily(frame, "AAPL"), "AAPL")["us_daily_price_available"] is False
    assert observed_daily_volume(frame) is None


def test_completed_intraday_promotion_never_inherits_daily_source(monkeypatch):
    frame = attest_yahoo_daily(history(), "AAPL")
    intraday = pd.DataFrame({
        "open": [104.] * 78, "high": [106.] * 78, "low": [103.] * 78,
        "close": [105.] * 78, "volume": [100.] * 78,
    }, index=pd.date_range("2026-08-28 09:30", periods=78, freq="5min", tz="America/New_York"))
    monkeypatch.setattr("strategy._promote_completed_us_intraday_session", lambda daily, live:
        _promote_completed_us_intraday_session(daily, live, now=datetime(
            2026, 8, 28, 16, 10, tzinfo=ZoneInfo("America/New_York"))))
    row = features(frame, intraday)
    assert row["official_session_date"] == "2026-08-28"
    assert row["us_daily_price_available"] is False
    assert row["source_daily_ohlcv_complete"] is False
    assert row["official_volume"] is None


@pytest.mark.parametrize("symbol", ["2330.TW", "3718.TWO", "SHOP.TO", "^GSPC", "TWD=X"])
def test_non_us_symbols_do_not_acquire_us_currency_attestation(symbol):
    assert us_daily_metadata(attest_yahoo_daily(history(), symbol), symbol)["us_daily_price_available"] is False


def tw_snapshot():
    return {"open": 100., "high": 103., "low": 99., "close": 102., "volume": 1200.,
            "date": "2026-08-27", "tw_official_price_available": True,
            "tw_price_source": "TWSE OpenAPI", "tw_price_unit": "TWD/shares"}


def test_tw_complete_snapshot_matches_actual_overlaid_bar():
    result = tw_official_daily_metadata(history(), tw_snapshot())
    assert result == {"source_daily_ohlcv_complete": True,
                      "source_daily_ohlcv_session_date": "2026-08-27"}


@pytest.mark.parametrize("change", [{"volume": None}, {"open": None}, {"date": "2026-08-26"},
                                     {"close": 101}, {"tw_price_unit": "TWD/lots"}])
def test_tw_partial_mixed_or_stale_overlay_cannot_attest_complete_source(change):
    result = tw_official_daily_metadata(history(), {**tw_snapshot(), **change})
    assert result["source_daily_ohlcv_complete"] is False
