import base64
import json

from stockq_market_context import (
    CACHE_TTL_SECONDS,
    apply_stockq_market_fallback,
    decode_stockq_value,
    parse_stockq_html,
    update_stockq_market_context,
)


def _encode_like_stockq(seed: int, value: str) -> str:
    # Invert the public browser decoder to build deterministic fixtures.
    z = seed
    def random_value():
        nonlocal z
        z = z * 48271 % 2147483647
        return z / 2147483647
    random_value(); random_value()
    order = [0, 1, 2]
    for index in range(2, 0, -1):
        swap = int(random_value() * (index + 1))
        order[index], order[swap] = order[swap], order[index]
    random_value(); fake1 = int(random_value() * 4)
    random_value(); fake2 = int(random_value() * 5)
    pieces = [value[:1], value[1:3], value[3:]]
    real = [pieces[order[index]] for index in range(3)]
    fields = list(real)
    fields.insert(fake1, "FAKE1")
    fields.insert(fake2, "FAKE2")
    return base64.b64encode((str(seed) + "|" + "|".join(fields)).encode()).decode()


def test_decode_stockq_value_matches_browser_algorithm():
    payload = _encode_like_stockq(1234567, "53559.13")
    assert decode_stockq_value(payload) == "53559.13"


def test_parse_stockq_html_separates_spot_and_futures():
    spot = _encode_like_stockq(2345678, "7677.28")
    change = _encode_like_stockq(3456789, "24.42")
    pct = _encode_like_stockq(4567891, "0.32")
    future = _encode_like_stockq(5678912, "7665.75")
    html = f"""
    <table class="marketdatatable">
      <tr><td colspan="5">美洲股市指數行情</td></tr>
      <tr><td>股市</td><td>指數</td><td>漲跌</td><td>比例</td><td>當地</td></tr>
      <tr><td>S&amp;P 500</td><td><span class="sq-obfuscated" data-sq="{spot}"></span></td>
      <td><span data-sq="{change}"></span></td><td><span data-sq="{pct}"></span>%</td><td>08/28</td></tr>
    </table>
    <table class="marketdatatable">
      <tr><td colspan="5">全球指數期貨</td></tr>
      <tr><td>股市</td><td>指數</td><td>漲跌</td><td>比例</td><td>台北</td></tr>
      <tr><td>S&amp;P 500</td><td><span data-sq="{future}"></span></td><td>-17.5</td><td>-0.23%</td><td>08/28</td></tr>
    </table>
    """
    parsed = parse_stockq_html(html)
    assert parsed["sp500"]["price"] == 7677.28
    assert parsed["sp500"]["change_pct"] == 0.32
    assert parsed["sp500_futures"]["price"] == 7665.75
    assert parsed["sp500_futures"]["change_pct"] == -0.23


def test_update_uses_fresh_cache_without_refetch(tmp_path):
    cached = {
        "schema_version": 1,
        "fetched_epoch": 1000,
        "status": "ok",
        "indicator_count": 12,
        "indicators": {},
    }
    (tmp_path / "stockq_market_context.json").write_text(json.dumps(cached))
    called = False
    def fail_fetch():
        nonlocal called
        called = True
        raise AssertionError("fresh cache must not refetch")
    result = update_stockq_market_context(
        tmp_path, updated_at="2026-08-29 20:00:00",
        now_epoch=1000 + CACHE_TTL_SECONDS - 1, fetcher=fail_fetch,
    )
    assert result["cache_status"] == "fresh"
    assert called is False


def test_open_market_uses_cache_without_any_stockq_request(tmp_path):
    cached = {
        "schema_version": 1,
        "updated_at": "2026-08-28 20:00:00",
        "fetched_epoch": 1000,
        "status": "ok",
        "indicator_count": 12,
        "indicators": {"taiwan_weighted": {"price": 46331.45}},
    }
    (tmp_path / "stockq_market_context.json").write_text(json.dumps(cached))
    called = False

    def fail_fetch():
        nonlocal called
        called = True
        raise AssertionError("盤中不得連線抓 StockQ")

    result = update_stockq_market_context(
        tmp_path,
        updated_at="2026-08-29 12:00:00",
        now_epoch=1000 + 86400,
        fetcher=fail_fetch,
        allow_network=False,
    )

    assert called is False
    assert result["cache_status"] == "after_close_hold"
    assert result["network_fetch_skipped"] is True
    assert result["indicators"]["taiwan_weighted"]["price"] == 46331.45


def test_stockq_fills_only_missing_primary_market_values():
    primary = {
        "加權指數": {"price": None, "change_pct": None, "session_date": None},
        "S&P 500": {"price": 7700.0, "change_pct": 0.2, "session_date": "2026-08-28"},
    }
    stockq = {
        "updated_at": "2026-08-29 06:00:00",
        "indicators": {
            "taiwan_weighted": {
                "price": 46331.45, "change_pct": 0.77, "observed_at": "08/28",
            },
            "sp500": {"price": 7711.76, "change_pct": -0.25, "observed_at": "08/28"},
        },
    }

    result = apply_stockq_market_fallback(primary, stockq)

    assert result["加權指數"] == {
        "price": 46331.45,
        "change_pct": 0.77,
        "session_date": "2026-08-28",
        "source": "StockQ_after_close_fallback",
        "source_url": "https://www.stockq.org/",
    }
    assert result["S&P 500"] == primary["S&P 500"]


def test_update_uses_dated_stale_fallback_on_provider_error(tmp_path):
    cached = {
        "schema_version": 1,
        "fetched_epoch": 1000,
        "status": "ok",
        "indicator_count": 12,
        "indicators": {"sp500": {"price": 7000}},
    }
    (tmp_path / "stockq_market_context.json").write_text(json.dumps(cached))
    result = update_stockq_market_context(
        tmp_path, updated_at="2026-08-29 20:00:00",
        now_epoch=1000 + CACHE_TTL_SECONDS + 1,
        fetcher=lambda: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    assert result["status"] == "stale_fallback"
    assert result["indicators"]["sp500"]["price"] == 7000
    assert "blocked" in result["last_error"]
