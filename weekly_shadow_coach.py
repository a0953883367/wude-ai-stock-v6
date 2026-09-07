#!/usr/bin/env python3
"""Create a governed weekly explanation for the independent shadow model.

The coach reads already-matured error events and a freshly verified cloud
archive result.  It may ask OpenAI for a structured explanation, but it only
writes shadow reports: it never changes forecasts, rankings, weights, model
control, historical evidence, or broker orders.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-luna"
MAX_EVENTS = 12
MAX_HISTORY = 104

LOCKED_POLICY = {
    "shadow_only": True,
    "formal_v6_unchanged": True,
    "formal_rankings_unchanged": True,
    "formal_weights_unchanged": True,
    "historical_data_never_deleted": True,
    "automatic_orders": False,
    "automatic_merge": False,
    "automatic_formal_promotion": False,
}

COACH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "root_causes": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cause": {"type": "string"},
                    "evidence_events": {"type": "integer", "minimum": 0},
                    "explanation": {"type": "string"},
                    "shadow_experiment": {"type": "string"},
                    "risk": {"type": "string"},
                },
                "required": [
                    "cause",
                    "evidence_events",
                    "explanation",
                    "shadow_experiment",
                    "risk",
                ],
            },
        },
        "priority_actions": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string"},
        },
        "data_gaps": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string"},
        },
        "confidence_notes": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string"},
        },
    },
    "required": [
        "headline",
        "root_causes",
        "priority_actions",
        "data_gaps",
        "confidence_notes",
    ],
}


class CoachBlocked(RuntimeError):
    """Raised when a safety or evidence prerequisite is not satisfied."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise CoachBlocked(f"cannot read required JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CoachBlocked(f"required JSON is not an object: {path}")
    return value


def _write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _require_locked_inputs(
    learning: dict[str, Any],
    backup: dict[str, Any],
    archive: dict[str, Any] | None,
) -> None:
    policy = learning.get("policy") or {}
    required_learning_locks = {
        "formal_v6_frozen": True,
        "formal_ranking_unchanged": True,
        "formal_weights_unchanged": True,
        "historical_predictions_never_rewritten": True,
        "broker_orders": False,
    }
    for key, expected in required_learning_locks.items():
        if policy.get(key) is not expected:
            raise CoachBlocked(f"learning safety lock is missing: {key}={expected}")

    required_backup = {
        "status": "ok",
        "verified": True,
        "private_backup": True,
        "formal_v6_modified": False,
        "automatic_orders": False,
        "public_database_exposed": False,
    }
    for key, expected in required_backup.items():
        if backup.get(key) != expected:
            raise CoachBlocked(f"backup safety check failed: {key}={expected}")

    if archive is not None:
        counts = archive.get("counts") or {}
        if archive.get("status") != "ok" or int(counts.get("errors") or 0):
            raise CoachBlocked("Google Drive archive verification is not green")
        if int(counts.get("total") or 0) < 1:
            raise CoachBlocked("Google Drive archive verification has no files")


def _bounded_context(
    learning: dict[str, Any],
    backup: dict[str, Any],
    archive: dict[str, Any] | None,
) -> dict[str, Any]:
    errors = learning.get("error_learning") or {}
    progress = learning.get("progress") or {}
    signal_health = learning.get("signal_health") or {}
    events = errors.get("recent_events") or []
    safe_events = []
    for event in events[:MAX_EVENTS]:
        if not isinstance(event, dict):
            continue
        safe_events.append({
            key: event.get(key)
            for key in (
                "event_id",
                "market",
                "symbol",
                "cohort",
                "source_start_date",
                "evaluated_end_date",
                "row_count",
                "horizons",
                "predicted_directions",
                "worst_directional_return_pct",
                "largest_actual_move_pct",
                "primary_cause",
                "cause_label",
                "diagnosis",
                "learning_action",
            )
        })

    archive_counts = (archive or {}).get("counts") or {}
    return {
        "report_updated_at": learning.get("updated_at"),
        "trading_days_collected": int(progress.get("trading_days_collected") or 0),
        "promotion_review_days": int(progress.get("promotion_review_days") or 60),
        "raw_error_rows": int(errors.get("raw_error_rows") or 0),
        "independent_events": int(errors.get("independent_events") or 0),
        "duplicate_rows_collapsed": int(errors.get("duplicate_rows_collapsed") or 0),
        "cause_counts": errors.get("cause_counts") or {},
        "signal_health": signal_health,
        "recent_matured_events": safe_events,
        "backup": {
            "created_at": backup.get("created_at"),
            "verified": backup.get("verified"),
            "table_counts": backup.get("table_counts") or {},
        },
        "cloud_archive": {
            "verified": archive is not None,
            "total": int(archive_counts.get("total") or 0),
            "uploaded": int(archive_counts.get("uploaded") or 0),
            "verified_existing": int(archive_counts.get("verified_existing") or 0),
            "errors": int(archive_counts.get("errors") or 0),
        },
    }


def _dry_run_coach(context: dict[str, Any]) -> dict[str, Any]:
    causes = context.get("cause_counts") or {}
    root_causes = []
    for cause, count in sorted(
        causes.items(), key=lambda item: (-int(item[1] or 0), str(item[0]))
    )[:5]:
        root_causes.append({
            "cause": str(cause),
            "evidence_events": int(count or 0),
            "explanation": "沿用既有事件級分類，等待生成式教練補充可驗證原因。",
            "shadow_experiment": "只建立候選假設並做前向回測，不變更正式模型。",
            "risk": "樣本仍在60個交易日驗證期，禁止依單週結果升級。",
        })
    return {
        "headline": (
            f"模擬教練已整理 {context['independent_events']} 個獨立錯誤事件；"
            "尚未呼叫付費API。"
        ),
        "root_causes": root_causes,
        "priority_actions": [
            "先比較事件級錯誤，不把同一股票的多個觀察窗重複計分。",
            "只把改善建議送入影子前向回測。",
            "滿60個交易日後仍須人工決定是否影響正式V6。",
        ],
        "data_gaps": [
            key
            for key, value in (context.get("signal_health") or {}).items()
            if int((value or {}).get("trade_signal_samples") or 0) == 0
        ][:5],
        "confidence_notes": [
            f"目前為 {context['trading_days_collected']}/"
            f"{context['promotion_review_days']} 個交易日。",
            "模擬模式只驗證資料與安全流程，不代表API分析品質。",
        ],
    }


def _response_output_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                return str(content.get("text") or "")
    raise CoachBlocked("OpenAI response did not contain structured output text")


def _openai_coach(
    context: dict[str, Any],
    *,
    api_key: str,
    model: str,
    session: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not api_key:
        raise CoachBlocked("OPENAI_API_KEY is required for OpenAI mode")
    instructions = (
        "你是武得AI股票系統的影子教練。只分析已成熟且有結果的錯誤事件。"
        "將重複觀察窗視為同一事件，禁止使用未來資料，禁止提出個股買賣指令，"
        "禁止修改正式V6、正式排名、正式權重、歷史資料或下單。"
        "提出的每項改善都必須是可前向驗證的影子實驗；樣本不足時明確說明。"
    )
    request_body = {
        "model": model,
        "store": False,
        "max_output_tokens": 1800,
        "input": [
            {"role": "developer", "content": instructions},
            {
                "role": "user",
                "content": "請根據以下凍結資料產生本週教導報告：\n"
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "weekly_shadow_coach",
                "strict": True,
                "schema": COACH_SCHEMA,
            }
        },
    }
    if session is None:
        import requests

        client = requests.Session()
    else:
        client = session
    response = client.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=request_body,
        timeout=120,
    )
    response.raise_for_status()
    raw = response.json()
    coach = json.loads(_response_output_text(raw))
    if not isinstance(coach, dict):
        raise CoachBlocked("OpenAI structured output is not an object")
    return coach, {
        "called": True,
        "model": model,
        "response_id": str(raw.get("id") or ""),
        "usage": raw.get("usage") or {},
        "store": False,
    }


def _append_history(path: Path, report: dict[str, Any]) -> None:
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        history = []
    if not isinstance(history, list):
        history = []
    history.append({
        "generated_at": report.get("generated_at"),
        "status": report.get("status"),
        "mode": report.get("mode"),
        "source": report.get("source"),
        "headline": (report.get("coach") or {}).get("headline"),
        "policy": report.get("policy"),
    })
    _write_json(path, history[-MAX_HISTORY:])


def build_weekly_coach(
    reports_dir: Path,
    *,
    mode: str = "dry_run",
    archive_result: Path | None = None,
    output: Path | None = None,
    history: Path | None = None,
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    session: Any | None = None,
    generated_at: str = "",
) -> dict[str, Any]:
    reports_dir = Path(reports_dir)
    output = output or reports_dir / "weekly_shadow_coach.json"
    history = history or reports_dir / "weekly_shadow_coach_history.json"
    learning = _read_json(reports_dir / "model_learning.json")
    backup = _read_json(reports_dir / "prediction_evidence_backup_health.json")
    archive = _read_json(archive_result) if archive_result else None
    _require_locked_inputs(learning, backup, archive)
    context = _bounded_context(learning, backup, archive)

    if mode == "openai":
        coach, api = _openai_coach(
            context,
            api_key=api_key,
            model=model,
            session=session,
        )
    elif mode == "dry_run":
        coach = _dry_run_coach(context)
        api = {"called": False, "model": model, "reason": "dry_run", "store": False}
    else:
        raise CoachBlocked(f"unsupported mode: {mode}")

    report = {
        "schema": "wude.weekly_shadow_coach.v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "mode": mode,
        "source": context,
        "coach": coach,
        "api": api,
        "policy": dict(LOCKED_POLICY),
    }
    _write_json(output, report)
    _append_history(history, report)
    return report


def _write_failure(path: Path, *, mode: str, message: str) -> None:
    _write_json(path, {
        "schema": "wude.weekly_shadow_coach.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "blocked",
        "mode": mode,
        "error": message,
        "policy": dict(LOCKED_POLICY),
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--mode", choices=("dry_run", "openai"), default="dry_run")
    parser.add_argument("--archive-result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()
    output = args.output or args.reports_dir / "weekly_shadow_coach.json"
    try:
        report = build_weekly_coach(
            args.reports_dir,
            mode=args.mode,
            archive_result=args.archive_result,
            output=output,
            history=args.history,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=args.model,
        )
    except Exception as exc:
        _write_failure(output, mode=args.mode, message=str(exc))
        print(f"weekly shadow coach blocked: {exc}")
        return 1
    print(
        "weekly shadow coach: "
        f"mode={report['mode']} events={report['source']['independent_events']} "
        f"output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
