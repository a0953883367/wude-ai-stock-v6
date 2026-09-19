"""Deterministic, domain-isolated conversation compaction without model calls."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping


TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")
CATEGORY_MARKERS = {
    "decisions": ("決定", "同意", "採用", "確認", "approve", "decide"),
    "constraints": ("不要", "禁止", "必須", "只能", "不可", "without", "must"),
    "pending": ("待", "尚未", "接下來", "未完成", "todo", "pending"),
}


def estimate_tokens(text: str) -> int:
    """Return a transparent approximation; this is not provider billing data."""
    return len(TOKEN_PATTERN.findall(str(text or "")))


def _clean(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split())[:limit]


def _unique(values: Iterable[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if not value or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def compact_messages(
    messages: Iterable[Mapping[str, Any]],
    *,
    namespace: str,
    keep_recent: int = 6,
    max_items_per_category: int = 8,
) -> dict[str, Any]:
    rows = [dict(item) for item in messages if str(item.get("namespace") or namespace) == namespace]
    recent = rows[-max(0, keep_recent):] if keep_recent else []
    older = rows[: max(0, len(rows) - len(recent))]
    categories: dict[str, list[str]] = {key: [] for key in CATEGORY_MARKERS}
    facts: list[str] = []
    for row in older:
        content = _clean(row.get("content"))
        folded = content.casefold()
        matched = False
        for category, markers in CATEGORY_MARKERS.items():
            if any(marker in folded for marker in markers):
                categories[category].append(content)
                matched = True
        if not matched:
            facts.append(content)
    compacted = {
        key: _unique(values, max_items_per_category)
        for key, values in categories.items()
    }
    compacted["facts"] = _unique(facts, max_items_per_category)
    recent_rows = [
        {"role": _clean(row.get("role"), 24), "content": _clean(row.get("content")), "namespace": namespace}
        for row in recent
    ]
    raw_text = "\n".join(_clean(row.get("content"), 10000) for row in rows)
    compact_text = "\n".join(item for values in compacted.values() for item in values) + "\n" + "\n".join(
        row["content"] for row in recent_rows
    )
    return {
        "schema": "wude.compacted_memory.v1",
        "namespace": namespace,
        "summary": compacted,
        "recent": recent_rows,
        "source_message_count": len(rows),
        "compacted_message_count": len(older),
        "token_estimate": {
            "method": "deterministic_estimate_not_billing",
            "before": estimate_tokens(raw_text),
            "after": estimate_tokens(compact_text),
        },
        "source_digest": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "raw_history_stored": False,
        "cross_domain_memory": False,
    }
