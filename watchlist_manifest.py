"""Offline canonical-pool export and identity-only report completeness gate.

No provider requests, prices, scores, ranking decisions, or task configuration.
The imported watchlist is the sole source; report-specific additions stay outside
this canonical artifact. Run from a trusted checkout of this repository.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import watchlist


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "watchlist_manifest.json"
SCHEMA_VERSION = 1


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def identity(row: dict[str, Any]) -> tuple[str, str]:
    """Normalize case/space only; retain exchange suffixes and market identity."""
    market = row.get("market")
    symbol = row.get("symbol")
    if not isinstance(market, str) or not isinstance(symbol, str):
        raise ValueError("market and symbol must be strings")
    key = market.strip().upper(), symbol.strip().upper()
    if key[0] not in {"TW", "US"} or not key[1] or ":" in key[1]:
        raise ValueError("invalid market/symbol identity")
    return key


def _key(row: dict[str, Any]) -> str:
    return ":".join(identity(row))


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("unsupported source form")


def _source_groups(source: bytes) -> list[tuple[str, list[str]]]:
    """Discover the literal base and every supported WATCHLIST.extend in order.

    Group labels/counts are derived from source, never maintained as a second
    fixed list. Unknown forms fail closed rather than silently omitting a group.
    Final identities are cross-checked against the actually loaded watchlist.
    """
    tree = ast.parse(source)
    assignments: dict[str, ast.expr] = {}
    groups: list[tuple[str, list[str]]] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments[node.target.id] = node.value
            if node.target.id == "WATCHLIST":
                groups.append(("WATCHLIST", [_key(row) for row in ast.literal_eval(node.value)]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
                    if target.id == "WATCHLIST":
                        groups.append(("WATCHLIST", [_key(row) for row in ast.literal_eval(node.value)]))
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        func = call.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "WATCHLIST" and func.attr == "extend"):
            continue
        try:
            _require(len(call.args) == 1 and not call.keywords)
            generator = call.args[0]
            _require(isinstance(generator, ast.GeneratorExp) and len(generator.generators) == 1)
            loop = generator.generators[0]
            _require(not loop.ifs and not loop.is_async)
            _require(isinstance(loop.iter, ast.Call) and isinstance(loop.iter.func, ast.Name))
            _require(loop.iter.func.id == "enumerate" and isinstance(loop.iter.args[0], ast.Name))
            group_name = loop.iter.args[0].id
            _require(isinstance(loop.target, ast.Tuple) and len(loop.target.elts) == 2)
            fields = loop.target.elts[1]
            _require(isinstance(fields, ast.Tuple) and all(isinstance(f, ast.Name) for f in fields.elts))
            names = [f.id for f in fields.elts]
            rows = ast.literal_eval(assignments[group_name])
            keys = [_key(dict(zip(names, row, strict=True))) for row in rows]
        except (AssertionError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("unsupported WATCHLIST.extend; update the manifest generator") from exc
        groups.append((group_name, keys))
    if not groups or groups[0][0] != "WATCHLIST":
        raise ValueError("literal WATCHLIST base not found")
    return groups


def build_manifest() -> dict[str, Any]:
    source = Path(watchlist.__file__).read_bytes()
    rows = watchlist.load_watchlist()
    groups = _source_groups(source)
    source_keys = [key for _, keys in groups for key in keys]
    if source_keys != [_key(row) for row in rows]:
        raise ValueError("source groups do not exactly cover the loaded watchlist in order")

    entries: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for row, (group_name, _) in zip(
        rows, ((name, key) for name, keys in groups for key in keys), strict=True,
    ):
        key = _key(row)
        if key in by_key:
            # Exact duplicates are counted and collapsed. Conflicting metadata
            # requires an explicit source correction, never a silent overwrite.
            if by_key[key]["security"] != row:
                raise ValueError(f"conflicting duplicate canonical identity: {key}")
            if group_name not in by_key[key]["source_groups"]:
                by_key[key]["source_groups"].append(group_name)
            continue
        entry = {
            "key": key,
            "source_groups": [group_name],
            "coverage_role": "reference_only" if row.get("ranking_mode") == "reference_only" else "per_symbol",
            "security": dict(row),
        }
        entries.append(entry)
        by_key[key] = entry

    reference_count = sum(entry["coverage_role"] == "reference_only" for entry in entries)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "canonical_watchlist_only",
        "source": {
            "repository": "a0953883367/wude-ai-stock-v6",
            "path": "watchlist.py",
            "git_blob_sha1": hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest(),
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        "identity_fields": ["market", "symbol"],
        "source_groups": [{"name": name, "source_row_count": len(keys)} for name, keys in groups],
        "counts": {
            "source_rows": len(rows),
            "unique_entries": len(entries),
            "duplicate_rows": len(rows) - len(entries),
            "per_symbol_required": len(entries) - reference_count,
            "reference_only": reference_count,
            "by_market": dict(sorted(Counter(identity(e["security"])[0] for e in entries).items())),
        },
        "universe_sha256": hashlib.sha256(_json_bytes(entries)).hexdigest(),
        "entries": entries,
    }


def render_manifest(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def check_report_coverage(report: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Check structured coverage, not analysis quality or investment eligibility.

    Each required symbol needs one row with status 'analyzed' or 'unavailable';
    reference entries instead need status 'reference_only'. An unavailable row
    must explain the missing data. All reference entries remain in the census.
    """
    expected = {entry["key"]: entry for entry in manifest["entries"]}
    errors: list[str] = []
    if not isinstance(report, dict):
        raise ValueError("coverage report must be an object")
    for field, value in (
        ("universe_sha256", manifest["universe_sha256"]),
        ("source_sha256", manifest["source"]["sha256"]),
    ):
        if report.get(field) != value:
            errors.append(f"{field} missing or stale")
    if manifest.get("scope") == "report_union" and report.get("inputs_sha256") != manifest.get("inputs_sha256"):
        errors.append("inputs_sha256 missing or stale")
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise ValueError("coverage report rows must be a list")
    seen: Counter[str] = Counter()
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            key = _key(row)
        except ValueError as exc:
            errors.append(f"row {index}: {exc}")
            continue
        seen[key] += 1
        if key not in expected:
            continue
        reference = expected[key]["coverage_role"] == "reference_only"
        valid_statuses = {"reference_only"} if reference else {"analyzed", "unavailable"}
        if row.get("status") not in valid_statuses:
            errors.append(f"{key}: invalid coverage status")
        if row.get("status") == "unavailable" and (
            not isinstance(row.get("reason"), str) or not row["reason"].strip()
        ):
            errors.append(f"{key}: unavailable row needs a reason")
    missing = [key for key in expected if key not in seen]
    unexpected = [key for key in seen if key not in expected]
    duplicates = [key for key, count in seen.items() if count > 1]
    return {
        "complete": not (missing or unexpected or duplicates or errors),
        "expected_count": len(expected),
        "covered_count": len(expected.keys() & seen.keys()),
        "missing": missing,
        "unexpected": unexpected,
        "duplicates": duplicates,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the canonical JSON artifact")
    mode.add_argument("--check", action="store_true", help="fail if the canonical JSON is missing or stale")
    parser.add_argument("--coverage", type=Path, help="also check a structured canonical coverage report")
    args = parser.parse_args()
    if args.coverage and not args.check:
        parser.error("--coverage requires --check")
    try:
        manifest = build_manifest()
        rendered = render_manifest(manifest)
        if args.write:
            MANIFEST_PATH.write_text(rendered, encoding="utf-8")
        elif not MANIFEST_PATH.exists() or MANIFEST_PATH.read_text(encoding="utf-8") != rendered:
            print("FAIL: canonical manifest missing or stale; run python watchlist_manifest.py --write")
            return 1
        if args.coverage:
            result = check_report_coverage(json.loads(args.coverage.read_text(encoding="utf-8")), manifest)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["complete"] else 1
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"OK: {manifest['counts']['unique_entries']} canonical entries; {manifest['universe_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
