"""Forward-only comprehensive shadow rankings for the Central AI hub.

The formal V6 output is the immutable baseline.  This module applies small,
transparent and horizon-specific overlays from the existing shadow evidence,
then freezes top-10 snapshots for later 20/60-session comparison.  It never
writes formal scores, weights, recommendations, or broker instructions.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MODEL_VERSION = "COMPREHENSIVE-SHADOW-V1"
HORIZONS = ("short", "medium", "long")
HORIZON_LABELS = {"short": "1～5 日", "medium": "45 日", "long": "6 個月"}
MAX_TOTAL_ADJUSTMENT = {"short": 8.0, "medium": 6.0, "long": 6.0}

# These are deliberately provisional and bounded.  They can be promoted only
# after the separate 60-session validation and manual graduation decision.
SOURCE_LIMITS = {
    "valuation_shadow": {"short": 1.0, "medium": 2.0, "long": 5.0},
    "inverse_etf_shadow": {"short": 3.0, "medium": 2.0, "long": 0.0},
    "rotation_shadow": {"short": 3.0, "medium": 2.0, "long": 1.0},
    "capital_flow_shadow": {"short": 3.0, "medium": 0.0, "long": 0.0},
}
SOURCE_LABELS = {
    "valuation_shadow": "估值風險",
    "inverse_etf_shadow": "反向 ETF",
    "rotation_shadow": "族群輪動",
    "capital_flow_shadow": "大量買賣資金流",
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _bounded(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    number = _number(value, low)
    return round(max(low, min(high, float(number))), 2)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _direction_factor(evidence: dict[str, Any]) -> float:
    direction = str(evidence.get("direction") or "missing")
    strength = _bounded(evidence.get("strength"))
    confidence = _bounded(evidence.get("confidence")) / 100.0
    source = str(evidence.get("source_id") or "")
    if direction == "support":
        raw = max(0.0, (strength - 50.0) / 50.0)
    elif direction == "oppose":
        raw = -(
            strength / 100.0
            if source in {"valuation_shadow", "inverse_etf_shadow", "capital_flow_shadow"}
            else max(0.0, (50.0 - strength) / 50.0)
        )
    else:
        return 0.0
    return raw * confidence


def _source_adjustment(evidence: dict[str, Any], horizon: str) -> float:
    source = str(evidence.get("source_id") or "")
    limit = float((SOURCE_LIMITS.get(source) or {}).get(horizon) or 0.0)
    if not limit or str(evidence.get("direction") or "") == "missing":
        return 0.0
    return round(max(-limit, min(limit, _direction_factor(evidence) * limit)), 2)


def _baseline(item: dict[str, Any], horizon: str) -> tuple[float, float]:
    base = (item.get("shadow_baseline") or {}).get(horizon) or {}
    current = (item.get("horizons") or {}).get(horizon) or {}
    score = _number(base.get("score"), _number(current.get("score"), 0.0)) or 0.0
    confidence = _number(
        base.get("confidence"), _number(current.get("confidence"), 0.0)
    ) or 0.0
    return _bounded(score), _bounded(confidence)


def _rank_item(item: dict[str, Any], horizon: str) -> dict[str, Any]:
    base_score, base_confidence = _baseline(item, horizon)
    evidence_by_source = {
        str(row.get("source_id") or ""): row
        for row in item.get("evidence") or []
        if isinstance(row, dict)
    }
    adjustments = []
    for source, limits in SOURCE_LIMITS.items():
        evidence = evidence_by_source.get(source)
        applicable = bool(float(limits.get(horizon) or 0.0))
        available = bool(
            evidence and str(evidence.get("direction") or "missing") != "missing"
            and _number(evidence.get("confidence"), 0.0) > 0
        )
        points = _source_adjustment(evidence or {}, horizon) if available else 0.0
        adjustments.append({
            "source_id": source,
            "label": SOURCE_LABELS[source],
            "applicable": applicable,
            "available": available,
            "status": (evidence or {}).get("status") or "missing",
            "direction": (evidence or {}).get("direction") or "missing",
            "confidence": _bounded((evidence or {}).get("confidence")),
            "points": points,
            "reason": (evidence or {}).get("reason") or "目前沒有可對齊證據",
        })
    raw_adjustment = sum(row["points"] for row in adjustments if row["applicable"])
    cap = MAX_TOTAL_ADJUSTMENT[horizon]
    total_adjustment = round(max(-cap, min(cap, raw_adjustment)), 2)
    relevant = [row for row in adjustments if row["applicable"]]
    available = [row for row in relevant if row["available"]]
    coverage_pct = round(len(available) / len(relevant) * 100, 1) if relevant else 100.0
    confidences = [base_confidence] + [row["confidence"] for row in available]
    confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    score = _bounded(base_score + total_adjustment)
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "market": item.get("market"),
        "asset_type": item.get("asset_type"),
        "industry": item.get("industry"),
        "session_date": item.get("session_date"),
        "formal_overall_rank": item.get("formal_rank"),
        "formal_overall_score": _number(item.get("formal_score")),
        "horizon": horizon,
        "horizon_label": HORIZON_LABELS[horizon],
        "baseline_score": base_score,
        "shadow_score": score,
        "adjustment_points": total_adjustment,
        "confidence": confidence,
        "shadow_coverage_pct": coverage_pct,
        "risk_blocked": bool(item.get("risk_blocks")),
        "core_data_complete": not bool(item.get("core_data_missing")),
        "adjustments": adjustments,
        "formal_ranking_unchanged": True,
        "places_orders": False,
    }


def _rank_market(items: list[dict[str, Any]], market: str, horizon: str) -> list[dict[str, Any]]:
    rows = [_rank_item(item, horizon) for item in items if item.get("market") == market]
    baseline = sorted(
        rows,
        key=lambda row: (
            not row["core_data_complete"], row["risk_blocked"], -row["baseline_score"],
            int(row.get("formal_overall_rank") or 999999), str(row.get("symbol") or ""),
        ),
    )
    baseline_rank = {str(row.get("symbol")): index for index, row in enumerate(baseline, 1)}
    for row in rows:
        row["baseline_rank"] = baseline_rank[str(row.get("symbol"))]
    rows.sort(key=lambda row: (
        not row["core_data_complete"], row["risk_blocked"], -row["shadow_score"],
        row["baseline_rank"],
        str(row.get("symbol") or ""),
    ))
    for rank, row in enumerate(rows, 1):
        base_rank = row["baseline_rank"]
        row["shadow_rank"] = rank
        row["rank_change"] = base_rank - rank
    return rows


def _session_date(rows: list[dict[str, Any]]) -> str | None:
    dates = [str(row.get("session_date")) for row in rows if row.get("session_date")]
    if not dates:
        return None
    return Counter(dates).most_common(1)[0][0]


def _record_snapshot(
    reports_dir: Path,
    markets: dict[str, Any],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict[str, Any]:
    path = reports_dir / "comprehensive_shadow_history.json"
    history = _read(path)
    records = [row for row in history.get("records") or [] if isinstance(row, dict)]
    close_market = None if intraday else "TW" if period == "evening" else "US" if period == "morning" else None
    if close_market:
        market = markets.get(close_market) or {}
        session_date = market.get("session_date")
        key_exists = any(
            row.get("market") == close_market and row.get("session_date") == session_date
            for row in records
        )
        if session_date and not key_exists:
            ranking_count = int(market.get("ranking_count") or 0)
            complete_count = int(market.get("core_data_complete_count") or 0)
            quality_pct = round(complete_count / ranking_count * 100, 1) if ranking_count else 0.0
            records.append({
                "market": close_market,
                "session_date": session_date,
                "created_at": updated_at,
                "quality_pct": quality_pct,
                "eligible": quality_pct >= 95.0,
                "model_version": MODEL_VERSION,
                "top10": {
                    horizon: [
                        {"rank": row["shadow_rank"], "symbol": row["symbol"], "score": row["shadow_score"]}
                        for row in (market.get("horizons", {}).get(horizon) or [])[:10]
                    ]
                    for horizon in HORIZONS
                },
            })
    records = sorted(records, key=lambda row: (str(row.get("session_date") or ""), str(row.get("market") or "")))[-240:]
    valid_days = {
        market: len({
            row.get("session_date") for row in records
            if row.get("market") == market and row.get("eligible") and row.get("session_date")
        })
        for market in ("TW", "US")
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "updated_at": updated_at,
        "mode": "forward_only_frozen_snapshots",
        "valid_trading_days": valid_days,
        "target_trading_days": 60,
        "records": records,
        "policy": {
            "future_data_forbidden": True,
            "same_session_snapshot_immutable": True,
            "missing_sessions_not_counted": True,
            "formal_ranking_unchanged": True,
        },
    }
    _write(path, payload)
    return payload


def update_comprehensive_shadow_ranking(
    reports_dir: Path,
    decisions: list[dict[str, Any]],
    *,
    period: str,
    updated_at: str,
    intraday: bool,
) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    markets: dict[str, Any] = {}
    for market in ("TW", "US"):
        market_items = [item for item in decisions if item.get("market") == market]
        ranked = {horizon: _rank_market(market_items, market, horizon) for horizon in HORIZONS}
        markets[market] = {
            "session_date": _session_date(market_items),
            "ranking_count": len(market_items),
            "core_data_complete_count": sum(not item.get("core_data_missing") for item in market_items),
            "horizons": ranked,
        }
    history = _record_snapshot(
        reports_dir, markets, period=period, updated_at=updated_at, intraday=intraday
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "updated_at": updated_at,
        "period": period,
        "mode": "comprehensive_shadow_ranking_only",
        "status": "collecting",
        "policy": {
            "formal_v6_locked": True,
            "formal_weights_unchanged": True,
            "automatic_promotion": False,
            "automatic_orders": False,
            "markets_separate": True,
            "horizons_separate": True,
            "missing_data_never_imputed": True,
            "duplicate_formal_sources_adjustment": 0,
            "provisional_adjustment_caps": MAX_TOTAL_ADJUSTMENT,
        },
        "validation": {
            "initial_review_days": 20,
            "graduation_days": 60,
            "valid_trading_days": history["valid_trading_days"],
            "promotion_requires_manual_decision": True,
        },
        "source_policy": {
            "provisional_shadow_adjustments": SOURCE_LIMITS,
            "display_only_already_counted": [
                "formal_v6", "short_plan", "medium_45d", "long_6m",
                "tw_official_institution", "verified_news",
            ],
            "validation_only_not_stock_ranking": [
                "holding_simulation", "million_simulation", "validation_60d",
                "model_graduation",
            ],
        },
        "markets": markets,
    }
    ranking_files: dict[str, dict[str, str]] = {}
    for market in ("TW", "US"):
        ranking_files[market] = {}
        for horizon in HORIZONS:
            filename = f"comprehensive_shadow_{market}_{horizon}.json"
            _write(reports_dir / filename, {
                "schema_version": SCHEMA_VERSION,
                "model_version": MODEL_VERSION,
                "updated_at": updated_at,
                "market": market,
                "session_date": markets[market]["session_date"],
                "horizon": horizon,
                "horizon_label": HORIZON_LABELS[horizon],
                "rankings": markets[market]["horizons"][horizon],
            })
            ranking_files[market][horizon] = filename
    payload["ranking_files"] = ranking_files
    index_payload = {key: value for key, value in payload.items() if key != "markets"}
    index_payload["markets"] = {
        market: {
            "session_date": markets[market]["session_date"],
            "ranking_count": markets[market]["ranking_count"],
            "core_data_complete_count": markets[market]["core_data_complete_count"],
        }
        for market in ("TW", "US")
    }
    _write(reports_dir / "comprehensive_shadow_ranking.json", index_payload)
    return payload


def _load_decisions(reports_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    index = _read(reports_dir / "decision_hub.json")
    rows: list[dict[str, Any]] = []
    for filename in index.get("decision_files") or []:
        rows.extend(_read(reports_dir / str(filename)).get("decisions") or [])
    return index, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate comprehensive Central AI shadow rankings")
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    index, decisions = _load_decisions(reports_dir)
    payload = update_comprehensive_shadow_ranking(
        reports_dir,
        decisions,
        period=str(index.get("period") or "evening"),
        updated_at=str(index.get("updated_at") or ""),
        intraday=index.get("run_mode") == "intraday_refresh",
    )
    print(
        "Comprehensive shadow: "
        + ", ".join(
            f"{market} {payload['markets'][market]['ranking_count']}"
            for market in ("TW", "US")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
