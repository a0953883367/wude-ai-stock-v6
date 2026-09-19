"""Load only the skills and tool schemas needed by one authorized task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_control import BLOCKED_ACTIONS, route_task


CATALOG_PATH = Path("agent_workspaces/capability_catalog.json")


def _catalog_path(root: Path, catalog_path: Path) -> Path:
    candidate = root / catalog_path
    if candidate.is_file():
        return candidate
    return Path(__file__).resolve().parent / catalog_path


def load_capability_catalog(*, root: Path = Path("."), catalog_path: Path = CATALOG_PATH) -> dict[str, Any]:
    value = json.loads(_catalog_path(root, catalog_path).read_text(encoding="utf-8"))
    if value.get("schema") != "wude.capability_catalog.v1":
        raise ValueError("不支援的能力清冊版本")
    return value


def build_capability_plan(
    task: str,
    action: str,
    *,
    explicit_agent: str | None = None,
    root: Path = Path("."),
    max_skills: int | None = None,
    max_tools: int | None = None,
    max_schema_bytes: int | None = None,
) -> dict[str, Any]:
    agent = route_task(task, explicit_agent=explicit_agent)
    catalog = load_capability_catalog(root=root)
    policy = catalog.get("policy") or {}
    skill_limit = int(max_skills or policy.get("default_max_skills") or 3)
    tool_limit = int(max_tools or policy.get("default_max_tools") or 5)
    byte_limit = int(max_schema_bytes or policy.get("default_max_schema_bytes") or 16000)
    blocked = action in BLOCKED_ACTIONS
    route = {} if blocked else (catalog.get("routes") or {}).get(action, {})

    available_skills = catalog.get("skills") or {}
    selected_skills: list[str] = []
    for name in route.get("skills") or []:
        definition = available_skills.get(name) or {}
        domains = definition.get("domains") or []
        if name in available_skills and ("*" in domains or agent.namespace in domains):
            selected_skills.append(name)
    omitted_skills = max(0, len(selected_skills) - skill_limit)
    selected_skills = selected_skills[:skill_limit]

    available_tools = catalog.get("tools") or {}
    selected_tools: list[dict[str, Any]] = []
    schema_bytes = 0
    omitted_tools = 0
    for name in route.get("tools") or []:
        definition = available_tools.get(name)
        if not isinstance(definition, dict):
            continue
        encoded = json.dumps(definition.get("schema") or {}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(selected_tools) >= tool_limit or schema_bytes + len(encoded) > byte_limit:
            omitted_tools += 1
            continue
        selected_tools.append({"name": name, "schema": definition.get("schema") or {}})
        schema_bytes += len(encoded)

    return {
        "schema": "wude.capability_plan.v1",
        "agent_id": agent.agent_id,
        "namespace": agent.namespace,
        "action": action,
        "skills": selected_skills,
        "tools": selected_tools,
        "limits": {"max_skills": skill_limit, "max_tools": tool_limit, "max_schema_bytes": byte_limit},
        "usage": {
            "skills": len(selected_skills),
            "tools": len(selected_tools),
            "schema_bytes": schema_bytes,
            "omitted_skills": omitted_skills,
            "omitted_tools": omitted_tools,
        },
        "executor_loaded": not blocked and bool(selected_tools),
        "blocked_action": blocked,
        "cross_domain_tools": False,
    }
