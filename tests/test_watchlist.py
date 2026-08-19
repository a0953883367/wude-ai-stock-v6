from notifier import _telegram_chunks, render_markdown
from watchlist import load_watchlist


def test_watchlist_is_deduplicated_and_contains_core_symbols():
    rows = load_watchlist()
    symbols = [row["symbol"] for row in rows]
    assert len(symbols) == len(set(symbols))
    for symbol in ("2327.TW", "3481.TW", "NVDA", "SMH", "0050.TW", "EUV"):
        assert symbol in symbols


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
