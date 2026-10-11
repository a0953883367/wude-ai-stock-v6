"""Offline independent census check for existing corporate-actions CI artifacts.

Read raw configured sources directly, without reusing the report producer's
_tracked_securities or _combined_active_payload normalization. No network calls,
source-health relaxation, universe edits, or investment eligibility decisions.
"""

from collections import Counter
import json
from pathlib import Path
from typing import Any


def load_json_unique(path: Path) -> Any:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def _identity(market: Any, symbol: Any) -> tuple[str, str]:
    if not isinstance(market, str) or not isinstance(symbol, str):
        raise ValueError("market and symbol must be strings")
    market = {"🇹🇼 台灣": "TW", "🇺🇸 美國": "US", "TW": "TW", "US": "US"}.get(market.strip())
    symbol = symbol.strip().upper()
    if not market or not symbol or (market == "TW") != symbol.endswith((".TW", ".TWO")):
        raise ValueError("invalid configured market/symbol")
    return market, symbol


def expected_census(search: dict[str, Any], watchlist_rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Union raw source identities; duplicates within either source are errors."""
    if not isinstance(search, dict) or not isinstance(search.get("data"), list):
        raise ValueError("search data must contain a list")
    if not isinstance(watchlist_rows, list) or not watchlist_rows or not search["data"]:
        raise ValueError("both configured source pools must be nonempty")
    expected: dict[tuple[str, str], str] = {}
    for rows, market_field, symbol_field, type_field in (
        (search["data"], "市場", "代號", "類型"),
        (watchlist_rows, "market", "symbol", "type"),
    ):
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("source row must be an object")
            key = _identity(row.get(market_field), row.get(symbol_field))
            if key in seen:
                raise ValueError(f"duplicate configured identity: {key}")
            seen.add(key)
            kind = {"個股": "STOCK", "ETF": "ETF"}.get(row.get(type_field))
            if kind is None:
                raise ValueError(f"unknown configured stock/ETF classification: {key}")
            if key in expected and expected[key] != kind:
                raise ValueError(f"conflicting configured stock/ETF classification: {key}")
            expected[key] = kind
    return expected


def validate_corporate_census(
    search: dict[str, Any], watchlist_rows: list[dict[str, Any]],
    report: dict[str, Any], registry: dict[str, Any],
) -> dict[str, int]:
    expected = expected_census(search, watchlist_rows)
    records = registry.get("records") if isinstance(registry, dict) else None
    if not isinstance(records, dict):
        raise ValueError("corporate registry records must be an object")
    actual: dict[tuple[str, str], dict[str, Any]] = {}
    for symbol, row in records.items():
        if not isinstance(row, dict):
            raise ValueError("corporate registry row must be an object")
        key = _identity(row.get("market"), row.get("symbol"))
        if symbol != key[1] or key in actual:
            raise ValueError("registry identity mismatch or duplicate")
        if key not in expected:
            raise ValueError(f"unexpected corporate registry identity: {key}")
        if row.get("asset_type") != expected[key]:
            raise ValueError(f"corporate stock/ETF classification mismatch: {key}")
        if row.get("present") is not None and type(row["present"]) is not bool:
            raise ValueError("registry present must be bool or null")
        actual[key] = row
    missing = sorted(expected.keys() - actual.keys())
    if missing:
        raise ValueError(f"missing corporate registry identities: {missing}")
    kinds = Counter(expected.values())
    matched_kinds = Counter(expected[key] for key, row in actual.items() if row.get("present") is True)
    matched = sum(matched_kinds.values())
    counts = {
        "tracked_total": len(expected),
        "tracked_stocks": kinds["STOCK"],
        "tracked_etfs": kinds["ETF"],
        "officially_matched": matched,
        "officially_matched_stocks": matched_kinds["STOCK"],
        "officially_matched_etfs": matched_kinds["ETF"],
        "unmatched": len(expected) - matched,
    }
    summary = report.get("summary") if isinstance(report, dict) else None
    if not isinstance(summary, dict):
        raise ValueError("corporate report summary is required")
    for name, wanted in counts.items():
        if type(summary.get(name)) is not int or summary[name] != wanted:
            raise ValueError(f"corporate summary {name} mismatch: {summary.get(name)} != {wanted}")
    return counts
