"""Taiwan-only market and peer context for layered stock scoring.

The individual stock remains the primary signal.  Broad-market and peer data
are explicit, bounded context layers so one index move can never make every
Taiwan stock look equally attractive.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Any


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _is_tw_stock(row: dict[str, Any]) -> bool:
    return (
        str(row.get("market") or "").upper() == "TW"
        and "ETF" not in str(row.get("type") or "").upper()
    )


def _exchange(row: dict[str, Any]) -> str:
    return "TPEx" if str(row.get("symbol") or "").upper().endswith(".TWO") else "TWSE"


def _index_change(
    market: dict[str, dict[str, Any]],
    name: str,
    session_date: str | None,
) -> float | None:
    item = market.get(name) or {}
    index_date = str(item.get("session_date") or "")
    if session_date and index_date != session_date:
        return None
    return _number(item.get("change_pct"))


def _breadth(rows: list[dict[str, Any]]) -> dict[str, Any]:
    changes = [
        value
        for row in rows
        if _is_tw_stock(row)
        for value in [_number(row.get("change_pct"))]
        if value is not None
    ]
    if not changes:
        return {
            "sample_count": 0,
            "up_ratio_pct": None,
            "median_change_pct": None,
        }
    return {
        "sample_count": len(changes),
        "up_ratio_pct": round(sum(value > 0 for value in changes) / len(changes) * 100, 1),
        "median_change_pct": round(float(median(changes)), 2),
    }


def _context_score(index_change: float | None, breadth: dict[str, Any]) -> float:
    """Bound index and breadth to a context score; neutral is explicit."""
    score = 50.0
    if index_change is not None:
        score += max(-18.0, min(18.0, index_change * 8.0))
    up_ratio = _number(breadth.get("up_ratio_pct"))
    if up_ratio is not None:
        score += max(-12.0, min(12.0, (up_ratio - 50.0) * 0.30))
    return round(_clamp(score), 1)


def build_tw_market_context(
    rows: list[dict[str, Any]],
    market: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build TWSE/TPEx context from same-session index and breadth data."""
    market = market or {}
    all_tw_rows = [row for row in rows if _is_tw_stock(row)]
    date_counts = Counter(
        str(row.get("official_session_date") or "")
        for row in all_tw_rows
        if str(row.get("official_session_date") or "")
    )
    session_date = date_counts.most_common(1)[0][0] if date_counts else None
    tw_rows = [
        row for row in all_tw_rows
        if session_date and str(row.get("official_session_date") or "") == session_date
    ]
    twse_rows = [row for row in tw_rows if _exchange(row) == "TWSE"]
    tpex_rows = [row for row in tw_rows if _exchange(row) == "TPEx"]
    overall_breadth = _breadth(tw_rows)
    twse_breadth = _breadth(twse_rows)
    tpex_breadth = _breadth(tpex_rows)
    twse_change = _index_change(market, "加權指數", session_date)
    tpex_change = _index_change(market, "櫃買指數", session_date)

    # The index feed is preferred, but a sufficiently broad set of same-day
    # individual returns is itself real market evidence. This fallback is
    # deliberately unavailable for small samples so a handful of candidates
    # can never masquerade as the whole market.
    overall_available = bool(session_date and overall_breadth["sample_count"] >= 20)
    twse_available = bool(session_date and twse_breadth["sample_count"] >= 10)
    # If the OTC index feed is temporarily unavailable, a sufficiently broad
    # same-session TPEx universe is still useful, but is labelled as breadth-only.
    tpex_available = bool(
        tpex_breadth["sample_count"] >= 10
        and (tpex_change is not None or overall_available)
    )

    overall_score = _context_score(twse_change, overall_breadth)
    twse_score = _context_score(twse_change, twse_breadth)
    tpex_reference = tpex_change if tpex_change is not None else twse_change
    tpex_score = _context_score(tpex_reference, tpex_breadth)
    return {
        "available": overall_available,
        "session_date": session_date,
        "score": overall_score,
        "index_change_pct": twse_change,
        "breadth": overall_breadth,
        "exchanges": {
            "TWSE": {
                "available": twse_available,
                "score": twse_score,
                "index_change_pct": twse_change,
                "breadth": twse_breadth,
                "source": (
                    "加權指數＋上市候選股市場廣度"
                    if twse_change is not None
                    else "上市候選股市場廣度（加權指數暫缺）"
                ),
            },
            "TPEx": {
                "available": tpex_available,
                "score": tpex_score,
                "index_change_pct": tpex_change,
                "breadth": tpex_breadth,
                "source": (
                    "櫃買指數＋上櫃候選股市場廣度"
                    if tpex_change is not None
                    else "上櫃候選股市場廣度（櫃買指數暫缺）"
                ),
            },
        },
        "source": (
            "台股市場指數＋同交易日市場廣度"
            if twse_change is not None
            else "同交易日市場廣度（加權指數暫缺）"
        ),
    }


def attach_tw_context(
    rows: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach bounded market and relative peer context to Taiwan rows only."""
    context = context or {}
    context_date = str(context.get("session_date") or "")
    tw_assets = [
        row for row in rows
        if str(row.get("market") or "").upper() == "TW"
        and context_date
        and str(row.get("official_session_date") or "") == context_date
    ]
    tw_stocks = [row for row in tw_assets if _is_tw_stock(row)]
    exchange_medians: dict[str, float] = {}
    exchange_return_medians: dict[tuple[str, str, int], float | None] = {}
    for exchange in ("TWSE", "TPEx"):
        changes = [
            value
            for row in tw_stocks
            if _exchange(row) == exchange
            for value in [_number(row.get("change_pct"))]
            if value is not None
        ]
        exchange_medians[exchange] = float(median(changes)) if changes else 0.0
        for asset_class in ("STOCK", "ETF"):
            minimum = 10 if asset_class == "STOCK" else 3
            for horizon in (5, 20):
                returns = [
                    value
                    for row in tw_assets
                    if _exchange(row) == exchange
                    and (("ETF" in str(row.get("type") or "").upper()) == (asset_class == "ETF"))
                    for value in [_number(row.get(f"tw_return_{horizon}d_pct"))]
                    if value is not None
                ]
                exchange_return_medians[(exchange, asset_class, horizon)] = (
                    float(median(returns)) if len(returns) >= minimum else None
                )

    peer_changes: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    peer_returns: dict[tuple[str, str, str, int], list[tuple[str, float]]] = {}
    for row in tw_assets:
        asset_class = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
        industry = str(row.get("industry") or row.get("theme") or "其他")
        value = _number(row.get("change_pct"))
        if value is not None:
            peer_changes.setdefault((_exchange(row), asset_class, industry), []).append(
                (str(row.get("symbol") or ""), value)
            )
        for horizon in (5, 20):
            return_value = _number(row.get(f"tw_return_{horizon}d_pct"))
            if return_value is not None:
                peer_returns.setdefault((_exchange(row), asset_class, industry, horizon), []).append(
                    (str(row.get("symbol") or ""), return_value)
                )

    exchanges = context.get("exchanges") or {}
    for row in rows:
        if str(row.get("market") or "").upper() != "TW":
            continue
        exchange = _exchange(row)
        exchange_context = exchanges.get(exchange) or {}
        row_date = str(row.get("official_session_date") or "")
        same_session = bool(context_date and row_date == context_date)
        market_available = bool(exchange_context.get("available") and same_session)
        market_score = _number(exchange_context.get("score"))
        if market_score is None:
            market_score = 50.0

        asset_class = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
        industry = str(row.get("industry") or row.get("theme") or "其他")
        symbol = str(row.get("symbol") or "")
        peers = [
            value for peer_symbol, value
            in peer_changes.get((exchange, asset_class, industry), [])
            if peer_symbol != symbol
        ]
        peer_available = bool(same_session and len(peers) >= 2)
        peer_median = float(median(peers)) if peer_available else 0.0
        relative_change = peer_median - exchange_medians.get(exchange, 0.0)
        peer_score = round(_clamp(50.0 + relative_change * 12.0), 1)

        relative_components: list[float] = []
        market_relative_count = 0
        sector_relative_count = 0
        relative_fields: dict[str, float | None] = {}
        for horizon, multiplier in ((5, 4.0), (20, 2.0)):
            stock_return = _number(row.get(f"tw_return_{horizon}d_pct"))
            market_median = exchange_return_medians.get((exchange, asset_class, horizon))
            horizon_peers = [
                peer_value
                for peer_symbol, peer_value
                in peer_returns.get((exchange, asset_class, industry, horizon), [])
                if peer_symbol != symbol
            ]
            sector_median = float(median(horizon_peers)) if len(horizon_peers) >= 2 else None
            market_relative = (
                stock_return-market_median
                if stock_return is not None and market_median is not None else None
            )
            sector_relative = (
                stock_return-sector_median
                if stock_return is not None and sector_median is not None else None
            )
            relative_fields[f"tw_market_relative_{horizon}d_pct"] = (
                round(market_relative, 2) if market_relative is not None else None
            )
            relative_fields[f"tw_sector_relative_{horizon}d_pct"] = (
                round(sector_relative, 2) if sector_relative is not None else None
            )
            for relative in (market_relative, sector_relative):
                if relative is not None:
                    relative_components.append(_clamp(50.0+relative*multiplier))
            market_relative_count += int(market_relative is not None)
            sector_relative_count += int(sector_relative is not None)
        relative_available = bool(same_session and len(relative_components) >= 2)
        relative_score = (
            round(sum(relative_components)/len(relative_components), 1)
            if relative_components else None
        )
        if not same_session:
            relative_status = "交易日不一致，不使用相對強弱"
        elif relative_score is None:
            relative_status = "同市場／族群歷史資料不足"
        elif relative_score >= 65:
            relative_status = (
                "明顯領先同市場與族群"
                if market_relative_count and sector_relative_count else
                "明顯領先同市場" if market_relative_count else "明顯領先同族群"
            )
        elif relative_score >= 55:
            relative_status = "相對強勢"
        elif relative_score <= 35:
            relative_status = (
                "明顯落後同市場與族群"
                if market_relative_count and sector_relative_count else
                "明顯落後同市場" if market_relative_count else "明顯落後同族群"
            )
        elif relative_score <= 45:
            relative_status = "相對弱勢"
        else:
            relative_status = "相對表現中性"

        row.update({
            "tw_exchange": exchange,
            "tw_market_context_available": market_available,
            "tw_market_context_score": round(market_score, 1),
            "tw_market_context_source": exchange_context.get("source"),
            "tw_market_context_session_date": context_date or None,
            "tw_market_index_change_pct": exchange_context.get("index_change_pct"),
            "tw_market_breadth_up_pct": (exchange_context.get("breadth") or {}).get("up_ratio_pct"),
            "tw_sector_context_available": peer_available,
            "tw_sector_context_score": peer_score,
            "tw_sector_peer_count": len(peers),
            "tw_sector_median_change_pct": round(peer_median, 2) if peers else None,
            "tw_sector_relative_change_pct": round(relative_change, 2) if peers else None,
            "tw_relative_strength_available": relative_available,
            "tw_relative_strength_score": relative_score,
            "tw_relative_strength_status": relative_status,
            **relative_fields,
            "tw_context_affects_us": False,
        })
    return rows
