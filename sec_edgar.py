"""Free official SEC EDGAR fundamentals used only as a US-company fallback.

The SEC endpoints need no API key.  Requests identify this project, stay below
the SEC fair-access rate, and are cached so scheduled reports do not repeatedly
download unchanged filings.  SEC facts never replace a non-empty primary
field; they only fill gaps left by the existing company-metadata source.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import requests


LOG = logging.getLogger(__name__)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
DEFAULT_USER_AGENT = (
    "WudeAIStock/6.32 "
    "github.com/a0953883367/wude-ai-stock-v6"
)

REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percent(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def _headers() -> dict[str, str]:
    return {
        "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT).strip()
        or DEFAULT_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }


def _data_headers() -> dict[str, str]:
    headers = _headers()
    headers["Host"] = "data.sec.gov"
    return headers


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _age_days(value: Any, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(value))).days
    except (TypeError, ValueError):
        return None


def _ticker_variants(symbol: str) -> tuple[str, ...]:
    normalized = str(symbol or "").upper().strip()
    return tuple(dict.fromkeys((
        normalized,
        normalized.replace(".", "-"),
        normalized.replace("-", "."),
    )))


def normalize_ticker_map(payload: Any) -> dict[str, str]:
    """Return SEC ticker -> zero-padded CIK for dict or list responses."""
    rows: Iterable[Any]
    if isinstance(payload, dict):
        rows = payload.values()
    elif isinstance(payload, list):
        rows = payload
    else:
        return {}
    output: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        try:
            cik = f"{int(row.get('cik_str')):010d}"
        except (TypeError, ValueError):
            continue
        if ticker:
            output[ticker] = cik
    return output


def _unit_rows(
    payload: dict[str, Any], concept: str, *, preferred_unit: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    fact = ((payload.get("facts") or {}).get("us-gaap") or {}).get(concept) or {}
    units = fact.get("units") or {}
    if not isinstance(units, dict):
        return [], None
    if preferred_unit:
        rows = units.get(preferred_unit)
        return (
            ([row for row in rows if isinstance(row, dict)], preferred_unit)
            if isinstance(rows, list) and rows else ([], None)
        )
    candidates = []
    candidates.extend(("USD", "shares", "USD/shares"))
    candidates.extend(str(key) for key in units)
    for unit in dict.fromkeys(candidates):
        rows = units.get(unit)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)], unit
    return [], None


def _annual_values(
    payload: dict[str, Any], concepts: Iterable[str], *, preferred_unit: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    for concept in concepts:
        rows, unit = _unit_rows(payload, concept, preferred_unit=preferred_unit)
        annual = [
            row for row in rows
            if str(row.get("form") or "") in SEC_FORMS
            and str(row.get("fp") or "").upper() == "FY"
            and _number(row.get("val")) is not None
            and row.get("end")
        ]
        if not annual:
            continue
        # Amendments and later filings can repeat the same fiscal period.  Keep
        # the latest filed value for each end date, then return newest first.
        by_end: dict[str, dict[str, Any]] = {}
        for row in sorted(annual, key=lambda item: str(item.get("filed") or "")):
            by_end[str(row["end"])] = row
        return sorted(by_end.values(), key=lambda item: str(item["end"]), reverse=True), unit
    return [], None


def _latest_point(
    payload: dict[str, Any], concepts: Iterable[str], *, preferred_unit: str | None = None
) -> tuple[float | None, str | None, str | None]:
    allowed = SEC_FORMS | {"10-Q", "10-Q/A", "6-K", "6-K/A"}
    for concept in concepts:
        rows, unit = _unit_rows(payload, concept, preferred_unit=preferred_unit)
        candidates = [
            row for row in rows
            if str(row.get("form") or "") in allowed
            and _number(row.get("val")) is not None
            and row.get("end")
        ]
        if candidates:
            latest = max(
                candidates,
                key=lambda item: (str(item.get("end") or ""), str(item.get("filed") or "")),
            )
            return _number(latest.get("val")), str(latest.get("end")), unit
    return None, None, None


def normalize_company_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize conservative annual/point-in-time facts for ranking fallback."""
    revenue_rows, currency = _annual_values(payload, REVENUE_CONCEPTS)
    if not revenue_rows:
        return {}
    revenue = _number(revenue_rows[0].get("val"))
    prior_revenue = _number(revenue_rows[1].get("val")) if len(revenue_rows) > 1 else None
    report_date = str(revenue_rows[0].get("end") or "") or None

    net_rows, _ = _annual_values(payload, ("NetIncomeLoss", "ProfitLoss"), preferred_unit=currency)
    gross_rows, _ = _annual_values(payload, ("GrossProfit",), preferred_unit=currency)
    operating_rows, _ = _annual_values(payload, ("OperatingIncomeLoss",), preferred_unit=currency)
    cash_rows, _ = _annual_values(
        payload, ("NetCashProvidedByUsedInOperatingActivities",), preferred_unit=currency
    )
    capex_rows, _ = _annual_values(
        payload,
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForProceedsFromOtherPropertyPlantAndEquipment"),
        preferred_unit=currency,
    )
    net_income = _number(net_rows[0].get("val")) if net_rows else None
    prior_net_income = _number(net_rows[1].get("val")) if len(net_rows) > 1 else None
    gross_profit = _number(gross_rows[0].get("val")) if gross_rows else None
    operating_income = _number(operating_rows[0].get("val")) if operating_rows else None
    operating_cash_flow = _number(cash_rows[0].get("val")) if cash_rows else None
    capex = _number(capex_rows[0].get("val")) if capex_rows else None
    assets, assets_date, _ = _latest_point(payload, ("Assets",), preferred_unit=currency)
    liabilities, liabilities_date, _ = _latest_point(payload, ("Liabilities",), preferred_unit=currency)
    equity, equity_date, _ = _latest_point(
        payload,
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        preferred_unit=currency,
    )
    eps_rows, eps_unit = _annual_values(
        payload, ("EarningsPerShareDiluted", "EarningsPerShareBasic")
    )
    eps = _number(eps_rows[0].get("val")) if eps_rows and eps_unit == "USD/shares" else None
    shares, _, shares_unit = _latest_point(
        payload,
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        preferred_unit="shares",
    )
    if shares_unit != "shares":
        shares = None

    revenue_growth = None
    if revenue is not None and prior_revenue is not None and prior_revenue > 0:
        revenue_growth = round((revenue / prior_revenue - 1) * 100, 4)
    earnings_growth = None
    if net_income is not None and prior_net_income is not None and prior_net_income > 0:
        earnings_growth = round((net_income / prior_net_income - 1) * 100, 4)

    # Absolute values are safe to mix with US market-cap fields only when the
    # SEC filing reports USD.  Ratios remain valid in any consistently reported
    # currency, which matters for foreign issuers and ADRs.
    usd = currency == "USD"
    absolute_ocf = operating_cash_flow if usd else None
    free_cash_flow = (
        operating_cash_flow - abs(capex)
        if usd and operating_cash_flow is not None and capex is not None else None
    )
    debt_ratio = _percent(liabilities, assets)
    fields = {
        "revenue_yoy_pct": revenue_growth,
        "eps_yoy_pct": earnings_growth,
        "gross_margin_pct": _percent(gross_profit, revenue),
        "operating_margin_pct": _percent(operating_income, revenue),
        "roe_pct": _percent(net_income, equity),
        "debt_ratio_pct": debt_ratio,
        "operating_cash_flow": absolute_ocf,
        "operating_cash_flow_positive": (
            None if operating_cash_flow is None else float(operating_cash_flow > 0)
        ),
        "free_cash_flow": free_cash_flow,
        "eps": eps,
        # SEC fallback currently normalizes audited annual flow values.  Do not
        # mislabel them as TTM values used by the valuation-radar denominator.
        "total_revenue_ttm": None,
        "net_income_ttm": None,
        "shares_outstanding": shares,
    }
    available = sum(value is not None for value in fields.values())
    if not available:
        return {}
    return {
        **fields,
        "fundamental_available": float(revenue_growth is not None),
        "financial_quality_available": float(any(
            fields.get(key) is not None for key in (
                "eps_yoy_pct", "gross_margin_pct", "operating_margin_pct",
                "roe_pct", "debt_ratio_pct", "operating_cash_flow_positive",
            )
        )),
        "financial_report_date": report_date,
        "us_sec_balance_sheet_date": max(
            (value for value in (assets_date, liabilities_date, equity_date) if value),
            default=None,
        ),
        "us_sec_data_available": True,
        "us_sec_data_fields": available,
        "us_sec_data_source": "SEC EDGAR companyfacts",
        "us_sec_filing_currency": currency,
        "us_sec_official": True,
    }


def merge_sec_fallback(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Fill missing company fields from SEC without replacing primary values."""
    if not fallback or not fallback.get("us_sec_data_available"):
        return dict(primary)
    merged = dict(primary)
    for key in primary.get("us_sec_fallback_field_names") or []:
        if isinstance(key, str):
            merged.pop(key, None)
    filled = 0
    filled_names: list[str] = []
    metadata = {
        "financial_report_date", "us_sec_balance_sheet_date", "us_sec_data_available", "us_sec_data_fields",
        "us_sec_data_source", "us_sec_filing_currency", "us_sec_official",
        "fundamental_available", "financial_quality_available",
    }
    for key, value in fallback.items():
        if key in metadata:
            continue
        if merged.get(key) is None and value is not None:
            merged[key] = value
            filled += 1
            filled_names.append(key)
    if filled:
        for key in metadata:
            if fallback.get(key) is not None:
                merged[key] = fallback[key]
        merged["us_sec_fallback_used"] = True
        merged["us_sec_fallback_fields"] = filled
        merged["us_sec_fallback_field_names"] = filled_names
        primary_source = str(primary.get("us_company_data_source") or "").strip()
        sources = [
            part.strip() for part in primary_source.split("+")
            if part.strip() and part.strip() != "SEC EDGAR"
        ]
        merged["us_company_data_source"] = " + ".join([*sources, "SEC EDGAR"])
        merged["us_company_data_available"] = True
        merged["us_company_data_fields"] = sum(
            value is not None for key, value in merged.items()
            if key in {
                "per", "pbr", "dividend_yield", "revenue_yoy_pct", "eps_yoy_pct",
                "gross_margin_pct", "operating_margin_pct", "roe_pct", "debt_ratio_pct",
                "operating_cash_flow", "free_cash_flow", "eps", "market_cap",
                "enterprise_value", "total_revenue_ttm", "net_income_ttm", "total_cash",
                "total_debt", "shares_outstanding",
            }
        )
        merged["fundamental_available"] = float(any(
            merged.get(key) is not None
            for key in ("per", "pbr", "dividend_yield", "revenue_yoy_pct")
        ))
        merged["financial_quality_available"] = float(any(
            merged.get(key) is not None
            for key in (
                "eps_yoy_pct", "gross_margin_pct", "operating_margin_pct",
                "roe_pct", "debt_ratio_pct", "operating_cash_flow",
            )
        ))
    return merged


def fetch_sec_company_fundamentals(
    symbols: Iterable[str],
    *,
    cache_path: Path,
    timeout: int = 20,
    getter: Callable[..., Any] = requests.get,
    sleeper: Callable[[float], None] = time.sleep,
    max_requests_per_run: int = 24,
) -> dict[str, dict[str, Any]]:
    """Fetch once-daily SEC company facts with stale-cache preservation."""
    requested = sorted({str(symbol).upper().strip() for symbol in symbols if symbol})
    if not requested:
        return {}
    today = date.today()
    cache = _read_cache(cache_path)
    companies = cache.get("companies") if isinstance(cache.get("companies"), dict) else {}
    ticker_map = cache.get("ticker_map") if isinstance(cache.get("ticker_map"), dict) else {}
    ticker_age = _age_days(cache.get("ticker_map_updated_at"), today)
    if ticker_age is None or ticker_age >= 7 or not ticker_map:
        try:
            response = getter(TICKERS_URL, headers=_headers(), timeout=timeout)
            response.raise_for_status()
            refreshed = normalize_ticker_map(response.json())
            if refreshed:
                ticker_map = refreshed
                cache["ticker_map_updated_at"] = today.isoformat()
        except Exception as exc:
            LOG.warning("SEC ticker map unavailable; preserving cache: %s", exc)

    output: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, str]] = []
    for symbol in requested:
        item = companies.get(symbol) if isinstance(companies.get(symbol), dict) else {}
        age = _age_days(item.get("cached_at"), today)
        if age is not None and age < 1:
            if item.get("us_sec_data_available"):
                output[symbol] = {key: value for key, value in item.items() if key != "cached_at"}
            continue
        if item.get("us_sec_data_available"):
            # Keep the last complete official record available while this
            # bounded run refreshes only part of a large universe.
            output[symbol] = {key: value for key, value in item.items() if key != "cached_at"}
        cik = next((ticker_map.get(key) for key in _ticker_variants(symbol) if ticker_map.get(key)), None)
        if cik:
            pending.append((symbol, str(cik)))

    pending = pending[:max(0, int(max_requests_per_run))]
    for index, (symbol, cik) in enumerate(pending):
        try:
            response = getter(
                COMPANY_FACTS_URL.format(cik=cik),
                headers=_data_headers(),
                timeout=timeout,
            )
            response.raise_for_status()
            normalized = normalize_company_facts(response.json())
            if normalized:
                output[symbol] = normalized
                companies[symbol] = {**normalized, "cached_at": today.isoformat()}
            else:
                companies[symbol] = {
                    "us_sec_data_available": False,
                    "cached_at": today.isoformat(),
                }
        except Exception as exc:
            LOG.warning("SEC company facts unavailable for %s: %s", symbol, exc)
            stale = companies.get(symbol) if isinstance(companies.get(symbol), dict) else {}
            if stale.get("us_sec_data_available"):
                output[symbol] = {key: value for key, value in stale.items() if key != "cached_at"}
        if index + 1 < len(pending):
            sleeper(0.13)  # comfortably below the SEC's 10 requests/second limit

    cache["updated_at"] = today.isoformat()
    cache["ticker_map"] = ticker_map
    cache["companies"] = companies
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        LOG.debug("SEC EDGAR cache write failed: %s", exc)
    return output
