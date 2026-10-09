"""Read-only, fail-closed event registry for shadow outputs; never a scoring input.

Each record is an immutable observed revision, not a forecast. Callers retain the
raw revision history; this module neither fetches nor persists external data.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

VERSION = "POINT-IN-TIME-EVENTS-SHADOW-V1"
EVENT_TYPES = {"earnings", "guidance", "filing", "news"}
OFFICIAL_SOURCES = {"SEC EDGAR companyfacts", "MOPS via TWSE OpenAPI", "MOPS via TPEX OpenAPI"}


def _time(value: Any) -> datetime | None:
    """Require a complete timezone-aware timestamp. A calendar date is not time."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def event_snapshot(records: list[dict[str, Any]], *, cutoff: datetime | str) -> dict[str, Any]:
    """Filter availability BEFORE resolving revisions, preventing future leakage.

    first_seen is when THIS revision was actually observed by the collector. It
    must never be backdated to publication. Duplicate keys have identical source,
    symbol, event_id and revision; conflicting payloads fail closed at this cutoff.
    """
    at = _time(cutoff)
    if at is None:
        raise ValueError("cutoff must be a timezone-aware timestamp")
    excluded: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            excluded.append({"index": index, "reason": "invalid_record"})
            continue
        base = {"index": index, "event_id": record.get("event_id"), "source": record.get("source")}
        reason = None
        if any(not isinstance(record.get(key), str) or not record[key].strip()
               for key in ("event_id", "symbol", "source", "revision")):
            reason = "missing_identity_or_revision"
        elif record.get("event_type") not in EVENT_TYPES:
            reason = "unsupported_event_type"
        published, seen = _time(record.get("published_at")), _time(record.get("first_seen"))
        revised = _time(record.get("revision_published_at")) if record.get("revision_published_at") is not None else published
        if not reason and published is None:
            reason = "missing_or_imprecise_publication_time"
        if not reason and seen is None:
            reason = "missing_or_imprecise_first_seen"
        if not reason and revised is None:
            reason = "missing_or_imprecise_revision_time"
        if not reason and revised < published:
            reason = "revision_precedes_publication"
        if not reason and seen < revised:
            reason = "first_seen_precedes_publication"
        available = max(published, seen, revised) if not reason else None
        if not reason and available > at:
            reason = "not_available_at_cutoff"
        if reason:
            excluded.append({**base, "reason": reason})
            continue
        normalized = dict(record)
        normalized.update({"published_at": _iso(published), "first_seen": _iso(seen),
                           "revision_published_at": _iso(revised), "available_at": _iso(available),
                           "source_trust": "official" if record["source"] in OFFICIAL_SOURCES else "unverified"})
        key = tuple(record[name] for name in ("source", "symbol", "event_id", "revision"))
        candidates.setdefault(key, []).append(normalized)
    versions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    conflicted_identities: set[tuple[str, str, str]] = set()
    duplicate_count = 0
    for key, copies in candidates.items():
        # Observational timestamps may differ across repeated collection. Content
        # and publication metadata must remain immutable under a revision ID.
        fingerprints = {_hash({k: v for k, v in item.items() if k not in {"first_seen", "available_at"}}) for item in copies}
        if len(fingerprints) > 1:
            conflicted_identities.add(key[:3])
            excluded.append({"event_id": key[2], "source": key[0], "revision": key[3], "reason": "conflicting_revision"})
            continue
        duplicate_count += len(copies) - 1
        versions.setdefault(key[:3], []).append(min(copies, key=lambda item: _time(item["available_at"])))
    events = []
    history = []
    for key, revisions in sorted(versions.items()):
        ordered = sorted(revisions, key=lambda item: (_time(item["revision_published_at"]), _time(item["available_at"]), item["revision"]))
        # Two different revisions at the same publication/observation time are
        # ambiguous, rather than an invitation to guess revision ID ordering.
        newest = ordered[-1]
        tied = [item for item in ordered if (item["revision_published_at"], item["available_at"]) ==
                (newest["revision_published_at"], newest["available_at"])]
        history.extend(ordered)
        # Never revive an older revision after a known conflicting amendment.
        # No reconciliation contract exists, so any observed revision conflict
        # quarantines this identity while preserving unambiguous history.
        if key in conflicted_identities:
            excluded.append({"event_id": key[2], "source": key[0], "reason": "identity_has_conflicting_revision"})
        elif len(tied) > 1:
            excluded.append({"event_id": key[2], "source": key[0], "reason": "ambiguous_latest_revision"})
        else:
            events.append(newest)
    return {"version": VERSION, "shadow_only": True, "cutoff": _iso(at), "events": events,
            "available_revision_history": history, "excluded": excluded,
            "counts": {"input": len(records), "eligible_events": len(events), "duplicates": duplicate_count,
                       "exclusion_reasons": dict(Counter(item["reason"] for item in excluded))},
            "coverage": "adapter_contract_only; production_event_coverage_not_validated",
            "affects_scores": False}


def sec_companyfacts_events(payload: dict[str, Any], *, symbol: str, first_seen: str) -> list[dict[str, Any]]:
    """Preserve raw SEC accession facts without reusing collapsed latest values.

    Company Facts has date-only `filed`; this adapter intentionally cannot make
    those events eligible without an upstream precise publication timestamp.
    Period end is retained separately and is NEVER used as publication time.
    """
    by_accession: dict[str, list[dict[str, Any]]] = {}
    for namespace, concepts in (payload.get("facts") or {}).items():
        for concept, fact in concepts.items():
            for unit, rows in (fact.get("units") or {}).items():
                for row in rows:
                    if isinstance(row, dict) and row.get("accn"):
                        by_accession.setdefault(str(row["accn"]), []).append({"namespace": namespace, "concept": concept, "unit": unit, **row})
    output = []
    for accession, facts in sorted(by_accession.items()):
        filed_dates = sorted({str(item.get("filed")) for item in facts if item.get("filed")})
        output.append({"event_id": accession, "symbol": symbol, "source": "SEC EDGAR companyfacts",
                       "event_type": "filing", "revision": accession, "first_seen": first_seen,
                       "published_at": filed_dates[0] if len(filed_dates) == 1 else None,
                       "period_ends": sorted({str(item["end"]) for item in facts if item.get("end")}),
                       "payload": {"facts": facts}, "publication_precision": "date"})
    return output


def build_event_snapshot(row: dict[str, Any], *, cutoff: datetime | str) -> dict[str, Any]:
    """Pipeline adapter over existing row news plus explicit immutable event rows.

    Existing date-only news are audited as excluded; no title inference labels
    earnings/guidance. Upstream adapters must supply explicit event classification.
    """
    records = list(row.get("point_in_time_events") or [])
    for article in row.get("news_articles") or []:
        if not isinstance(article, dict):
            records.append(article)
            continue
        source = str(article.get("publisher") or "unknown")
        records.append({"event_id": str(article.get("url") or _hash({"title": article.get("title"), "source": source})),
                        "symbol": str(row.get("symbol") or ""), "source": source,
                        "event_type": "news", "revision": str(article.get("revision") or _hash(article)),
                        "published_at": article.get("published_at"),
                        "first_seen": article.get("first_seen") or row.get("news_scanned_at"),
                        "payload": {"title": article.get("title"), "url": article.get("url")}})
    result = event_snapshot(records, cutoff=cutoff)
    result["source_limitations"] = [
        "Existing news and TW announcements expose date-only publication and are excluded.",
        "SEC latest fundamentals collapse revisions; period ends are not publication timestamps.",
        "No forecast calendar, consensus estimates, or inferred earnings surprises are supplied.",
    ]
    return result
