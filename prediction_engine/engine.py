"""Independent, point-in-time prediction engine.

The existing application remains the collector and presentation shell.  This
module reads completed reports, writes its own private SQLite database and
publishes only a compact, read-only result contract for the Central AI layer.
It never edits formal V6 rows or the existing five/60-session ledgers.
"""

from __future__ import annotations

import copy
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .features import extract_features, group_name, session_date
from .models import (
    HORIZONS,
    MODEL_VERSION,
    challenger_qualification,
    fit_challenger,
    forecast,
    learned_forecast,
)
from .storage import PredictionStore


SCHEMA_VERSION = 1
REPORT_VERSION = "WUDE-PREDICTION-CONTRACT-V1"
GROUPS = ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF")
PORTFOLIO_HORIZONS = ("UP_5D", "UP_45D", "UP_126D")
PORTFOLIO_CAPITAL = {"TW": (1_000_000.0, "TWD"), "US": (1_000_000.0, "USD")}
MIN_PORTFOLIO_QUALITY = 75.0
MAX_PUBLIC_INDEX_BYTES = 300_000
MAX_PUBLIC_CHUNK_BYTES = 300_000
# Backwards-compatible name used by older tests; it now protects only the
# lightweight index, not the combined model output.
MAX_PUBLIC_REPORT_BYTES = MAX_PUBLIC_INDEX_BYTES


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any], *, compact: bool = True) -> int:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return len(encoded)


def _dominant_session(rows: list[dict[str, Any]]) -> str:
    dates = [session_date(row) for row in rows if session_date(row)]
    if not dates:
        return ""
    counts = Counter(dates)
    return max(counts, key=lambda value: (counts[value], value))


def _market_ready(market: str, period: str | None, intraday: bool) -> bool:
    if intraday:
        return False
    if period is None or str(period).lower() in {"test", "all", "force"}:
        return True
    expected = "evening" if market == "TW" else "morning"
    return str(period).lower() == expected


def _capital_flow_index(payload: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else {}
    for market, days in markets.items():
        for day in days if isinstance(days, list) else []:
            if not isinstance(day, dict) or not day.get("closed"):
                continue
            date = str(day.get("session_date") or "")[:10]
            for bucket in ("top_inflows", "top_outflows", "amount_top_inflows", "amount_top_outflows"):
                for row in day.get(bucket) or []:
                    if not isinstance(row, dict) or not row.get("symbol"):
                        continue
                    key = (str(market).upper(), date, str(row["symbol"]).upper())
                    prior = result.get(key)
                    if prior is None or float(row.get("confidence") or 0) > float(prior.get("confidence") or 0):
                        result[key] = row
    return result


def _shadow_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Collapse ten legacy votes into five independent evidence families."""
    votes = row.get("next_session_model_votes")
    if not isinstance(votes, dict):
        return {"evidence": []}
    families = {
        "趨勢結構": ("price_trend", "relative_strength"),
        "量能資金": ("volume_confirmation", "market_flow"),
        "環境風險": ("macro_risk", "exhaustion_guard"),
        "進場反轉": ("mean_reversion", "entry_timing"),
        "平衡動能": ("balanced_next", "momentum_confirmed"),
    }
    evidence = []
    for family, members in families.items():
        member_rows = [votes[name] for name in members if isinstance(votes.get(name), dict)]
        if not member_rows:
            continue
        signed = []
        confidence = []
        for item in member_rows:
            score = float(item.get("score") or 50.0)
            direction = str(item.get("direction") or "").upper()
            signed.append(50.0 - abs(score - 50.0) if direction == "DOWN" else score)
            confidence.append(float(item.get("confidence") or 50.0))
        average = sum(signed) / len(signed)
        evidence.append({
            "source_id": family,
            "direction": "support" if average >= 50 else "oppose",
            "strength": 50.0 + abs(average - 50.0),
            "confidence": sum(confidence) / len(confidence),
            "time_aligned": True,
            "reason": f"{family}影子家族（{len(member_rows)}模型，僅計一次）",
        })
    return {"evidence": evidence}


def _trade_blocked(row: dict[str, Any]) -> bool:
    if row.get("trade_guard_blocked"):
        return True
    contract = row.get("market_contract_valid")
    return contract is False


def _make_predictions(
    rows: list[dict[str, Any]],
    *,
    market: str,
    date: str,
    updated_at: str,
    flow_by_symbol: dict[tuple[str, str, str], dict[str, Any]],
    selected_models: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    predictions = []
    for row in rows:
        source_price = float(row.get("official_close_price") or row.get("price") or 0)
        if source_price <= 0:
            continue
        group = group_name(row)
        flow = flow_by_symbol.get((market, date, str(row["symbol"]).upper()))
        extracted = extract_features(
            row,
            capital_flow=flow,
            shadow=_shadow_evidence(row),
        )
        for code in HORIZONS:
            selection = (selected_models or {}).get((group, code)) or {
                "model_version": MODEL_VERSION, "weights": None,
            }
            arguments = {
                "chase_risk_points": float(extracted["evidence"]["chase_risk_points"]),
                "data_quality_pct": float(extracted["data_quality_pct"]),
                "trade_blocked": _trade_blocked(row),
            }
            if selection.get("weights"):
                result = learned_forecast(
                    extracted["features"], code,
                    weights=selection["weights"], **arguments,
                )
            else:
                result = forecast(extracted["features"], code, **arguments)
            evidence = dict(extracted["evidence"])
            evidence["active_model_version"] = selection["model_version"]
            evidence["model_selected_before_session"] = True
            predictions.append({
                "market": market,
                "asset_group": group,
                "session_date": date,
                "symbol": str(row["symbol"]),
                "name": str(row.get("name") or row["symbol"]),
                "horizon_code": code,
                "model_version": selection["model_version"],
                "source_price": source_price,
                "data_quality_pct": extracted["data_quality_pct"],
                "features": extracted["features"],
                "evidence": evidence,
                "trade_blocked": _trade_blocked(row),
                "created_at": updated_at,
                **result,
            })
    return predictions


def _bootstrap_archived_point_in_time_reports(
    reports_dir: Path,
    store: PredictionStore,
) -> dict[str, Any]:
    """Seed learning only from immutable reports that existed at prediction time."""
    if store.session_count() or not (reports_dir / "archive").is_dir():
        return {"attempted": False, "sessions": 0, "predictions": 0, "matured": 0}
    checkpoints: dict[tuple[str, str], tuple[str, list[dict[str, Any]]]] = {}
    for path in sorted((reports_dir / "archive").glob("*.json")):
        payload = _read_json(path)
        rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not rows and isinstance(payload.get("watchlist"), list):
            # Archived briefing files intentionally stored the then-current
            # watchlist rather than today's full universe.  They are still
            # valid point-in-time training rows and are never expanded using
            # present-day information.
            rows = payload["watchlist"]
        period = str(payload.get("period") or "").lower()
        updated = str(payload.get("updated_at") or path.stem)
        for market, expected_period in (("TW", "evening"), ("US", "morning")):
            if period != expected_period:
                continue
            market_rows = [row for row in rows if isinstance(row, dict) and str(row.get("market") or "").upper() == market]
            date = _dominant_session(market_rows)
            if date and market_rows:
                checkpoints[(market, date)] = (updated, market_rows)
    inserted = 0
    for (market, date), (updated, rows) in sorted(checkpoints.items(), key=lambda item: (item[0][1], item[0][0])):
        store.record_session(market, date, updated)
        store.record_prices(rows, date)
        predictions = _make_predictions(
            rows,
            market=market,
            date=date,
            updated_at=updated,
            flow_by_symbol={},
        )
        inserted += store.insert_predictions(predictions)
        store.record_source_usage(
            date,
            "immutable_archive_bootstrap",
            read_count=len(rows),
            network_requests=0,
            cache_hits=len(rows),
        )
    matured = sum(store.settle_matured(market) for market in ("TW", "US"))
    return {
        "attempted": True,
        "sessions": len(checkpoints),
        "predictions": inserted,
        "matured": matured,
        "source": "reports/archive point-in-time completed checkpoints",
        "historical_lab_proxy_used_for_training": False,
    }


def _ranking_score(prediction: dict[str, Any]) -> float:
    if prediction["target_side"] == "DOWN":
        magnitude = max(0.0, -float(prediction["expected_return_pct"]))
        return float(prediction["probability_pct"]) * 0.72 + min(30.0, magnitude) * 0.93
    positive_return = max(-10.0, min(40.0, float(prediction["expected_return_pct"])))
    return (
        float(prediction["probability_pct"]) * 0.43
        + float(prediction["buyability_score"]) * 0.37
        + (positive_return + 10.0) * 0.40
        - float(prediction["downside_risk_pct"]) * 0.08
    )


def _ensure_latest_paper_portfolios(
    store: PredictionStore,
    predictions: list[dict[str, Any]],
    *,
    created_at: str,
) -> int:
    """Create the three requested accounts from each market's latest checkpoint."""
    created = 0
    for market in ("TW", "US"):
        capital, currency = PORTFOLIO_CAPITAL[market]
        for code in PORTFOLIO_HORIZONS:
            eligible = [
                item for item in predictions
                if item["market"] == market
                and item["horizon_code"] == code
                and item["target_side"] == "UP"
                and float(item["buyability_score"]) > 0
                and float(item["data_quality_pct"]) >= MIN_PORTFOLIO_QUALITY
                and float(item["probability_pct"]) >= 55.0
                and float(item["expected_return_pct"]) > 0
            ]
            eligible.sort(key=lambda item: (-_ranking_score(item), item["symbol"]))
            picks = eligible[:5]
            if not picks:
                continue
            session = str(picks[0]["session_date"])
            picks = [item for item in picks if str(item["session_date"]) == session]
            weight = round(1.0 / len(picks), 10)
            positions = [
                {
                    "symbol": item["symbol"], "rank": rank,
                    "weight": weight, "entry_price": item["source_price"],
                }
                for rank, item in enumerate(picks, 1)
            ]
            if store.create_portfolio(
                {
                    "market": market, "horizon_code": code, "session_date": session,
                    "capital": capital, "currency": currency,
                    "horizon_sessions": HORIZONS[code]["sessions"], "created_at": created_at,
                },
                positions,
            ):
                created += 1
    return created


def _compact_prediction(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") or {}
    return {
        "symbol": row["symbol"],
        "name": row["name"],
        "market": row["market"],
        "asset_group": row["asset_group"],
        "session_date": row["session_date"],
        "horizon_code": row["horizon_code"],
        "model_version": row["model_version"],
        "target_side": row["target_side"],
        "probability_pct": row["probability_pct"],
        "expected_return_pct": row["expected_return_pct"],
        "buyability_score": row["buyability_score"],
        "downside_risk_pct": row["downside_risk_pct"],
        "data_quality_pct": row["data_quality_pct"],
        "chase_risk_points": evidence.get("chase_risk_points", 0),
        "trade_blocked": bool(row.get("trade_blocked")),
        "ranking_score": round(_ranking_score(row), 2),
    }


def _build_public_contract(
    predictions: list[dict[str, Any]],
    *,
    updated_at: str,
    period: str,
    market_status: dict[str, dict[str, Any]],
    store: PredictionStore,
    inserted: int,
    matured: int,
    portfolios_settled: int,
    archive_bootstrap: dict[str, Any],
    maintenance: dict[str, Any],
    input_symbol_count: int,
) -> dict[str, Any]:
    compact = [_compact_prediction(row) for row in predictions]
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        group: {code: [] for code in HORIZONS} for group in GROUPS
    }
    symbols: dict[str, dict[str, Any]] = {}
    for row in compact:
        grouped.setdefault(row["asset_group"], {}).setdefault(row["horizon_code"], []).append(row)
        key = f'{row["market"]}:{row["symbol"]}'
        symbol = symbols.setdefault(key, {
            "symbol": row["symbol"], "name": row["name"], "market": row["market"],
            "asset_group": row["asset_group"], "session_date": row["session_date"], "horizons": {},
        })
        symbol["horizons"][row["horizon_code"]] = {
            key: row[key] for key in (
                "target_side", "probability_pct", "expected_return_pct", "buyability_score",
                "downside_risk_pct", "data_quality_pct", "chase_risk_points", "trade_blocked",
                "ranking_score", "model_version",
            )
        }
    rankings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for group, horizons in grouped.items():
        rankings[group] = {}
        for code, values in horizons.items():
            ordered = sorted(values, key=lambda item: (-item["ranking_score"], item["symbol"]))
            rankings[group][code] = [dict(item, rank=index + 1) for index, item in enumerate(ordered[:10])]

    learning = {}
    for group in GROUPS:
        market = group.split("_", 1)[0]
        learning[group] = {}
        for code in HORIZONS:
            challenger = store.latest_challenger(market, group, code)
            control = store.control_state(market, group, code)
            learning[group][code] = (
                {
                    "status": challenger["status"],
                    "model_version": challenger["model_version"],
                    "trained_through": challenger["trained_through"],
                    "sample_count": challenger["sample_count"],
                    "session_count": challenger["session_count"],
                    "metrics": challenger["metrics"],
                    "qualified_for_promotion": challenger_qualification(challenger)[0],
                    "control": control,
                    "automatic_promotion": True,
                }
                if challenger else {
  