"""Official Taiwan-market data adapters.

The module deliberately keeps Taiwan exchange data separate from US feeds.
TWSE/TPEx are the primary completed-session sources; FinMind and Yahoo remain
fallbacks in ``briefing.py``.  Every value carries a date and unit so stale or
mis-scaled data cannot silently enter the Taiwan ranking model.
"""

from __future__ import annotations

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import SETTINGS, TAIPEI


LOG = logging.getLogger(__name__)
TWSE_BASE = "https://openapi.twse.com.tw/v1"
TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_BASE = "https://www.tpex.org.tw/openapi/v1"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("+", "")
    if text in {"", "--", "---", "N/A", "nan", "除權", "除息"}:
        return None
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso_date(value: Any) -> str | None:
    """Normalize YYYYMMDD, ROC YYYMMDD, or ISO dates."""
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if len(text) == 7 and text.isdigit():
        return f"{int(text[:3]) + 1911:04d}-{text[3:5]}-{text[5:7]}"
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def _get_json(url: str, *, params: dict[str, str] | None = None) -> Any:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": "WudeAIStock/1.0 official-market-data"},
        timeout=min(15, SETTINGS.request_timeout),
    )
    response.raise_for_status()
    return response.json()


def _twse_t86_rows() -> list[dict[str, Any]]:
    payload = _get_json(TWSE_T86, params={"selectType": "ALL", "response": "json"})
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    fields = payload.get("fields") or []
    trade_date = _iso_date(payload.get("date"))
    rows = []
    for values in payload.get("data") or []:
        row = dict(zip(fields, values))
        row["_date"] = trade_date
        rows.append(row)
    return rows


def _safe_rows(url: str) -> list[dict[str, Any]]:
    """Keep one unavailable official dataset from discarding every other one."""
    try:
        payload = _get_json(url)
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        LOG.warning("Official Taiwan dataset unavailable (%s): %s", url, exc)
        return []


def _safe_twse_t86_rows() -> list[dict[str, Any]]:
    try:
        return _twse_t86_rows()
    except Exception as exc:
        LOG.warning("TWSE T86 unavailable: %s", exc)
        return []


def _field(row: dict[str, Any], *candidates: str) -> Any:
    for key in candidates:
        if key in row:
            return row.get(key)
    return None


def _parse_twse(
    stock_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], ...]:
    prices: dict[str, dict[str, Any]] = {}
    institutions: dict[str, dict[str, Any]] = {}
    credit: dict[str, dict[str, Any]] = {}
    fundamentals: dict[str, dict[str, Any]] = {}
    announcements: dict[str, list[dict[str, Any]]] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {
            "price": pool.submit(_safe_rows, f"{TWSE_BASE}/exchangeReport/STOCK_DAY_ALL"),
            "institution": pool.submit(_safe_twse_t86_rows),
            "credit": pool.submit(_safe_rows, f"{TWSE_BASE}/exchangeReport/MI_MARGN"),
            "valuation": pool.submit(_safe_rows, f"{TWSE_BASE}/exchangeReport/BWIBBU_ALL"),
            "revenue": pool.submit(_safe_rows, f"{TWSE_BASE}/opendata/t187ap05_L"),
            "announcement": pool.submit(_safe_rows, f"{TWSE_BASE}/opendata/t187ap04_L"),
        }
        datasets = {name: job.result() for name, job in jobs.items()}

    for row in datasets["price"]:
        sid = str(row.get("Code") or "").strip()
        if sid not in stock_ids:
            continue
        values = {
            "open": _number(row.get("OpeningPrice")),
            "high": _number(row.get("HighestPrice")),
            "low": _number(row.get("LowestPrice")),
            "close": _number(row.get("ClosingPrice")),
            "volume": _number(row.get("TradeVolume")),
        }
        if values["close"] is not None:
            prices[sid] = {
                **values,
                "date": _iso_date(row.get("Date")),
                "tw_official_price_available": True,
                "tw_price_source": "TWSE OpenAPI",
                "tw_price_unit": "TWD/shares",
            }

    latest_price_date = max(
        (str(item.get("date")) for item in prices.values() if item.get("date")),
        default=None,
    )

    for row in datasets["institution"]:
        sid = str(row.get("證券代號") or "").strip()
        if sid not in stock_ids:
            continue
        foreign = _number(_field(
            row,
            "外陸資買賣超股數(不含外資自營商)",
            "外陸資買賣超股數(不含外資自營商) ",
        )) or 0.0
        trust = _number(row.get("投信買賣超股數")) or 0.0
        dealer = _number(row.get("自營商買賣超股數")) or 0.0
        total = _number(row.get("三大法人買賣超股數"))
        total = foreign + trust + dealer if total is None else total
        institutions[sid] = {
            "foreign": foreign,
            "trust": trust,
            "dealer": dealer,
            "available": 1.0,
            "institution_1d": total,
            "institution_date": row.get("_date"),
            "institution_source": "TWSE T86",
            "institution_unit": "shares",
            "institution_official": True,
        }

    for row in datasets["credit"]:
        sid = str(row.get("股票代號") or "").strip()
        if sid not in stock_ids:
            continue
        margin_balance = _number(row.get("融資今日餘額"))
        margin_previous = _number(row.get("融資前日餘額"))
        short_balance = _number(row.get("融券今日餘額"))
        short_previous = _number(row.get("融券前日餘額"))
        if margin_balance is None and short_balance is None:
            continue
        credit[sid] = {
            "credit_available": 1.0,
            "margin_balance": margin_balance,
            "margin_1d_change": (
                margin_balance - margin_previous
                if margin_balance is not None and margin_previous is not None else None
            ),
            "short_balance": short_balance,
            "short_1d_change": (
                short_balance - short_previous
                if short_balance is not None and short_previous is not None else None
            ),
            "credit_date": latest_price_date,
            "credit_source": "TWSE OpenAPI MI_MARGN",
            "credit_unit": "lots",
            "credit_official": True,
        }

    for row in datasets["valuation"]:
        sid = str(row.get("Code") or "").strip()
        if sid not in stock_ids:
            continue
        per = _number(row.get("PEratio"))
        pbr = _number(row.get("PBratio"))
        dividend = _number(row.get("DividendYield"))
        if all(value is None for value in (per, pbr, dividend)):
            continue
        fundamentals[sid] = {
            "fundamental_available": 1.0,
            "per": per,
            "pbr": pbr,
            "dividend_yield": dividend,
            "valuation_date": _iso_date(row.get("Date")),
            "valuation_source": "TWSE OpenAPI BWIBBU_ALL",
            "valuation_official": True,
        }
    for row in datasets["revenue"]:
        sid = str(row.get("公司代號") or "").strip()
        if sid not in stock_ids:
            continue
        period = str(row.get("資料年月") or "").strip()
        year = int(period[:3]) + 1911 if len(period) == 5 and period.isdigit() else None
        month = int(period[3:]) if year else None
        item = fundamentals.setdefault(sid, {})
        item.update({
            "fundamental_available": 1.0,
            "revenue_year": year,
            "revenue_month": month,
            "monthly_revenue": _number(row.get("營業收入-當月營收")),
            "revenue_mom_pct": _number(row.get("營業收入-上月比較增減(%)")),
            "revenue_yoy_pct": _number(row.get("營業收入-去年同月增減(%)")),
            "revenue_date": _iso_date(row.get("出表日期")),
            "revenue_source": "MOPS via TWSE OpenAPI",
            "revenue_unit": "TWD thousands",
            "revenue_official": True,
        })
    for row in datasets["announcement"]:
        sid = str(row.get("公司代號") or "").strip()
        if sid not in stock_ids:
            continue
        title = str(_field(row, "主旨 ", "主旨") or "").strip()
        if title:
            announcements.setdefault(sid, []).append({
                "date": _iso_date(row.get("發言日期")),
                "title": title,
                "source": "MOPS via TWSE OpenAPI",
                "official": True,
            })
    return prices, institutions, credit, fundamentals, announcements


def _parse_tpex(
    stock_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], ...]:
    prices: dict[str, dict[str, Any]] = {}
    institutions: dict[str, dict[str, Any]] = {}
    credit: dict[str, dict[str, Any]] = {}
    fundamentals: dict[str, dict[str, Any]] = {}
    announcements: dict[str, list[dict[str, Any]]] = {}

    with ThreadPoolExecutor(max_workers=7) as pool:
        jobs = {
            "price": pool.submit(_safe_rows, f"{TPEX_BASE}/tpex_mainboard_daily_close_quotes"),
            "institution": pool.submit(_safe_rows, f"{TPEX_BASE}/tpex_3insti_daily_trading"),
            "credit": pool.submit(_safe_rows, f"{TPEX_BASE}/tpex_mainboard_margin_balance"),
            "sbl": pool.submit(_safe_rows, f"{TPEX_BASE}/tpex_margin_sbl"),
            "valuation": pool.submit(_safe_rows, f"{TPEX_BASE}/tpex_mainboard_peratio_analysis"),
            "revenue": pool.submit(_safe_rows, f"{TPEX_BASE}/mopsfin_t187ap05_O"),
            "announcement": pool.submit(_safe_rows, f"{TPEX_BASE}/mopsfin_t187ap04_O"),
        }
        datasets = {name: job.result() for name, job in jobs.items()}

    for row in datasets["price"]:
        sid = str(row.get("SecuritiesCompanyCode") or "").strip()
        if sid not in stock_ids:
            continue
        close = _number(row.get("Close"))
        if close is None:
            continue
        prices[sid] = {
            "open": _number(row.get("Open")),
            "high": _number(row.get("High")),
            "low": _number(row.get("Low")),
            "close": close,
            "volume": _number(row.get("TradingShares")),
            "date": _iso_date(row.get("Date")),
            "tw_official_price_available": True,
            "tw_price_source": "TPEx OpenAPI",
            "tw_price_unit": "TWD/shares",
        }

    for row in datasets["institution"]:
        sid = str(row.get("SecuritiesCompanyCode") or "").strip()
        if sid not in stock_ids:
            continue
        foreign = _number(_field(
            row,
            "ForeignInvestorsInclude MainlandAreaInvestors-Difference",
            "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference",
        )) or 0.0
        trust = _number(row.get("SecuritiesInvestmentTrustCompanies-Difference")) or 0.0
        dealer = _number(row.get("Dealers-Difference")) or 0.0
        total = _number(row.get("TotalDifference"))
        total = foreign + trust + dealer if total is None else total
        institutions[sid] = {
            "foreign": foreign,
            "trust": trust,
            "dealer": dealer,
            "available": 1.0,
            "institution_1d": total,
            "institution_date": _iso_date(row.get("Date")),
            "institution_source": "TPEx OpenAPI",
            "institution_unit": "shares",
            "institution_official": True,
        }

    for row in datasets["credit"]:
        sid = str(row.get("SecuritiesCompanyCode") or "").strip()
        if sid not in stock_ids:
            continue
        margin_balance = _number(row.get("MarginPurchaseBalance"))
        margin_previous = _number(row.get("MarginPurchaseBalancePreviousDay"))
        short_balance = _number(row.get("ShortSaleBalance"))
        short_previous = _number(row.get("ShortSaleBalancePreviousDay"))
        credit[sid] = {
            "credit_available": 1.0,
            "margin_balance": margin_balance,
            "margin_1d_change": (
                margin_balance - margin_previous
                if margin_balance is not None and margin_previous is not None else None
            ),
            "short_balance": short_balance,
            "short_1d_change": (
                short_balance - short_previous
                if short_balance is not None and short_previous is not None else None
            ),
            "credit_date": _iso_date(row.get("Date")),
            "credit_source": "TPEx OpenAPI",
            "credit_unit": "lots",
            "credit_official": True,
        }

    for row in datasets["sbl"]:
        sid = str(row.get("SecuritiesCompanyCode") or "").strip()
        if sid not in stock_ids:
            continue
        current = _number(row.get("SecuritiesBorrowingBalanceOfTheMarketDay"))
        previous = _number(row.get("SecuritiesBorrowingBalancePreviousDay"))
        item = credit.setdefault(sid, {"credit_available": 1.0})
        item.update({
            "sbl_balance": current,
            "sbl_1d_change": (
                current - previous
                if current is not None and previous is not None else None
            ),
            "credit_date": _iso_date(row.get("Date")),
            "credit_source": "TPEx OpenAPI",
            "credit_official": True,
            "sbl_unit": "shares",
        })

    for row in datasets["valuation"]:
        sid = str(row.get("SecuritiesCompanyCode") or "").strip()
        if sid not in stock_ids:
            continue
        per = _number(row.get("PriceEarningRatio"))
        pbr = _number(row.get("PriceBookRatio"))
        dividend = _number(row.get("YieldRatio"))
        if all(value is None for value in (per, pbr, dividend)):
            continue
        fundamentals[sid] = {
            "fundamental_available": 1.0,
            "per": per,
            "pbr": pbr,
            "dividend_yield": dividend,
            "valuation_date": _iso_date(row.get("Date")),
            "valuation_source": "TPEx OpenAPI",
            "valuation_official": True,
        }
    for row in datasets["revenue"]:
        sid = str(row.get("公司代號") or "").strip()
        if sid not in stock_ids:
            continue
        period = str(row.get("資料年月") or "").strip()
        year = int(period[:3]) + 1911 if len(period) == 5 and period.isdigit() else None
        month = int(period[3:]) if year else None
        item = fundamentals.setdefault(sid, {})
        item.update({
            "fundamental_available": 1.0,
            "revenue_year": year,
            "revenue_month": month,
            "monthly_revenue": _number(row.get("營業收入-當月營收")),
            "revenue_mom_pct": _number(row.get("營業收入-上月比較增減(%)")),
            "revenue_yoy_pct": _number(row.get("營業收入-去年同月增減(%)")),
            "revenue_date": _iso_date(row.get("出表日期")),
            "revenue_source": "MOPS via TPEx OpenAPI",
            "revenue_unit": "TWD thousands",
            "revenue_official": True,
        })
    for row in datasets["announcement"]:
        sid = str(row.get("SecuritiesCompanyCode") or "").strip()
        if sid not in stock_ids:
            continue
        title = str(row.get("主旨") or "").strip()
        if title:
            announcements.setdefault(sid, []).append({
                "date": _iso_date(row.get("發言日期")),
                "title": title,
                "source": "MOPS via TPEx OpenAPI",
                "official": True,
            })
    return prices, institutions, credit, fundamentals, announcements


def _merge_latest_institution(
    fallback: dict[str, Any], official: dict[str, Any]
) -> dict[str, Any]:
    """Replace the latest session while preserving verified same-date windows."""
    merged = dict(fallback)
    fallback_date = str(fallback.get("institution_date") or "")
    official_date = str(official.get("institution_date") or "")
    old_one = _number(fallback.get("institution_1d"))
    new_one = _number(official.get("institution_1d"))
    history_ready = bool(fallback.get(
        "institution_multiday_available",
        fallback.get("institution_5d") is not None,
    ))
    if (
        fallback_date and fallback_date == official_date and history_ready
        and old_one is not None and new_one is not None
    ):
        delta = new_one - old_one
        for window in (3, 5, 10):
            key = f"institution_{window}d"
            previous = _number(fallback.get(key))
            if previous is not None:
                merged[key] = previous + delta
        merged["institution_multiday_available"] = True
    else:
        # Never label a stale FinMind window as current official history.
        for window in (3, 5, 10):
            merged[f"institution_{window}d"] = None
        merged["institution_multiday_available"] = False
    merged.update(official)
    return merged


def _merge_latest_credit(
    fallback: dict[str, Any], official: dict[str, Any]
) -> dict[str, Any]:
    """Merge current official balances without presenting stale 5-day deltas."""
    merged = dict(fallback)
    same_date = bool(
        fallback.get("credit_date")
        and fallback.get("credit_date") == official.get("credit_date")
    )
    history_ready = bool(fallback.get(
        "credit_multiday_available",
        any(fallback.get(f"{prefix}_5d_change") is not None for prefix in (
            "margin", "short", "sbl"
        )),
    ))
    if same_date and history_ready:
        for prefix in ("margin", "short", "sbl"):
            latest_key = f"{prefix}_1d_change"
            window_key = f"{prefix}_5d_change"
            old_latest = _number(fallback.get(latest_key))
            new_latest = _number(official.get(latest_key))
            old_window = _number(fallback.get(window_key))
            if old_window is not None and old_latest is not None and new_latest is not None:
                merged[window_key] = old_window + new_latest - old_latest
        merged["credit_multiday_available"] = True
    else:
        for prefix in ("margin", "short", "sbl"):
            merged[f"{prefix}_5d_change"] = None
        merged["credit_multiday_available"] = False
    usable = {key: value for key, value in official.items() if value is not None}
    merged.update(usable)
    return merged


def merge_official_with_fallback(
    fallback: dict[str, dict[str, Any]],
    official: dict[str, dict[str, Any]],
    *,
    kind: str,
) -> dict[str, dict[str, Any]]:
    output = {sid: dict(values) for sid, values in fallback.items()}
    for sid, values in official.items():
        base = output.get(sid, {})
        if kind == "institution":
            output[sid] = _merge_latest_institution(base, values)
        elif kind == "credit":
            output[sid] = _merge_latest_credit(base, values)
        else:
            # An official endpoint can legitimately omit a field (for example
            # PE for a loss-making company). Do not erase a valid fallback
            # field with ``None``; provenance flags and dates still update.
            usable = {key: value for key, value in values.items() if value is not None}
            output[sid] = {**base, **usable}
    return output


def overlay_official_daily(
    frame: pd.DataFrame | None, snapshot: dict[str, Any] | None
) -> pd.DataFrame | None:
    """Overlay or append the exchange-completed daily bar to Yahoo history."""
    if frame is None or frame.empty or not snapshot or not snapshot.get("date"):
        return frame
    trade_date = pd.Timestamp(str(snapshot["date"]))
    output = frame.copy()
    output.columns = [str(column).lower() for column in output.columns]
    if isinstance(output.index, pd.DatetimeIndex) and output.index.tz is not None:
        trade_date = trade_date.tz_localize(output.index.tz)
    latest_date = pd.Timestamp(output.index[-1]).date()
    if trade_date.date() < latest_date:
        return output
    target = output.index[-1] if trade_date.date() == latest_date else trade_date
    if target not in output.index:
        output.loc[target] = {column: float("nan") for column in output.columns}
    for column in ("open", "high", "low", "close", "volume"):
        value = _number(snapshot.get(column))
        if value is not None:
            output.loc[target, column] = value
    if "adj close" in output.columns and pd.isna(output.loc[target, "adj close"]):
        output.loc[target, "adj close"] = output.loc[target, "close"]
    return output.sort_index()


def fetch_taiwan_official_data(
    universe: list[dict[str, Any]],
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Fetch official completed-session data for all maintained Taiwan symbols."""
    cache_path = cache_path or (SETTINGS.reports_dir / "tw_official_cache.json")
    twse_ids = {
        str(item.get("symbol") or "").split(".")[0]
        for item in universe
        if item.get("market") == "TW" and str(item.get("symbol") or "").endswith(".TW")
    }
    tpex_ids = {
        str(item.get("symbol") or "").split(".")[0]
        for item in universe
        if item.get("market") == "TW" and str(item.get("symbol") or "").endswith(".TWO")
    }
    empty = {
        "prices": {}, "institutions": {}, "credit": {},
        "fundamentals": {}, "announcements": {},
    }
    try:
        twse = _parse_twse(twse_ids) if twse_ids else ({}, {}, {}, {}, {})
        tpex = _parse_tpex(tpex_ids) if tpex_ids else ({}, {}, {}, {}, {})
        result = {
            name: {**twse[index], **tpex[index]}
            for index, name in enumerate(
                ("prices", "institutions", "credit", "fundamentals", "announcements")
            )
        }
        if not any(result.values()):
            raise RuntimeError("official Taiwan endpoints returned no maintained symbols")
        payload = {
            "updated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
            "data": result,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        LOG.warning("Taiwan official data unavailable; using last cache: %s", exc)
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            cached = payload.get("data") or {}
            # A cache is useful for slowly changing valuation/revenue context,
            # but cached daily price/flow must never masquerade as current.
            return {
                "prices": {},
                "institutions": {},
                "credit": {},
                "fundamentals": dict(cached.get("fundamentals") or {}),
                "announcements": {},
            }
        except (OSError, ValueError, TypeError):
            return empty
