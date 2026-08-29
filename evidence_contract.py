"""Canonical evidence contract shared by the Central Decision Hub adapters."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


SCHEMA_VERSION = 1
VALID_DIRECTIONS = {"support", "oppose", "neutral", "missing"}
VALID_HORIZONS = {"all", "short", "medium", "long", "market", "risk", "portfolio"}


def _bounded(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return round(max(0.0, min(100.0, number)), 1)


def make_evidence(
    *, source_id: str, source_label: str, horizon: str, direction: str,
    strength: Any, confidence: Any, as_of: str | None, status: str,
    reason: str, affects_decision: bool, symbol: str | None = None,
    market: str | None = None, provenance: str | None = None,
) -> dict[str, Any]:
    if direction not in VALID_DIRECTIONS:
        direction = "missing"
    if horizon not in VALID_HORIZONS:
        horizon = "all"
    identity = "|".join((symbol or "MARKET", source_id, horizon, as_of or ""))
    return {
        "evidence_schema_version": SCHEMA_VERSION,
        "evidence_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "symbol": symbol,
        "market": market,
        "source_id": source_id,
        "source_label": source_label,
        "horizon": horizon,
        "direction": direction,
        "strength": _bounded(strength),
        "confidence": _bounded(confidence),
        "as_of": as_of or None,
        "status": str(status or "missing"),
        "reason": str(reason or "沒有提供原因"),
        "affects_decision": bool(affects_decision),
        "provenance": provenance or source_id,
    }


def validate_evidence(item: dict[str, Any]) -> list[str]:
    errors = []
    required = (
        "evidence_schema_version", "evidence_id", "source_id", "source_label",
        "horizon", "direction", "strength", "confidence", "status", "reason",
        "affects_decision", "provenance",
    )
    for key in required:
        if key not in item:
            errors.append(f"missing:{key}")
    if item.get("direction") not in VALID_DIRECTIONS:
        errors.append("invalid:direction")
    if item.get("horizon") not in VALID_HORIZONS:
        errors.append("invalid:horizon")
    return errors


def build_unified_evidence_report(
    decisions: list[dict[str, Any]], *, updated_at: str
) -> dict[str, Any]:
    rows = []
    invalid = []
    for decision in decisions:
        for evidence in decision.get("evidence", []):
            item = dict(evidence)
            errors = validate_evidence(item)
            if errors:
                invalid.append({"evidence_id": item.get("evidence_id"), "errors": errors})
            rows.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "status": "ready" if not invalid else "invalid",
        "evidence_count": len(rows),
        "invalid_count": len(invalid),
        "invalid": invalid,
        "contract": {
            "directions": sorted(VALID_DIRECTIONS),
            "horizons": sorted(VALID_HORIZONS),
            "missing_never_imputed": True,
            "provenance_required": True,
        },
        "evidence": rows,
        "integrity_sha256": hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
    }
