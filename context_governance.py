"""Bounded context planning for the isolated central agents.

The planner returns a small allowlisted file plan before any content is read.
It never changes rankings, weights, reports, equipment or external systems.
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_control import route_task


CATALOG_PATH = Path("agent_workspaces/context_catalog.json")
TEXT_SUFFIXES = {".json", ".md", ".txt", ".py", ".csv"}


@dataclass(frozen=True)
class ContextItem:
    path: str
    layer: str
    domain: str
    bytes: int


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "wude.context_catalog.v1":
        raise ValueError("不支援的資料清冊版本")
    return value


def _safe_relative(root: Path, candidate: Path) -> str | None:
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _expand(root: Path, pattern: str) -> list[Path]:
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        return []
    matches = [Path(item) for item in glob.glob(str(root / pattern), recursive=True)]
    return sorted(path for path in matches if path.is_file())


def build_context_plan(
    task: str,
    *,
    profile: str = "formal_answer",
    explicit_agent: str | None = None,
    root: Path = Path("."),
    catalog_path: Path = CATALOG_PATH,
    max_files: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    catalog = load_catalog(root / catalog_path)
    agent = route_task(task, explicit_agent=explicit_agent)
    domains = catalog.get("domains") or {}
    domain = domains.get(agent.namespace)
    if not isinstance(domain, dict):
        raise ValueError(f"清冊缺少資料領域：{agent.namespace}")
    layers = (catalog.get("task_profiles") or {}).get(profile)
    if not isinstance(layers, list):
        raise ValueError(f"未知的任務設定：{profile}")
    if "archive" in layers and profile != "archive_review":
        raise ValueError("歷史封存只能由 archive_review 明確載入")

    policy = catalog.get("policy") or {}
    file_limit = int(max_files or policy.get("default_max_files") or 12)
    byte_limit = int(max_bytes or policy.get("default_max_bytes") or 200000)
    selected: list[ContextItem] = []
    used_bytes = 0
    truncated = False
    seen: set[str] = set()
    for layer in layers:
        for pattern in domain.get(layer) or []:
            for candidate in _expand(root, str(pattern)):
                relative = _safe_relative(root, candidate)
                if relative is None or relative in seen:
                    continue
                size = candidate.stat().st_size
                if len(selected) >= file_limit or used_bytes + size > byte_limit:
                    truncated = True
                    continue
                seen.add(relative)
                used_bytes += size
                selected.append(ContextItem(relative, layer, agent.namespace, size))
    return {
        "schema": "wude.context_plan.v1",
        "agent_id": agent.agent_id,
        "namespace": agent.namespace,
        "profile": profile,
        "layers": layers,
        "files": [item.__dict__ for item in selected],
        "limits": {"max_files": file_limit, "max_bytes": byte_limit},
        "usage": {"files": len(selected), "bytes": used_bytes, "truncated": truncated},
        "cross_domain_reads": False,
        "formal_outputs_mutated": False,
    }


def load_text_context(plan: dict[str, Any], *, root: Path = Path(".")) -> dict[str, str]:
    """Read only text files that were already selected by a context plan."""
    namespace = str(plan.get("namespace") or "")
    result: dict[str, str] = {}
    for item in plan.get("files") or []:
        if item.get("domain") != namespace:
            raise ValueError("偵測到跨事業資料讀取")
        path = root / str(item.get("path") or "")
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        relative = _safe_relative(root, path)
        if relative is None or not path.is_file():
            raise ValueError("清冊檔案不存在或超出專案範圍")
        result[relative] = path.read_text(encoding="utf-8")
    return result
