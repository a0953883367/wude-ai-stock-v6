from datetime import datetime
from zoneinfo import ZoneInfo

from large_buy_streams import market_live_window


TAIPEI = ZoneInfo("Asia/Taipei")


def at(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=TAIPEI)


def test_tw_connection_window_starts_at_0900_and_stops_at_1330():
    assert not market_live_window("TW", at("2026-08-31T08:59:00"))
    assert market_live_window("TW", at("2026-08-31T09:00:00"))
    assert market_live_window("TW", at("2026-08-31T13:29:00"))
    assert not market_live_window("TW", at("2026-08-31T13:30:00"))


def test_us_connection_window_starts_at_2130_and_crosses_midnight():
    assert not market_live_window("US", at("2026-08-31T21:29:00"))
    assert market_live_window("US", at("2026-08-31T21:30:00"))
    assert market_live_window("US", at("2026-09-01T03:59:00"))
    assert not market_live_window("US", at("2026-09-01T04:00:00"))


def test_us_winter_session_follows_new_york_clock_until_0500_taipei():
    assert not market_live_window("US", at("2026-12-01T22:29:00"))
    assert market_live_window("US", at("2026-12-01T22:30:00"))
    assert market_live_window("US", at("2026-12-02T04:59:00"))
    assert not market_live_window("US", at("2026-12-02T05:00:00"))


def test_weekend_connections_remain_closed():
    assert not market_live_window("TW", at("2026-08-30T10:00:00"))
    assert not market_live_window("US", at("2026-08-30T22:00:00"))
    assert not market_live_window("US", at("2026-08-31T03:00:00"))
