"""Pure, fail-closed report-only extraction from already fetched official rows.

This module has no I/O. It never expands the formal universe or manufactures a
bar. A response list has no completeness contract: absence is not delisting.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import date
from decimal import Decimal
from typing import Any


_MAX_NUMERIC_CHARACTERS = 128
_MAX_INTEGER_BITS = 256

_LICENSE = {
    "name": "Open Government Data License, version 1.0",
    "url": "https://data.gov.tw/license",
}
_SOURCES = {
    "TWSE": {
        "code": "2412", "instrument_type": "ordinary_stock",
        "source": "TWSE OpenAPI", "agency": "Taiwan Stock Exchange",
        "source_url": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "dataset_url": "https://data.gov.tw/dataset/11549",
        "dataset_name": "上市個股日成交資訊",
        "price_unit": "TWD/share", "volume_unit": "shares",
        "fields": {
            "code": "Code", "session_date": "Date", "open": "OpeningPrice",
            "high": "HighestPrice", "low": "LowestPrice",
            "close": "ClosingPrice", "volume": "TradeVolume",
        },
    },
    "TPEx": {
        "code": "00998A", "instrument_type": "ETF",
        "source": "TPEx OpenAPI", "agency": "Taipei Exchange",
        "source_url": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        "dataset_url": "https://data.gov.tw/dataset/11370",
        "dataset_name": "上櫃股票行情",
        "price_unit": "TWD/beneficial_unit", "volume_unit": "beneficial_units",
        "fields": {
            "code": "SecuritiesCompanyCode", "session_date": "Date",
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "TradingShares",
        },
    },
}


def _code(value: Any) -> str | None:
    # Never cast a numeric ID, strip a suffix, pad zeros, or uppercase a typo.
    return value.strip() if isinstance(value, str) else None


def _session(value: Any) -> str | None:
    """Accept real YYYYMMDD / ROC YYYMMDD dates, with optional / or - separators.

    A timestamp (including an update time) is deliberately not a session date.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = re.fullmatch(r"([0-9]{3,4})([-/])([0-9]{2})\2([0-9]{2})", text)
    if match:
        year, month, day = match.group(1), match.group(3), match.group(4)
    elif re.fullmatch(r"[0-9]{7,8}", text):
        year, month, day = text[:-4], text[-4:-2], text[-2:]
    else:
        return None
    try:
        return date(int(year) + (1911 if len(year) == 3 else 0), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _numeric(value: Any, *, volume: bool = False) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, int) and value.bit_length() > _MAX_INTEGER_BITS:
        return None
    text = str(value).strip()
    if len(text) > _MAX_NUMERIC_CHARACTERS:
        return None
    if not re.fullmatch(r"\+?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text):
        return None
    if volume:
        # Float would round large integers or underflow tiny fractions to zero.
        exact = Decimal(text.replace(",", ""))
        return int(exact) if exact >= 0 and exact == exact.to_integral_value() else None
    number = float(text.replace(",", ""))
    return number if math.isfinite(number) and number > 0 else None


def _raw(value: Any) -> Any:
    """Keep JSON scalar evidence without leaking a nonstandard NaN into JSON."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return {"invalid_type": "non_utf8_string"}
    if isinstance(value, int) and value.bit_length() > _MAX_INTEGER_BITS:
        return {"invalid_type": "out_of_range_integer"}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return {"invalid_type": type(value).__name__}


def _candidate(row: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    values, provenance = {}, {}
    for name, source_field in fields.items():
        raw = row.get(source_field)
        if name == "code":
            value = _code(raw)
        elif name == "session_date":
            value = _session(raw)
        else:
            value = _numeric(raw, volume=name == "volume")
        values[name] = value
        provenance[name] = {
            "source_field": source_field,
            "present": source_field in row,
            "raw_value": _raw(raw),
            "valid": value is not None,
        }
    reason = None
    if values["close"] is None:
        reason = "missing_or_invalid_official_close"
    elif values["session_date"] is None:
        reason = "missing_or_invalid_source_session"
    else:
        high, low = values["high"], values["low"]
        known = [values[key] for key in ("open", "close") if values[key] is not None]
        if (high is not None and any(high < value for value in known)) or (
            low is not None and any(low > value for value in known)
        ) or (high is not None and low is not None and high < low):
            reason = "inconsistent_raw_ohlc"
    return {"values": values, "raw_fields": provenance, "reason": reason}


class ReportOnlyQuoteCollector:
    """One refresh only; deliberately has no previous-cache input."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._responses: dict[str, dict[str, Any]] = {}
        for market, spec in _SOURCES.items():
            self._records[spec["code"]] = {
                "code": spec["code"], "market": market,
                "instrument_type": spec["instrument_type"],
                "source": spec["source"], "source_url": spec["source_url"],
                "dataset_url": spec["dataset_url"], "dataset_name": spec["dataset_name"],
                "dataset_version": None,  # Not supplied by the row API.
                "data_providing_agency": "金融監督管理委員會證券期貨局",
                "exchange": spec["agency"], "attribution_year": None,
                "license": dict(_LICENSE),
                "attribution": f"金融監督管理委員會證券期貨局 / {spec['agency']} / {spec['dataset_name']}; source session as recorded; dataset revision not supplied",
                "price_basis": "official_close", "price_unit": spec["price_unit"],
                "volume_unit": spec["volume_unit"], "session_date": None,
                "status": "not_observed", "reason": "market_not_fetched",
                "matched_row_count": 0, "quote": None, "raw_fields": None,
            }
            self._responses[market] = {
                "status": "not_observed", "received_row_count": None,
                "completeness": "not_verified",
            }

    def observe(self, market: str, rows: Any) -> None:
        spec = _SOURCES[market]
        record = self._records[spec["code"]]
        # Do not let a repeated observe call carry a prior quote forward.
        record.update(session_date=None, attribution_year=None, quote=None, raw_fields=None, matched_row_count=0)
        response = self._responses[market]
        response["received_row_count"] = len(rows) if isinstance(rows, list) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            response["status"] = "malformed_response"
            record.update(status="unavailable", reason="malformed_response")
            return
        response["status"] = "received_rows" if rows else "empty_or_unavailable"
        matches = [row for row in rows if _code(row.get(spec["fields"]["code"])) == spec["code"]]
        record["matched_row_count"] = len(matches)
        if not matches:
            record.update(status="missing", reason="absent_from_received_rows" if rows else "empty_or_unavailable")
            return
        candidates = [_candidate(row, spec["fields"]) for row in matches]
        first = candidates[0]
        # Compare field-presence evidence too. Never choose a last/first winner
        # or merge two incomplete rows, even when their closes happen to agree.
        uncertain_evidence = any(
            isinstance(field["raw_value"], dict)
            for item in candidates for field in item["raw_fields"].values()
        )
        if any(item != first for item in candidates[1:]) or (
            len(candidates) > 1 and uncertain_evidence
        ):
            record.update(status="conflict", reason="conflicting_duplicate_rows")
            return
        record["raw_fields"] = first["raw_fields"]
        record["session_date"] = first["values"]["session_date"]
        record["attribution_year"] = record["session_date"][:4] if record["session_date"] else None
        if first["reason"]:
            record.update(status="invalid", reason=first["reason"])
            return
        record.update(
            status="available", reason=None,
            quote={key: first["values"][key] for key in ("open", "high", "low", "close", "volume")},
        )

    def snapshot(self, observed_at: str, *, refresh_failed: bool = False) -> dict[str, Any]:
        records = copy.deepcopy(self._records)
        if refresh_failed:
            for record in records.values():
                record.update(status="unavailable", reason="refresh_failed", quote=None, session_date=None, attribution_year=None, raw_fields=None)
        return {
            "schema_version": 1, "scope": "report_only",
            "formal_model_input": False, "ranking_eligible": False,
            "observed_at": observed_at, "refresh_status": "failed" if refresh_failed else "completed",
            "session_policy": "source_Date_only; never observed_at or cache updated_at",
            "response_completeness": "not_verified",
            "responses": copy.deepcopy(self._responses), "records": records,
        }
