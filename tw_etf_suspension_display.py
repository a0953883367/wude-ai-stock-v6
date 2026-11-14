"""Report-only quote qualification for a verified, scheduled ETF suspension.

Never changes model inputs, ranks, price history, or portfolio records. The
legacy row is retained for audit; quote_display is the presentation contract.
An expected resumption date is not evidence that trading actually resumed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo


SCHEMA = "wude.quote_display.suspension.v1"
TAIPEI = ZoneInfo("Asia/Taipei")
EVENT = {
    "symbol": "00662.TW",
    "market": "TW",
    "asset_type": "ETF",
    "last_scheduled_trading_date": "2026-11-10",
    "suspension_start": "2026-11-11",
    "suspension_end": "2026-11-16",
    "planned_resumption_date": "2026-11-17",
    "split_ratio": 5,
    "source_url": "https://www.twse.com.tw/zh/ETFortune/announcement?company=A00010&date=20260910&fund=00662&seq=1&type=all",
    "source_published_date": "2026-09-10",
    "resumption_confirmed": False,
}


def _report_day(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=TAIPEI)
        return stamp.astimezone(TAIPEI).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def quote_display_for(row, report_updated_at):
    """Qualify display, without inventing a close or backdating source rows.

Resume verification is intentionally not inferred from provider booleans,
positive volume, split-adjusted prices, or the wall clock. A later change must
review official resumption evidence and actual traded candles explicitly.
"""
    if (row.get("symbol") != EVENT["symbol"] or row.get("market") != "TW"
            or row.get("type") != "ETF"):
        return None
    day = _report_day(report_updated_at)
    if day is not None and day < EVENT["suspension_start"]:
        return None
    if day is None:
        status, label = "report_time_unverified", "報告時間無法核對，報價暫不採用"
    elif day <= EVENT["suspension_end"]:
        status, label = "official_suspension", "分割停牌中，沒有當日成交報價"
    else:
        status, label = "resumption_unconfirmed", "預計恢復日已到，尚待有效成交資料確認"
    return {
        "schema": SCHEMA,
        "scope": "report_display_only",
        "status": status,
        "label": label,
        "suppress_current_quote": True,
        "price": None,
        "session_date": None,
        "change_pct": None,
        "volume": None,
        "official_close_available": False,
        "report_date": day,
        "event": dict(EVENT),
        "source_reference": {
            "price": row.get("price"),
            "reported_session_date": row.get("official_session_date"),
            "verified_as_trade": False,
        },
        "note": "原始來源日期與數值僅供稽核，不視為停牌期間成交；不回填最後交易日、不以淨值或分割試算替代報價。",
        "model_reference_note": "既有模型分數、排名與歷史紀錄保留；停牌或恢復未確認期間不展示當期買賣區間。",
    }


def project_report_quotes(payload):
    """Copy publication payloads, never mutate aliased scored/ledger rows."""
    projected = deepcopy(payload)
    for key in ("data", "watchlist", "top"):
        rows = projected.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Recompute our own carried annotation at this report's date.
            old = row.get("quote_display")
            if isinstance(old, dict) and old.get("schema") == SCHEMA:
                row.pop("quote_display")
            display = quote_display_for(row, projected.get("updated_at"))
            if display is not None:
                row["quote_display"] = display
    return projected
