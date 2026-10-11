"""Resolve an explicitly attested task supplement without changing canonical data.

Task provenance is an explicit local input, not a repository default or a task
edit. Both input identities remain verifiable in the resulting report union.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from watchlist_manifest import (
    MANIFEST_PATH, _json_bytes, _key, build_manifest, check_report_coverage,
    identity, render_manifest,
)


def manifest_sha256(value: Any) -> str:
    """SHA-256 of sorted-key compact UTF-8 JSON, without a trailing newline."""
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _validate_entries(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries must be a nonempty list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("security"), dict):
            raise ValueError("each entry requires security metadata")
        key = _key(entry["security"])
        if entry.get("key") != key or key in seen:
            raise ValueError("entry identity mismatch or duplicate")
        seen.add(key)
        role = entry.get("coverage_role")
        if role not in {"per_symbol", "reference_only"}:
            raise ValueError("invalid coverage_role")
        if (entry["security"].get("ranking_mode") == "reference_only") != (role == "reference_only"):
            raise ValueError("reference role conflicts with original metadata")


def validate_task_supplement(supplement: dict[str, Any]) -> None:
    if (not isinstance(supplement, dict) or type(supplement.get("schema_version")) is not int
            or supplement["schema_version"] != 1 or supplement.get("scope") != "task_report_supplement"):
        raise ValueError("expected task_report_supplement schema version 1")
    _validate_entries(supplement)
    source = supplement.get("source", {})
    if not isinstance(source, dict) or source.get("verification") != "stored_prompt_readback":
        raise ValueError("supplement requires stored prompt readback attestation")
    try:
        when = datetime.fromisoformat(source.get("readback_at", "").replace("Z", "+00:00"))
        if when.utcoffset() is None:
            raise ValueError("readback timestamp must include timezone")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid supplement readback_at") from exc
    tasks = source.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("supplement requires verified task provenance")
    task_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str) or not task["task_id"].strip():
            raise ValueError("task_id is required")
        if task["task_id"] in task_ids or not _sha256(task.get("prompt_sha256")):
            raise ValueError("duplicate task or invalid prompt hash")
        task_ids.add(task["task_id"])
    if supplement.get("entries_sha256") != manifest_sha256(supplement["entries"]):
        raise ValueError("supplement entries hash mismatch")
    if type(supplement.get("entry_count")) is not int or supplement["entry_count"] != len(supplement["entries"]):
        raise ValueError("supplement entry count mismatch")


def build_report_universe(canonical: dict[str, Any], supplement: dict[str, Any]) -> dict[str, Any]:
    """Stable market/symbol union; canonical metadata wins on overlap."""
    if (not isinstance(canonical, dict) or type(canonical.get("schema_version")) is not int
            or canonical["schema_version"] != 1 or canonical.get("scope") != "canonical_watchlist_only"):
        raise ValueError("expected canonical_watchlist_only schema version 1")
    _validate_entries(canonical)
    if canonical.get("universe_sha256") != manifest_sha256(canonical["entries"]):
        raise ValueError("canonical entries hash mismatch")
    source = canonical.get("source")
    if not isinstance(source, dict) or not _sha256(source.get("sha256")):
        raise ValueError("canonical source hash missing")
    groups = canonical.get("source_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("canonical source groups missing")
    group_names: set[str] = set()
    source_rows = 0
    for group in groups:
        if (not isinstance(group, dict) or not isinstance(group.get("name"), str)
                or not group["name"] or group["name"] in group_names
                or type(group.get("source_row_count")) is not int or group["source_row_count"] < 0):
            raise ValueError("invalid canonical source group")
        group_names.add(group["name"])
        source_rows += group["source_row_count"]
    for entry in canonical["entries"]:
        if (not isinstance(entry.get("source_groups"), list) or not entry["source_groups"]
                or any(name not in group_names for name in entry["source_groups"])):
            raise ValueError("entry source group mismatch")
    ref_count = sum(entry["coverage_role"] == "reference_only" for entry in canonical["entries"])
    expected_counts = {
        "source_rows": source_rows,
        "unique_entries": len(canonical["entries"]),
        "duplicate_rows": source_rows - len(canonical["entries"]),
        "per_symbol_required": len(canonical["entries"]) - ref_count,
        "reference_only": ref_count,
        "by_market": dict(sorted(Counter(identity(entry["security"])[0] for entry in canonical["entries"]).items())),
    }
    if source_rows < len(canonical["entries"]) or _json_bytes(canonical.get("counts")) != _json_bytes(expected_counts):
        raise ValueError("canonical counts mismatch")
    validate_task_supplement(supplement)
    entries = deepcopy(canonical["entries"])
    by_key = {entry["key"]: entry for entry in entries}
    for entry in entries:
        entry["origins"] = ["canonical_watchlist"]
    overlap = 0
    for addition in supplement["entries"]:
        key = addition["key"]
        if key in by_key:
            overlap += 1
            by_key[key]["origins"].append("task_supplement")
            continue
        entry = deepcopy(addition)
        entry["source_groups"] = []
        entry["origins"] = ["task_supplement"]
        entries.append(entry)
        by_key[key] = entry
    inputs = {
        "canonical_manifest_sha256": manifest_sha256(canonical),
        "task_supplement_sha256": manifest_sha256(supplement),
    }
    references = sum(entry["coverage_role"] == "reference_only" for entry in entries)
    return {
        "schema_version": 1,
        "scope": "report_union",
        "source": deepcopy(canonical["source"]),
        "supplement_source": deepcopy(supplement["source"]),
        "inputs": inputs,
        "inputs_sha256": manifest_sha256(inputs),
        "identity_fields": ["market", "symbol"],
        "counts": {
            "canonical_entries": len(canonical["entries"]),
            "supplement_entries": len(supplement["entries"]),
            "overlapping_entries": overlap,
            "unique_entries": len(entries),
            "per_symbol_required": len(entries) - references,
            "reference_only": references,
            "by_market": dict(sorted(Counter(identity(entry["security"])[0] for entry in entries).items())),
        },
        "universe_sha256": manifest_sha256(entries),
        "entries": entries,
    }


def validate_report_universe(universe: dict[str, Any], canonical: dict[str, Any], supplement: dict[str, Any]) -> None:
    """Fail if any union record, provenance, hash, count, or origin was altered."""
    if _json_bytes(universe) != _json_bytes(build_report_universe(canonical, supplement)):
        raise ValueError("report universe does not exactly match its attested inputs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()
    if args.coverage and not args.check:
        parser.error("--coverage requires --check")
    try:
        canonical = build_manifest()
        if MANIFEST_PATH.read_text(encoding="utf-8") != render_manifest(canonical):
            raise ValueError("canonical artifact is stale; regenerate it first")
        supplement = json.loads(args.supplement.read_text(encoding="utf-8"))
        union = build_report_universe(canonical, supplement)
        if args.write:
            args.output.write_text(render_manifest(union), encoding="utf-8")
        else:
            actual = json.loads(args.output.read_text(encoding="utf-8"))
            validate_report_universe(actual, canonical, supplement)
        if args.coverage:
            result = check_report_coverage(json.loads(args.coverage.read_text(encoding="utf-8")), union)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["complete"] else 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"OK: {union['counts']['unique_entries']} report-union entries; {union['universe_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
