"""Official TWSE/TPEx quarterly financial statement overlay.

The exchanges publish current listed/OTC income statements and balance sheets
through their OpenAPI catalogues.  Values are kept in the reported unit
(normally TWD thousands), which preserves ratios and statement/EPS-derived
market-value multiples without inventing missing cash-flow data.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests


SCHEMA_VERSION = 1
TWSE_BASE = "https://openapi.twse.com.tw/v1"
TPEX_BASE = "https://www.tpex.org.tw/openapi/v1"
INDUSTRY_TYPES = ("basi", "bd", "ci", "fh", "ins", "mim")
ENDPOINTS = {
    **{
        f"TWSE_income_{kind}": f"{TWSE_BASE}/opendata/t187ap06_L_{kind}"
        for kind in INDUSTRY_TYPES
    },
    **{
        f"TWSE_balance_{kind}": f"{TWSE_BASE}/opendata/t187ap07_L_{kind}"
        for kind in INDUSTRY_TYPES
    },
    **{
        f"TPEX_income_{kind}": f"{TPEX_BASE}/mopsfin_t187ap06_O_{kind}"
        for kind in INDUSTRY_TYPES
    },
    **{
        f"TPEX_balance_{kind}": f"{TPEX_BASE}/mopsfin_t187ap07_O_{kind}"
        for kind in INDUSTRY_TYPES
    },
}


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _report_date(row: dict[str, Any]) -> str | None:
    try:
        year = int(str(row.get("年度") or row.get("Year") or "")) + 1911
        quarter = int(str(row.get("季別") or row.get("Quarter") or ""))
    except ValueError:
        return None
    month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}.get(quarter)
    return f"{year:04d}-{month_day}" if month_day else None


def _stock_id(row: dict[str, Any]) -> str:
    return str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or "").strip()


def aggregate_official_financial_rows(
    endpoint_rows: dict[str, list[dict[str, Any]]], stock_ids: set[str]
) -> dict[str, dict[str, Any]]:
    income: dict[str, tuple[str, dict[str, Any], str]] = {}
    balance: dict[str, tuple[str, dict[str, Any], str]] = {}
    for endpoint, rows in endpoint_rows.items():
        kind = "income" if "_income_" in endpoint else "balance"
        target = income if kind == "income" else balance
        for row in rows:
            sid = _stock_id(row)
            report_date = _report_date(row)
            if sid not in stock_ids or not report_date:
                continue
            previous = target.get(sid)
            if previous is None or report_date >= previous[0]:
                target[sid] = (report_date, row, endpoint)

    result: dict[str, dict[str, Any]] = {}
    for sid in sorted(stock_ids.intersection(set(income) | set(balance))):
        item: dict[str, Any] = {
            "financial_quality_available": 1.0,
            "financial_quality_official": True,
            "financial_quality_source": "MOPS via TWSE/TPEx OpenAPI",
            "financial_statement_unit": "TWD_thousands_as_reported",
        }
        dates: list[str] = []
        fields: list[str] = []
        if sid in income:
            report_date, row, endpoint = income[sid]
            dates.append(report_date)
            revenue = _first_number(row, (
                "營業收入", "收入", "收益", "淨收益", "利息淨收益",
            ))
            net_income = _first_number(row, (
                "淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主",
                "本期稅後淨利（淨損）", "本期淨利（淨損）",
            ))
            eps = _first_number(row, ("基本每股盈餘（元）", "基本每股盈餘"))
            operating_profit = _first_number(row, (
                "營業利益（損失）", "營業利益", "繼續營業單位稅前損益",
                "繼續營業單位稅前淨利（淨損）",
            ))
            gross_profit = _first_number(row, ("營業毛利（毛損）淨額", "營業毛利（毛損）"))
            if revenue is not None:
                item["statement_revenue_ytd"] = revenue
                fields.append("statement_revenue_ytd")
            if net_income is not None:
                item["statement_net_income_ytd"] = net_income
                fields.append("statement_net_income_ytd")
            if eps is not None:
                item["eps"] = eps
                fields.append("eps")
            if revenue not in (None, 0) and operating_profit is not None:
                item["operating_margin_pct"] = round(operating_profit / revenue * 100, 4)
                fields.append("operating_margin_pct")
            if revenue not in (None, 0) and gross_profit is not None:
                item["gross_margin_pct"] = round(gross_profit / revenue * 100, 4)
                fields.append("gross_margin_pct")
            item["income_statement_endpoint"] = endpoint
        if sid in balance:
            report_date, row, endpoint = balance[sid]
            dates.append(report_date)
            assets = _first_number(row, ("資產總計", "資產合計"))
            liabilities = _first_number(row, ("負債總計", "負債合計"))
            equity = _first_number(row, (
                "歸屬於母公司業主之權益合計", "權益總計", "權益合計",
            ))
            book_value = _first_number(row, ("每股參考淨值", "每股淨值"))
            if assets is not None:
                item["total_assets"] = assets
                fields.append("total_assets")
            if liabilities is not None:
                item["total_debt"] = liabilities
                fields.append("total_debt")
            if equity is not None:
                item["total_equity"] = equity
                fields.append("total_equity")
            if book_value is not None:
                item["book_value_per_share"] = book_value
                fields.append("book_value_per_share")
            if assets not in (None, 0) and liabilities is not None:
                item["debt_ratio_pct"] = round(liabilities / assets * 100, 4)
                fields.append("debt_ratio_pct")
            item["balance_sheet_endpoint"] = endpoint
        item["financial_report_date"] = min(dates) if dates else None
        net_income = _number(item.get("statement_net_income_ytd"))
        equity = _number(item.get("total_equity"))
        if net_income is not None and equity not in (None, 0):
            try:
                quarter = int(item["financial_report_date"][5:7]) // 3
            except (TypeError, ValueError, IndexError):
                quarter = 4
            item["roe_pct"] = round(net_income * 4 / max(quarter, 1) / equity * 100, 4)
            fields.append("roe_pct")
        item["financial_quality_fields"] = sorted(set(fields))
        item["financial_quality_field_count"] = len(item["financial_quality_fields"])
        result[sid] = item
    return result


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fetch_tw_official_financials(
    stock_ids: set[str],
    *,
    cache_path: Path,
    timeout: int = 20,
    fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch all official statement tables once and retain per-symbol cache."""
    stock_ids = {str(item) for item in stock_ids if str(item).isdigit()}
    cached_payload = _read_cache(cache_path)
    cached_rows = cached_payload.get("symbols") if isinstance(cached_payload.get("symbols"), dict) else {}
    cached_requested = {
        str(item) for item in (cached_payload.get("requested_symbols") or cached_rows.keys())
    }
    # Quarterly statements do not need repeated intraday downloads. A complete
    # same-day cache is returned immediately; the next trading day retries all
    # official tables and can discover a newly published quarter.
    if (
        cached_payload.get("updated_at") == date.today().isoformat()
        and (
            stock_ids.issubset(cached_requested)
            or int(cached_payload.get("requested_count") or 0) == len(stock_ids)
        )
    ):
        return {sid: dict(cached_rows[sid]) for sid in stock_ids}

    def get_rows(url: str) -> list[dict[str, Any]]:
        if fetcher is not None:
            return fetcher(url)
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "wude-ai-stock-v6/1.0"})
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    endpoint_rows: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(get_rows, url): name for name, url in ENDPOINTS.items()}
        for job in as_completed(jobs):
            name = jobs[job]
            try:
                endpoint_rows[name] = job.result()
            except Exception as exc:  # noqa: BLE001 - provider boundary
                errors[name] = f"{type(exc).__name__}: {str(exc)[:140]}"
    refreshed = aggregate_official_financial_rows(endpoint_rows, stock_ids)
    merged = {
        sid: dict(cached_rows.get(sid) or {})
        for sid in stock_ids if isinstance(cached_rows.get(sid), dict)
    }
    for sid, row in refreshed.items():
        merged[sid] = row
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": date.today().isoformat(),
        "source": "TWSE/TPEx OpenAPI",
        "requested_count": len(stock_ids),
        "available_count": len(merged),
        "requested_symbols": sorted(stock_ids),
        "missing_symbols": sorted(stock_ids - set(merged)),
        "refreshed_count": len(refreshed),
        "coverage_pct": round(len(merged) / len(stock_ids) * 100, 2) if stock_ids else 100.0,
        "endpoint_success_count": len(endpoint_rows),
        "endpoint_error_count": len(errors),
        "endpoint_errors": errors,
        "symbols": merged,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_path)
    return merged
