"""Exercise the real briefing entry point through its pre-publication gate.

External enrichment stages are stubbed. The official adapter, CSV fallback,
calendar, overlay, feature builder, Taiwan contracts, carry logic, and source
session guard remain real. Stop before unrelated market/scoring/publication
work so this test cannot publish a report or update simulation state.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import briefing
import tw_official_data as official
import tw_open_data_fallback as fallback


NOW = datetime(2026, 10, 14, 8, tzinfo=ZoneInfo("Asia/Taipei"))
SESSION = "2026-10-13"
STOCK_IDS = tuple(f"00{number}" for number in range(50, 60))


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


class _PassedSourceGate(Exception):
    """The next production stage runs only after the real source gate passed."""


@pytest.fixture
def briefing_harness(monkeypatch, tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    settings = replace(briefing.SETTINGS, reports_dir=reports)
    monkeypatch.setattr(briefing, "SETTINGS", settings)
    monkeypatch.setattr(official, "SETTINGS", settings)
    monkeypatch.setattr(briefing, "datetime", _FixedDatetime)
    monkeypatch.setattr(official, "datetime", _FixedDatetime)
    monkeypatch.setattr(fallback, "datetime", _FixedDatetime)

    universe = [
        {"symbol": sid + ".TW", "market": "TW", "type": "ETF", "name": sid}
        for sid in STOCK_IDS
    ]
    previous_rows = [{
        **item, "official_session_date": SESSION,
        "price": 112.4, "official_open_price": 112.55,
        "official_high_price": 113.2, "official_low_price": 111.9,
        "official_close_price": 112.4, "official_volume": 242070746,
        "tw_official_session_date": SESSION,
        "tw_official_price_available": True,
        "tw_price_source": "TWSE OpenAPI", "tw_price_unit": "TWD/shares",
        "source_daily_ohlcv_complete": True,
        "source_daily_ohlcv_session_date": SESSION,
        "market_contract_valid": True,
    } for item in universe]
    accepted_bytes = json.dumps({"data": previous_rows}).encode()
    (reports / "all_analysis.json").write_bytes(accepted_bytes)
    (reports / "official_market_calendar.json").write_text(json.dumps({
        "version": 1,
        "markets": {
            "TW": {"years": {"2026": {
                "status": "verified_twse_tpex", "sources": ["TWSE", "TPEx"],
                "sessions": ["2026-10-08", "2026-10-12", SESSION, "2026-10-14"],
                "session_details": {},
            }}},
            "US": {"years": {}},
        },
    }), encoding="utf-8")

    history = pd.DataFrame({
        "open": [110.] * 30, "high": [112.] * 30, "low": [109.] * 30,
        "close": [111.] * 30, "volume": [1000.] * 30, "dividends": [0.] * 30,
    }, index=pd.bdate_range(end="2026-10-12", periods=30))
    # A Yahoo alignment/action-only tail must not count as a priced session.
    history.loc[pd.Timestamp(SESSION)] = [None, None, None, None, None, 0.]
    original_history = history.copy(deep=True)
    raw_primary = [{
        "Code": sid, "Date": "1151012", "OpeningPrice": "110",
        "HighestPrice": "112", "LowestPrice": "109",
        "ClosingPrice": "111", "TradeVolume": "1000",
    } for sid in STOCK_IDS]
    csv_text = "日期,證券代號,開盤價,最高價,最低價,收盤價,成交股數\n" + "".join(
        f"1151013,{sid},112.55,113.20,111.90,112.40,242070746\n"
        for sid in STOCK_IDS
    )
    captured = {"official_calls": [], "csv_calls": [], "history_calls": [],
                "intraday_calls": [], "primary_calls": []}

    def deny_network(*args, **kwargs):
        pytest.fail("This briefing integration test must remain offline")

    monkeypatch.setattr("requests.sessions.Session.request", deny_network)

    def primary_json(url, *, params=None):
        captured["primary_calls"].append(url)
        if url == f"{official.TWSE_BASE}/exchangeReport/STOCK_DAY_ALL":
            return deepcopy(raw_primary)
        if url == official.TWSE_T86:
            return {"stat": "OK", "date": "20261012", "fields": [], "data": []}
        return []

    monkeypatch.setattr(official, "_get_json", primary_json)

    def fetch_csv():
        captured["csv_calls"].append(fallback.URL)
        return csv_text

    monkeypatch.setattr(fallback, "_fetch_csv", fetch_csv)
    real_fetch = official.fetch_taiwan_official_data

    def fetch_official(*args, **kwargs):
        captured["official_calls"].append(dict(kwargs))
        result = real_fetch(*args, **kwargs)
        captured["official_result"] = result
        return result

    monkeypatch.setattr(briefing, "fetch_taiwan_official_data", fetch_official)

    def download_history(symbols, **kwargs):
        captured["history_calls"].append(list(symbols))
        return {symbol: history for symbol in symbols}

    def download_intraday(symbols):
        captured["intraday_calls"].append(list(symbols))
        return {}

    monkeypatch.setattr(briefing, "download_history", download_history)
    monkeypatch.setattr(briefing, "download_intraday", download_intraday)
    for name in ("load_analysis_universe", "load_watchlist", "load_search_universe"):
        monkeypatch.setattr(briefing, name, lambda: deepcopy(universe))
    monkeypatch.setattr(briefing, "load_frozen_forecasts", lambda *a, **kw: {})
    monkeypatch.setattr(briefing, "load_inverse_experiment_watchlist", lambda: [])
    monkeypatch.setattr(briefing, "load_inverse_etf_catalog", lambda: {"products": []})

    real_guard = briefing._report_session_issues

    def capture_guard(features, previous):
        captured["features"] = deepcopy(features)
        captured["issues"] = real_guard(features, previous)
        return captured["issues"]

    monkeypatch.setattr(briefing, "_report_session_issues", capture_guard)

    def stage(label, action):
        if label in {"日線價格", "盤中量價", "台股官方資料"}:
            return action()
        if label == "核心市場":
            raise _PassedSourceGate()
        return {}

    monkeypatch.setattr(briefing, "_stage", stage)

    def run(period="evening", intraday=False):
        from argparse import Namespace
        monkeypatch.setattr(briefing, "parse_args", lambda: Namespace(
            period=period, intraday=intraday, no_telegram=True,
        ))
        with pytest.raises(_PassedSourceGate):
            briefing.main()
        assert captured["issues"] == []
        assert len(captured["features"]) == len(universe)
        assert (reports / "all_analysis.json").read_bytes() == accepted_bytes
        pd.testing.assert_frame_equal(history, original_history)
        return captured

    return run


def test_main_uses_fallback_before_real_feature_and_publication_guards(briefing_harness):
    result = briefing_harness()

    assert result["official_calls"] == [{"use_open_data_fallback": True}]
    assert result["csv_calls"] == [fallback.URL]
    assert result["official_result"]["price_fallback_status"]["replaced_count"] == 10
    assert result["history_calls"] == [[sid + ".TW" for sid in STOCK_IDS]]
    for row in result["features"]:
        assert row["official_session_date"] == SESSION
        assert row["tw_official_session_date"] == SESSION
        assert row["official_open_price"] == 112.55
        assert row["official_high_price"] == 113.2
        assert row["official_low_price"] == 111.9
        assert row["official_close_price"] == 112.4
        assert row["official_volume"] == 242070746
        assert row["tw_price_source"] == fallback.SOURCE
        assert row["tw_official_price_available"] is True
        assert row["source_daily_ohlcv_complete"] is True
        assert row["source_daily_ohlcv_session_date"] == SESSION
        assert row["market_contract_valid"] is True
        assert row["tw_data_valid"] is True
        assert not row.get("session_carry_forward")


@pytest.mark.parametrize("period,intraday", [
    ("morning", False), ("noon", False), ("evening", True),
])
def test_main_frozen_tw_prices_never_request_official_or_csv_fallback(
    briefing_harness, period, intraday,
):
    result = briefing_harness(period=period, intraday=intraday)

    assert result["official_calls"] == []
    assert result["primary_calls"] == []
    assert result["csv_calls"] == []
    assert result["history_calls"] == [[]]
    assert result["intraday_calls"] == [[]]
    for row in result["features"]:
        assert row["official_session_date"] == SESSION
        assert row["official_close_price"] == 112.4
        assert row["official_volume"] == 242070746
        assert row["tw_price_source"] == "TWSE OpenAPI"
        assert row["after_close_price_policy"] == "carried_completed_session"
        assert row["price_fetch_deferred_until_close"] is True
