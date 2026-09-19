"""Measure bounded context and dynamic capability loading with byte/token estimates."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_governance import _expand, build_context_plan, load_catalog
from dynamic_capability_loader import build_capability_plan, load_capability_catalog


SCENARIOS = (
    ("stock_shadow", "shadow_validate", "股票影子驗證", "research"),
    ("zhiying_company", "draft_report", "製作至盈簡報", "output"),
    ("wt_fasteners", "analyze", "WT 螺絲商品分析", "formal_answer"),
    ("packaging_startup", "simulate_workflow", "包裝創業流程模擬", "formal_answer"),
)


def _all_domain_bytes(root: Path, catalog: dict[str, Any], namespace: str) -> int:
    domain = (catalog.get("domains") or {}).get(namespace) or {}
    paths: set[Path] = set()
    for layer in ("canonical", "reference", "research", "temporary", "archive"):
        for pattern in domain.get(layer) or []:
            paths.update(_expand(root, str(pattern)))
    return sum(path.stat().st_size for path in paths)


def _all_schema_bytes(catalog: dict[str, Any]) -> int:
    return sum(
        len(json.dumps(item.get("schema") or {}, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        for item in (catalog.get("tools") or {}).values()
        if isinstance(item, dict)
    )


def build_efficiency_report(*, root: Path = Path(".")) -> dict[str, Any]:
    context_catalog = load_catalog(root / Path("agent_workspaces/context_catalog.json"))
    capability_catalog = load_capability_catalog(root=root)
    eager_schema_bytes = _all_schema_bytes(capability_catalog)
    rows = []
    for agent_id, action, title, profile in SCENARIOS:
        context = build_context_plan(title, profile=profile, explicit_agent=agent_id, root=root)
        capability = build_capability_plan(title, action, explicit_agent=agent_id, root=root)
        eager_bytes = _all_domain_bytes(root, context_catalog, agent_id) + eager_schema_bytes
        dynamic_bytes = context["usage"]["bytes"] + capability["usage"]["schema_bytes"]
        before_tokens = (eager_bytes + 3) // 4
        after_tokens = (dynamic_bytes + 3) // 4
        reduction = 0.0 if before_tokens == 0 else round((before_tokens - after_tokens) / before_tokens * 100, 2)
        rows.append({
            "agent_id": agent_id,
            "action": action,
            "profile": profile,
            "before": {"bytes": eager_bytes, "estimated_tokens": before_tokens},
            "after": {"bytes": dynamic_bytes, "estimated_tokens": after_tokens},
            "reduction_pct": reduction,
            "selected_files": context["usage"]["files"],
            "selected_skills": capability["usage"]["skills"],
            "selected_tools": capability["usage"]["tools"],
        })
    average = round(sum(row["reduction_pct"] for row in rows) / len(rows), 2) if rows else 0.0
    return {
        "schema": "wude.context_efficiency.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "passed" if rows and all(row["after"]["bytes"] <= row["before"]["bytes"] for row in rows) else "attention",
        "measurement": "檔案與工具定義的靜態位元組／Token 估算；不是供應商帳單",
        "exact_provider_tokens": False,
        "average_reduction_pct": average,
        "scenarios": rows,
        "privacy": {"raw_messages_stored": False, "business_payloads_stored": False},
    }


def write_efficiency_report(output: Path, *, root: Path = Path(".")) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_efficiency_report(root=root), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="產生 Context Engineering 成效報表")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("reports/context_efficiency.json"))
    args = parser.parse_args()
    print(write_efficiency_report(args.output, root=args.root))


if __name__ == "__main__":
    main()
