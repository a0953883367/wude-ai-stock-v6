"""Build searchable publication rows without changing formal rankings."""

from __future__ import annotations

import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _pending_row(candidate: dict, official_prices: dict) -> dict:
    symbol = str(candidate.get("代號") or "").strip().upper()
    stock_id = symbol.split(".")[0]
    official = official_prices.get(stock_id, {})
    if not isinstance(official, dict):
        official = {}
    close = official.get("close")
    return {
        "symbol": symbol,
        "name": str(candidate.get("股票") or symbol),
        "market": "TW" if symbol.endswith((".TW", ".TWO")) else "US",
        "type": str(candidate.get("類型") or "個股"),
        "theme": str(candidate.get("主題") or "其他"),
        "industry": str(candidate.get("次產業") or "其他"),
        "price": close if isinstance(close, (int, float)) else None,
        "official_session_date": official.get("date"),
        "tw_official_price_available": official.get(
            "tw_official_price_available"
        ) is True,
        "tw_price_source": official.get("tw_price_source"),
        "tw_price_unit": official.get("tw_price_unit"),
        "score": None,
        "entry_score": None,
        "overall_display_rank": None,
        "overall_rank": None,
        "overall_rank_tier": 0,
        "overall_ranking_score": None,
        "overall_eligible": False,
        "ranking_pending": True,
        "ranking_status": "insufficient_history",
        "ranking_status_label": "新掛牌／歷史資料累積中",
        "action": "⚪ 資料累積中，未滿20個交易日，暫無正式排名",
        "risk": "資料不足，不列入正式排名或自動下單",
        "trade_guard_blocked": True,
        "formal_ranking_unchanged": True,
    }


def build_publication_rows(source: dict, root: Path = Path(".")) -> list[dict]:
    """Append searchable pending candidates after the untouched ranked rows.

    Formal ranking rows stay byte-for-byte equivalent as dictionaries.  The
    extra rows exist only in owner/friend publication snapshots and remain
    ineligible for ranks, model learning, simulations, and orders.
    """
    ranked = source.get("data")
    if not isinstance(ranked, list) or not ranked:
        raise RuntimeError("reports/all_analysis.json has no publishable rows")
    rows = [dict(row) for row in ranked if isinstance(row, dict)]
    seen = {
        str(row.get("symbol") or "").strip().upper()
        for row in rows
        if row.get("symbol")
    }

    explicit = source.get("pending_candidates")
    if isinstance(explicit, list):
        for row in explicit:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol and symbol not in seen:
                rows.append(dict(row))
                seen.add(symbol)

    # Compatibility path for the current completed report: search_data is the
    # maintained active catalog, while the next report will write the explicit
    # pending_candidates field itself.
    catalog = _load_json(root / "search_data.json").get("data", [])
    cache = _load_json(root / "reports" / "tw_official_cache.json")
    official_prices = cache.get("data", {}).get("prices", {})
    if not isinstance(official_prices, dict):
        official_prices = {}
    if isinstance(catalog, list):
        for candidate in catalog:
            if not isinstance(candidate, dict):
                continue
            symbol = str(candidate.get("代號") or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            rows.append(_pending_row(candidate, official_prices))
            seen.add(symbol)
    return rows
