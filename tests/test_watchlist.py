from notifier import _telegram_chunks, render_markdown
from watchlist import load_watchlist


def test_watchlist_is_deduplicated_and_contains_core_symbols():
    rows = load_watchlist()
    symbols = [row["symbol"] for row in rows]
    assert len(symbols) == len(set(symbols))
    for symbol in ("2327.TW", "3481.TW", "NVDA", "SMH", "0050.TW", "EUV"):
        assert symbol in symbols


def test_power_theme_and_friend_requested_mxic_are_in_fixed_watchlist():
    rows = load_watchlist()
    by_symbol = {row["symbol"]: row for row in rows}

    expected_us = {
        "CEG", "VST", "NEE", "DUK", "SO", "AEP", "EXC", "ETR", "D",
        "PCG", "GEV", "ETN", "PWR", "HUBB", "VRT", "CCJ", "BWXT",
        "LEU", "OKLO", "SMR",
    }
    expected_tw = {
        "1519.TW", "1513.TW", "1503.TW", "1514.TW", "2371.TW",
        "8926.TW", "6806.TW", "6873.TW", "6869.TW", "3713.TWO",
        "1605.TW", "1609.TW", "1618.TW", "1612.TW", "2308.TW",
        "6781.TW", "3027.TW", "2337.TW",
    }
    assert expected_us <= by_symbol.keys()
    assert expected_tw <= by_symbol.keys()
    assert by_symbol["2337.TW"]["name"] == "旺宏"
    assert by_symbol["2337.TW"]["theme"] == "記憶體"


def test_user_requested_us_listings_and_screenshot_symbols_are_in_fixed_watchlist():
    rows = load_watchlist()
    by_symbol = {row["symbol"]: row for row in rows}

    screenshot_symbols = {"PATH", "SPCX", "MRVL", "GLW", "UMC", "NOK", "SKHY", "AVGO", "EUV"}
    assert screenshot_symbols | {"HNHPF"} <= by_symbol.keys()
    assert by_symbol["SPCX"]["name"] == "SpaceX"
    assert by_symbol["SKHY"]["theme"] == "AI記憶體／HBM"
    assert by_symbol["UMC"]["name"] == "聯電 ADR"
    assert by_symbol["HNHPF"]["name"] == "鴻海 OTC"
    assert by_symbol["PATH"]["industry"] == "RPA軟體"
    assert by_symbol["GLW"]["theme"] == "光通訊／CPO"
    assert all(by_symbol[symbol]["market"] == "US" for symbol in screenshot_symbols | {"HNHPF"})


def test_mobile_report_has_no_raw_markdown_headings():
    row = {
        "symbol": "2327.TW", "name": "國巨", "market": "TW", "price": 100,
        "change_pct": 1.2, "volume_pace": 1.5, "buy_price": 98,
        "support1": 96, "resistance1": 105, "action": "🟢 可分批，等回測買點",
        "risk": "一般波動",
    }
    report = {
        "period": "evening", "updated_at": "2026-08-14 20:00:00",
        "watchlist_analyzed_count": 1, "watchlist_count": 1, "universe_count": 149,
        "market": {}, "watchlist": [row], "unavailable": [], "top": [row],
    }
    text = render_markdown(report)
    assert "###" not in text
    assert "國巨 2327" in text
    assert "🟢" in text
    assert all(len(chunk) <= 3800 for chunk in _telegram_chunks(text))


def test_mobile_report_does_not_call_empty_or_unqualified_top_list_best():
    report = {
        "period": "evening", "updated_at": "2026-08-19 20:00:00",
        "watchlist_analyzed_count": 0, "watchlist_count": 0, "universe_count": 337,
        "market": {}, "watchlist": [], "unavailable": [], "top": [],
    }

    text = render_markdown(report)

    assert "目前沒有通過完整風控" in text
    assert "最強 5 檔" not in text
