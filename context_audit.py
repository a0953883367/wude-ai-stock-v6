"""Audit the context catalog without moving or deleting business files."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_governance import _expand, _safe_relative, load_catalog


REQUIRED_LAYERS = ("canonical", "reference", "research", "temporary", "archive")


def audit_catalog(*, root: Path = Path("."), catalog_path: Path = Path("agent_workspaces/context_catalog.json")) -> dict[str, Any]:
    catalog = load_catalog(root / catalog_path)
    assignments: dict[str, list[dict[str, str]]] = defaultdict(list)
    missing_layers: list[dict[str, str]] = []
    unmatched_patterns: list[dict[str, str]] = []
    counts: dict[str, dict[str, int]] = {}
    domains = catalog.get("domains") or {}
    for domain_name, domain in domains.items():
        layer_counts: dict[str, int] = {}
        for layer in REQUIRED_LAYERS:
            if layer not in domain:
                missing_layers.append({"domain": domain_name, "layer": layer})
            matched: set[str] = set()
            for pattern in domain.get(layer) or []:
                paths = _expand(root, str(pattern))
                if not paths:
                    unmatched_patterns.append({"domain": domain_name, "layer": layer, "pattern": str(pattern)})
                for path in paths:
                    relative = _safe_relative(root, path)
                    if relative is None:
                        continue
                    matched.add(relative)
                    assignments[relative].append({"domain": domain_name, "layer": layer})
            layer_counts[layer] = len(matched)
        counts[domain_name] = layer_counts

    cross_domain = []
    cross_layer = []
    for path, rows in assignments.items():
        if len({row["domain"] for row in rows}) > 1:
            cross_domain.append({"path": path, "assignments": rows})
        if len({row["layer"] for row in rows}) > 1:
            cross_layer.append({"path": path, "assignments": rows})
    errors = len(missing_layers) + len(cross_domain) + len(cross_layer)
    return {
        "schema": "wude.context_audit.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "passed" if errors == 0 else "attention",
        "status_label": "五層資料清冊驗收通過" if errors == 0 else "五層資料清冊需要處理",
        "summary": {
            "domains": len(domains),
            "classified_files": len(assignments),
            "errors": errors,
            "warnings": len(unmatched_patterns),
        },
        "counts": counts,
        "missing_layers": missing_layers,
        "cross_domain_conflicts": cross_domain,
        "cross_layer_conflicts": cross_layer,
        "unmatched_patterns": unmatched_patterns,
        "safety": {
            "files_moved": 0,
            "files_deleted": 0,
            "formal_outputs_changed": False,
            "weights_changed": False,
            "shadow_records_changed": False,
        },
    }


def write_audit(*, root: Path = Path("."), output: Path = Path("reports/context_governance.json")) -> Path:
    target = root / output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(audit_catalog(root=root), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="稽核五層資料清冊")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("reports/context_governance.json"))
    args = parser.parse_args()
    print(write_audit(root=args.root, output=args.output))


if __name__ == "__main__":
    main()
