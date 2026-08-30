import json
from datetime import datetime
from zoneinfo import ZoneInfo

from stockq_us_close import (
    apply_stockq_us_close_to_simulation_rows,
    load_us_shadow_symbols,
    parse_stockq_us_close_html,
    update_stockq_us_close_fallback,
)


def _table(symbols=("MSFT", "NVDA")) -> str:
    rows = "".join(
        f"""
        <tr><td><a href="../index/US_{symbol}.php">{symbol} 公司</a></td>
        <td>{500 + index}.25</td><td>-1.5</td><td>-0.30%</td>
        <td>10.0%</td><td>1.0%</td><td>08/28</td></tr>
        """
        for index, symbol in enumerate(symbols)
    )
    return f"""
    <table class="marketdatatable">
      <tr><td colspan="7">全球金融行情</td></tr>
      <tr><td>名稱</td><td>價格</td><td>漲跌</td><td>比例</td><td>今年</td><td>偏離</td><td>當地</td></tr>
      {rows}
    </table>
    """


def test_parse_popular_us_page_preserves_symbol_close_and_date():
    result = parse_stockq_us_close_html(_table(), updated_at="2026-08-30 06:00:00")

    assert result["MSFT"]["official_close_price"] == 500.25
    assert result["MSFT"]["official_session_date"] == "2026-08-28"
    assert result["MSFT"]["change_pct"] == -0.3
    assert result["MSFT"]["stockq_close_only"] is True
    assert "official_open_price" not in result["MSFT"]


def test_us_regular_session_never_requests_stockq(tmp_path):
    called = False

    def fail_fetch():
        nonlocal called
        called = True
        raise AssertionError("美股盤中不得抓取 StockQ")

    result = update_stockq_us_close_fallback(
        tmp_path,
        updated_at="2026-08-31 23:00:00",
        requested_symbols={"MSFT"},
        now=datetime(2026, 8, 31, 14, 0, tzinfo=ZoneInfo("America/New_York")),
        fetcher=fail_fetch,
    )

    assert called is False
    assert result["status"] == "waiting_for_us_close"
    assert result["network_fetch_skipped"] is True


def test_refresh_records_limited_requested_coverage(tmp_path):
    symbols = tuple(f"T{index}" for index in range(10)) + ("MSFT",)
    result = update_stockq_us_close_fallback(
        tmp_path,
        updated_at="2026-08-30 06:00:00",
        requested_symbols={"MSFT", "MISSING"},
        now_epoch=1000,
        now=datetime(2026, 8, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        fetcher=lambda: _table(symbols),
    )

    assert result["status"] == "ok"
    assert result["symbol_count"] == 11
    assert result["covered_requested_symbols"] == ["MSFT"]
    assert result["missing_requested_symbols"] == ["MISSING"]


def test_close_fallback_does_not_overwrite_complete_primary_row():
    rows = [{
        "symbol": "MSFT", "market": "US", "type": "個股",
        "official_session_date": "2026-08-28", "official_close_price": 510.0,
        "official_price_source": "Yahoo",
    }]
    fallback = {"rows": {"MSFT": {
        "official_session_date": "2026-08-28", "official_close_price": 513.53,
    }}}

    result, applied = apply_stockq_us_close_to_simulation_rows(
        rows, fallback, [{"symbol": "MSFT", "market": "US", "type": "個股"}]
    )

    assert applied == []
    assert result[0]["official_close_price"] == 510.0
    assert result[0]["official_price_source"] == "Yahoo"


def test_close_fallback_can_value_required_holding_but_cannot_create_entry():
    fallback = {"rows": {"MSFT": {
        "name": "Microsoft 微軟",
        "official_session_date": "2026-08-28",
        "official_close_price": 513.53,
    }}}
    universe = [{"symbol": "MSFT", "name": "Microsoft", "market": "US", "type": "個股"}]

    result, applied = apply_stockq_us_close_to_simulation_rows(
        [], fallback, universe, required_symbols={"MSFT"}
    )

    assert applied == ["MSFT"]
    assert result[0]["official_close_price"] == 513.53
    assert result[0]["official_open_price"] is None
    assert result[0]["trade_guard_blocked"] is True
    assert result[0]["market_contract_valid"] is False


def test_shadow_symbol_loader_limits_fallback_to_active_us_state(tmp_path):
    holding = {
        "medium": {"US": {
            "positions": [{"symbol": "MSFT"}],
            "benchmark_positions": [{"symbol": "VOO"}],
            "pending": None,
        }},
        "long": {
            "positions": [{"symbol": "NVDA", "market": "US"}, {"symbol": "2330.TW", "market": "TW"}],
            "benchmark_positions": [],
            "pending": {"US": {"picks": [{"symbol": "AMZN"}]}},
        },
    }
    million = {"markets": {"US": {"pending": {
        "strategies": {"overall": [{"symbol": "AVGO"}], "short": []}
    }}}}
    (tmp_path / "holding_simulation.json").write_text(json.dumps(holding))
    (tmp_path / "million_simulation.json").write_text(json.dumps(million))

    assert load_us_shadow_symbols(tmp_path) == {"MSFT", "VOO", "NVDA", "AMZN", "AVGO"}
