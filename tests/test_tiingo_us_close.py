from datetime import datetime
from zoneinfo import ZoneInfo

from tiingo_us_close import (
    FREE_BATCH_LIMIT,
    apply_tiingo_us_close_to_simulation_rows,
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


def test_private_close_fallback_does_not_overwrite_primary():
    rows = [{
        "symbol": "MSFT", "market": "US", "type": "個股",
        "official_session_date": "2026-08-28", "official_close_price": 510.0,
        "official_price_source": "Yahoo",
    }]
    fallback = {"rows": {"MSFT": parse_tiingo_eod(_price(), "MSFT")}}

    result, applied = apply_tiingo_us_close_to_simulation_rows(
        rows, fallback, [{"symbol": "MSFT", "market": "US", "type": "個股"}]
    )

    assert applied == []
    assert result[0]["official_close_price"] == 510.0
    assert result[0]["official_price_source"] == "Yahoo"


def test_private_close_fallback_is_entry_blocked_for_required_holding():
    fallback = {"rows": {"MSFT": parse_tiingo_eod(_price(), "MSFT")}}
    universe = [{"symbol": "MSFT", "name": "Microsoft", "market": "US", "type": "個股"}]

    result, applied = apply_tiingo_us_close_to_simulation_rows(
        [], fallback, universe, required_symbols={"MSFT"}
    )

    assert applied == ["MSFT"]
    assert result[0]["official_open_price"] is None
    assert result[0]["official_price_source"] == "Tiingo_after_close_close_only"
    assert result[0]["trade_guard_blocked"] is True
    assert result[0]["market_contract_valid"] is False


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


def test_four_daily_runs_finish_186_symbol_cycle_before_next_session(tmp_path):
    symbols = {f"S{index:03d}" for index in range(186)}
    results = []

    for run_number in range(4):
        results.append(update_tiingo_us_close_fallback(
            tmp_path,
            updated_at=f"2026-08-31 {6 + run_number * 4:02d}:00:00",
            requested_symbols=symbols,
            now_epoch=1000 + run_number,
            now=datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")),
            api_token="test",
            fetcher=lambda _symbol: _price("2026-08-28"),
        ))

    assert FREE_BATCH_LIMIT == 47
    assert [item["staging_attempted_count"] for item in results[:3]] == [47, 94, 141]
    assert results[3]["status"] == "ok"
    assert results[3]["covered_requested_count"] == 186
    assert results[3]["attempted_count"] == 186
    assert not (tmp_path / "tiingo_us_close_staging.json").exists()
