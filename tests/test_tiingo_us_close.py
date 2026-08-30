from datetime import datetime
from zoneinfo import ZoneInfo

from tiingo_us_close import (
    parse_tiingo_eod,
    update_tiingo_us_close_fallback,
)


def _price(session_date="2026-08-28", close=513.53):
    return [{
        "date": f"{session_date}T00:00:00.000Z",
        "open": close - 2,
        "high": close + 1,
        "low": close - 3,
        "close": close,
        "volume": 123456,
        "adjClose": close,
    }]


def test_parse_tiingo_eod_keeps_only_raw_close_and_date():
    result = parse_tiingo_eod(_price(), "MSFT")

    assert result["official_session_date"] == "2026-08-28"
    assert result["official_close_price"] == 513.53
    assert result["tiingo_close_only"] is True
    assert result["close_only_fallback"] is True
    assert "official_open_price" not in result


def test_us_regular_session_never_requests_tiingo(tmp_path):
    called = False

    def fail_fetch(_symbol):
        nonlocal called
        called = True
        raise AssertionError("美股盤中不得抓取 Tiingo")

    result = update_tiingo_us_close_fallback(
        tmp_path,
        updated_at="2026-08-31 23:00:00",
        requested_symbols={"MSFT"},
        now=datetime(2026, 8, 31, 14, 0, tzinfo=ZoneInfo("America/New_York")),
        api_token="test",
        fetcher=fail_fetch,
    )

    assert called is False
    assert result["status"] == "waiting_for_us_close"
    assert result["covered_requested_count"] == 0


def test_missing_key_is_nonblocking_and_does_not_request(tmp_path):
    result = update_tiingo_us_close_fallback(
        tmp_path,
        updated_at="2026-08-30 06:00:00",
        requested_symbols={"MSFT"},
        now=datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        api_token="",
    )

    assert result["status"] == "unconfigured"
    assert result["configured"] is False
    assert result["rows"] == {}


def test_free_batches_do_not_promote_until_full_cycle(tmp_path):
    symbols = {"A", "B", "C"}
    first = update_tiingo_us_close_fallback(
        tmp_path,
        updated_at="2026-08-30 06:00:00",
        requested_symbols=symbols,
        max_requests=2,
        now_epoch=1000,
        now=datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        api_token="test",
        fetcher=lambda symbol: _price(close=100 + ord(symbol)),
    )

    assert first["status"] == "collecting"
    assert first["staging_attempted_count"] == 2
    assert not (tmp_path / "tiingo_us_close_fallback.json").exists()

    second = update_tiingo_us_close_fallback(
        tmp_path,
        updated_at="2026-08-30 12:00:00",
        requested_symbols=symbols,
        max_requests=2,
        now_epoch=2000,
        now=datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        api_token="test",
        fetcher=lambda symbol: _price(close=100 + ord(symbol)),
    )

    assert second["status"] == "ok"
    assert second["covered_requested_count"] == 3
    assert second["cache_status"] == "promoted_complete_cycle"
    assert not (tmp_path / "tiingo_us_close_staging.json").exists()


def test_newer_session_resets_older_staging_without_mixing_dates(tmp_path):
    symbols = {"A", "B", "C"}
    update_tiingo_us_close_fallback(
        tmp_path,
        updated_at="2026-08-31 06:00:00",
        requested_symbols=symbols,
        max_requests=1,
        now_epoch=1000,
        now=datetime(2026, 8, 31, 18, 0, tzinfo=ZoneInfo("America/New_York")),
        api_token="test",
        fetcher=lambda _symbol: _price("2026-08-28"),
    )
    second = update_tiingo_us_close_fallback(
        tmp_path,
        updated_at="2026-09-01 06:00:00",
        requested_symbols=symbols,
        max_requests=1,
        now_epoch=2000,
        now=datetime(2026, 8, 31, 18, 30, tzinfo=ZoneInfo("America/New_York")),
        api_token="test",
        fetcher=lambda _symbol: _price("2026-08-31"),
    )

    assert second["status"] == "collecting"
    assert second["staging_session_date"] == "2026-08-31"
    assert second["staging_attempted_count"] == 1
    assert second["staging_available_count"] == 1

