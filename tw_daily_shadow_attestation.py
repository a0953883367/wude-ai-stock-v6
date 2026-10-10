"""Re-attest a frozen Taiwan candle using the existing official bulk providers.

Only the two price datasets already consumed by ``tw_official_data`` are read.
No model, historical price producer, quote endpoint, broker, or Yahoo is called.
Returned rows are private shadow copies: callers must never save them over the
formal analysis. A newer or different candle cannot repair a frozen candle.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any, Callable

from tw_official_data import TPEX_BASE, TWSE_BASE, _get_json

VERSION = "TW-OFFICIAL-FROZEN-CANDLE-V1"
SOURCES = {
    "TWSE OpenAPI": {
        "url": f"{TWSE_BASE}/exchangeReport/STOCK_DAY_ALL", "suffix": ".TW",
        "symbol": "Code", "fields": ("OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice", "TradeVolume"),
    },
    "TPEx OpenAPI": {
        "url": f"{TPEX_BASE}/tpex_mainboard_daily_close_quotes", "suffix": ".TWO",
        "symbol": "SecuritiesCompanyCode", "fields": ("Open", "High", "Low", "Close", "TradingShares"),
    },
}
OHLCV = ("open", "high", "low", "close", "volume")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value).strip().replace(",", ""))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _session(value: Any) -> str | None:
    text = str(value or "").strip().replace("/", "").replace("-", "")
    if len(text) == 7 and text.isdigit():
        text = str(int(text[:3]) + 1911) + text[3:]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:])).isoformat()
    except ValueError:
        return None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parse_official_price_rows(source: str, payload: Any, *, fetched_at: str) -> dict[str, dict[str, Any]]:
    """Normalize only complete, internally consistent official daily candles.

    Duplicate symbols are excluded, even when the duplicated rows look equal.
    ``fetched_at`` means retrieval time, never the exchange's publication time.
    """
    spec = SOURCES.get(source) if isinstance(source, str) else None
    if spec is None or not isinstance(payload, list):
        return {}
    try:
        observed = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return {}
        payload_digest = _digest(payload)
    except (TypeError, ValueError):
        return {}
    records, seen, duplicates = {}, set(), set()
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get(spec["symbol"]) or "").strip()
        if not re.fullmatch(r"[0-9][0-9A-Z]{2,7}", sid):
            continue
        symbol = sid + spec["suffix"]
        if symbol in seen:
            duplicates.add(symbol)
            continue
        seen.add(symbol)
        session = _session(raw.get("Date"))
        values = [_decimal(raw.get(field)) for field in spec["fields"]]
        if not session or any(value is None for value in values):
            continue
        op, high, low, close, volume = values
        if (not all(math.isfinite(float(value)) for value in values)
                or min(op, high, low, close) <= 0 or volume < 0 or volume != volume.to_integral_value()
                or high < max(op, low, close) or low > min(op, high, close)):
            continue
        records[symbol] = {
            "symbol": symbol, "market": "TW", "source": source,
            "source_url": spec["url"], "source_session_date": session,
            "fetched_at": observed.isoformat(), "unit": "TWD/shares", "interval": "1d",
            "ohlcv": {key: (int(value) if key == "volume" else float(value)) for key, value in zip(OHLCV, values)},
            "raw_record": dict(raw), "raw_record_sha256": _digest(raw),
            "source_payload_sha256": payload_digest,
        }
    return {symbol: record for symbol, record in records.items() if symbol not in duplicates}


def attest_frozen_tw_row(row: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shadow copy; never replace formal prices, dates, ranks, or models.

    Exact frozen OHLC and any supplied volume must match the raw exchange row.
    A missing historical volume can be supplied only after this identity match.
    Quote price is retained separately and is not relabelled as candle close.
    """
    result = dict(row)
    if str(row.get("market") or "").upper() != "TW":
        return result
    proof = {
        "version": VERSION, "shadow_only": True, "status": "blocked",
        "reason": "official_record_unavailable", "symbol": row.get("symbol"),
        "frozen_session_date": row.get("official_session_date"),
        "quote_price": row.get("price"), "formal_fields_unchanged": True,
    }
    result.update(source_daily_ohlcv_complete=False, source_daily_ohlcv_session_date=None,
                  shadow_daily_ohlcv_proof=proof)
    if not isinstance(record, dict):
        return result
    source = record.get("source")
    spec = SOURCES.get(source) if isinstance(source, str) else None
    # Re-parse raw evidence at the trust boundary; a forged normalized candle or
    # a copied source label alone must never create an attestation.
    reparsed = parse_official_price_rows(source, [record.get("raw_record")], fetched_at=record.get("fetched_at"))
    parsed = reparsed.get(str(row.get("symbol") or "").upper())
    if (not spec or not parsed or record.get("source_url") != spec["url"]
            or record.get("symbol") != parsed["symbol"] or record.get("market") != "TW"
            or record.get("unit") != "TWD/shares" or record.get("interval") != "1d"
            or record.get("source_session_date") != parsed["source_session_date"]
            or record.get("ohlcv") != parsed["ohlcv"]
            or any(_decimal((record.get("ohlcv") or {}).get(key)) != _decimal(parsed["ohlcv"][key]) for key in OHLCV)
            or record.get("raw_record_sha256") != parsed["raw_record_sha256"]):
        proof["reason"] = "invalid_official_record"
        return result
    proof.update({key: record[key] for key in ("source", "source_url", "source_session_date", "fetched_at", "unit", "interval", "ohlcv", "raw_record", "raw_record_sha256", "source_payload_sha256") if key in record})
    session = row.get("official_session_date")
    if not session or session != record["source_session_date"]:
        proof["reason"] = "frozen_session_mismatch"
        return result
    previous_session = row.get("tw_official_session_date")
    if previous_session is not None and previous_session != session:
        proof["reason"] = "previous_source_session_mismatch"
        return result
    previous_source = row.get("tw_price_source")
    if previous_source is not None and previous_source != source:
        proof["reason"] = "previous_source_mismatch"
        return result
    if row.get("tw_price_unit") not in (None, "TWD/shares"):
        proof["reason"] = "previous_unit_mismatch"
        return result
    mismatches = [field for field in OHLCV[:4]
                  if _decimal(row.get(f"official_{field}_price")) != _decimal(record["ohlcv"][field])]
    if row.get("official_volume") is not None and _decimal(row["official_volume"]) != _decimal(record["ohlcv"]["volume"]):
        mismatches.append("volume")
    if mismatches:
        proof.update(reason="frozen_ohlcv_mismatch", mismatched_fields=mismatches)
        return result
    result.update(
        source_daily_ohlcv_complete=True, source_daily_ohlcv_session_date=session,
        official_volume=record["ohlcv"]["volume"],
        tw_official_price_available=True, tw_official_session_date=session,
        tw_price_source=source, tw_price_unit="TWD/shares",
    )
    proof.update(status="attested", reason="exact_frozen_candle_match")
    return result


def fetch_official_price_records(symbols: set[str], *,
                                 fetch_json: Callable[[str], Any] | None = None,
                                 now: datetime | None = None) -> tuple[dict, list]:
    """Read existing official bulk price feeds; raw records remain in memory."""
    if now is not None and now.tzinfo is None:
        raise ValueError("now must include timezone")
    fetcher = fetch_json or _get_json
    symbols = {str(symbol).upper() for symbol in symbols}
    requested = [source for source, spec in SOURCES.items() if any(symbol.endswith(spec["suffix"]) for symbol in symbols)]
    def fetch(source):
        spec = SOURCES[source]
        try:
            payload = fetcher(spec["url"])
            retrieved_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
            records = parse_official_price_rows(source, payload, fetched_at=retrieved_at)
            audit = {"source": source, "source_url": spec["url"], "fetched_at": retrieved_at,
                     "status": "available" if records else "unavailable",
                     "valid_record_count": len(records),
                     "source_session_dates": sorted({record["source_session_date"] for record in records.values()})}
            return records, audit
        except Exception as exc:
            return {}, {"source": source, "source_url": spec["url"], "status": "unavailable",
                        "error_type": type(exc).__name__, "valid_record_count": 0}
    records, sources = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for parsed, audit in pool.map(fetch, requested):
            records.update(parsed)
            sources.append(audit)
    return records, sources


def enrich_frozen_tw_rows(rows: list[dict[str, Any]], *,
                          fetch_json: Callable[[str], Any] | None = None,
                          now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch at most two bulk datasets, returning shadow copies plus fetch audit.

    Uses the existing bounded request/retry policy. Provider failures stay
    isolated and fail closed; no per-symbol backfill or fallback is performed.
    """
    symbols = {str(row.get("symbol") or "").upper() for row in rows
               if str(row.get("market") or "").upper() == "TW"}
    records, sources = fetch_official_price_records(symbols, fetch_json=fetch_json, now=now)
    enriched = [attest_frozen_tw_row(row, records.get(str(row.get("symbol") or "").upper())) for row in rows]
    reasons = Counter(row["shadow_daily_ohlcv_proof"]["reason"] for row in enriched if str(row.get("market") or "").upper() == "TW")
    return enriched, {"version": VERSION, "shadow_only": True, "sources": sources,
                      "tw_row_count": sum(reasons.values()),
                      "attested_count": reasons.get("exact_frozen_candle_match", 0),
                      "reason_counts": dict(sorted(reasons.items())), "formal_rows_rewritten": False}
