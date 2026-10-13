"""Regression: a Yahoo action-only alignment tail is not a price session."""
import copy

import pandas as pd
import pytest

from briefing import _report_session_issues, _carry_forward_symbol_session_regressions
from data_fetcher import _extract_frame
from daily_price_provenance import tw_official_daily_metadata
from tw_official_data import overlay_official_daily


def snapshot(session="2026-10-12"):
    return {"date": session, "open": 11.0, "high": 12.0, "low": 10.0,
            "close": 11.5, "volume": 1000.0,
            "tw_official_price_available": True, "tw_price_source": "TWSE OpenAPI",
            "tw_price_unit": "TWD/shares"}


def history(*, tz=None):
    return pd.DataFrame({"open": [10.0, None, None], "high": [11.0, None, None],
                         "low": [9.0, None, None], "close": [10.5, None, None],
                         "volume": [2000.0, None, None], "dividends": [0.0, 0.0, 0.0],
                         "stock splits": [0.0, 0.0, 0.0]},
                        index=pd.DatetimeIndex(["2026-10-08", "2026-10-12", "2026-10-13"], tz=tz))


@pytest.mark.parametrize("tz", [None, "Asia/Taipei", "UTC"])
def test_alignment_tail_cannot_veto_newer_official_bar(tz):
    frame = _extract_frame(history(tz=tz), "TEST.TW", False)
    before = frame.copy(deep=True)
    # Zero-valued actions make these empty price rows survive extraction.
    assert str(frame.index[-1].date()) == "2026-10-13"
    result = overlay_official_daily(frame, snapshot())
    assert str(result.index[-1].date()) == "2026-10-12"
    assert result["close"].iloc[-1] == 11.5
    assert len(result) == 2
    assert result.index.tz == frame.index.tz
    pd.testing.assert_frame_equal(frame, before)
    assert tw_official_daily_metadata(result, snapshot())["source_daily_ohlcv_complete"] is True


def test_real_newer_close_never_replaced_by_older_official_snapshot():
    frame = history()
    frame.loc[pd.Timestamp("2026-10-13"), ["open", "high", "low", "close", "volume"]] = [14, 15, 13, 14.5, 3000]
    before = frame.copy(deep=True)
    result = overlay_official_daily(frame, snapshot())
    pd.testing.assert_frame_equal(result, before)
    pd.testing.assert_frame_equal(frame, before)
    assert result["close"].iloc[-1] == 14.5


def test_older_snapshot_does_not_remove_unpriced_tail_or_change_input():
    frame = history()
    before = frame.copy(deep=True)
    result = overlay_official_daily(frame, snapshot("2026-10-07"))
    pd.testing.assert_frame_equal(result, before)
    pd.testing.assert_frame_equal(frame, before)


def test_same_priced_session_overlays_without_advancing_to_empty_tail():
    frame = history()
    result = overlay_official_daily(frame, snapshot("2026-10-08"))
    assert str(result.index[-1].date()) == "2026-10-08"
    assert result["close"].iloc[-1] == 11.5


@pytest.mark.parametrize("empty_kind", ["no_close_column", "all_close_missing", "empty", "none"])
def test_absent_priced_history_does_not_manufacture_history(empty_kind):
    frame = history()
    if empty_kind == "no_close_column":
        frame = frame.drop(columns="close")
    elif empty_kind == "all_close_missing":
        frame["close"] = float("nan")
    elif empty_kind == "empty":
        frame = frame.iloc[:0]
    else:
        frame = None
    before = None if frame is None else frame.copy(deep=True)
    result = overlay_official_daily(frame, snapshot())
    if frame is None:
        assert result is None
    else:
        pd.testing.assert_frame_equal(result, before)
        pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("source", [None, {}, {"close": 11.5}])
def test_absent_snapshot_or_date_changes_nothing(source):
    frame = history()
    before = frame.copy(deep=True)
    result = overlay_official_daily(frame, source)
    pd.testing.assert_frame_equal(result, before)


def test_missing_snapshot_close_is_not_invented_or_attested():
    frame = history()
    source = snapshot()
    source["close"] = None
    result = overlay_official_daily(frame, source)
    assert str(result.dropna(subset=["close"]).index[-1].date()) == "2026-10-08"
    assert pd.isna(result["close"].iloc[-1])
    assert tw_official_daily_metadata(result, source)["source_daily_ohlcv_complete"] is False


def test_corporate_action_only_tail_cannot_gain_price_evidence():
    frame = history()
    frame.loc[pd.Timestamp("2026-10-13"), "dividends"] = 7.0
    result = overlay_official_daily(frame, snapshot())
    assert str(result.index[-1].date()) == "2026-10-12"
    assert result["close"].iloc[-1] == 11.5
    assert frame["dividends"].iloc[-1] == 7.0


def test_repaired_oct12_still_blocked_against_accepted_oct13(tmp_path):
    result = overlay_official_daily(history(), snapshot())
    current = [{"symbol": f"TEST{i}.TW", "market": "TW", "type": "個股",
                "official_session_date": result.dropna(subset=["close"]).index[-1].date().isoformat()}
               for i in range(10)]
    previous = {"data": [{**row, "official_session_date": "2026-10-13"} for row in current]}
    repaired, carried = _carry_forward_symbol_session_regressions(current, previous)
    assert carried == []
    issues = _report_session_issues(repaired, previous)
    assert any("主交易日倒退" in issue for issue in issues)
    assert any("個股交易日倒退" in issue for issue in issues)
    assert all(row["official_session_date"] == "2026-10-12" for row in repaired)
