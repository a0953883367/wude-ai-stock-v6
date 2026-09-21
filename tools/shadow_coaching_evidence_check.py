#!/usr/bin/env python3
"""驗證 GPT 影子教導證據及正式層安全鎖；全程唯讀。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/weekly_shadow_coach.json"
HISTORY = ROOT / "reports/weekly_shadow_coach_history.json"
REGISTRY = ROOT / "reports/shadow_candidate_registry.json"

TRUE_LOCKS = (
    "shadow_only",
    "formal_v6_unchanged",
    "formal_rankings_unchanged",
    "formal_weights_unchanged",
    "historical_data_never_deleted",
)
FALSE_LOCKS = (
    "automatic_orders",
    "automatic_merge",
    "automatic_formal_promotion",
)
CANDIDATE_FALSE_LOCKS = (
    "visible_in_predictions",
    "affects_formal_v6",
    "affects_formal_rankings",
    "affects_formal_weights",
    "automatic_orders",
    "arbitrary_code_allowed",
    "automatic_formal_promotion",
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_policy(policy: Any, errors: list[str], label: str) -> None:
    if not isinstance(policy, dict):
        errors.append(f"{label} 缺少安全政策")
        return
    for key in TRUE_LOCKS:
        if policy.get(key) is not True:
            errors.append(f"{label} 安全鎖不正確：{key}=true")
    for key in FALSE_LOCKS:
        if policy.get(key) is not False:
            errors.append(f"{label} 安全鎖不正確：{key}=false")


def inspect_shadow_coaching_evidence(
    report_path: Path = REPORT,
    history_path: Path = HISTORY,
    registry_path: Path = REGISTRY,
) -> dict[str, Any]:
    errors: list[str] = []
    report = _read(report_path)
    history = _read(history_path)
    registry = _read(registry_path)

    if report.get("status") != "ok" or report.get("mode") != "openai":
        errors.append("最新教導報告不是成功的 OpenAI 影子教導")
    api = report.get("api") or {}
    if api.get("called") is not True or api.get("store") is not False:
        errors.append("OpenAI 呼叫證據或不儲存設定不完整")
    if not str(api.get("response_id") or "").startswith("resp_"):
        errors.append("缺少 OpenAI 回應識別碼")

    source = report.get("source") or {}
    if int(source.get("independent_events") or 0) < 1:
        errors.append("沒有可稽核的獨立成熟事件")
    if (source.get("backup") or {}).get("verified") is not True:
        errors.append("教導前證據備份未驗證")
    archive = source.get("cloud_archive") or {}
    if archive.get("verified") is not True or int(archive.get("errors") or 0) != 0:
        errors.append("雲端封存證據未通過")

    _check_policy(report.get("policy"), errors, "最新報告")
    generated_at = report.get("generated_at")
    matching_history = [
        item for item in history
        if isinstance(item, dict)
        and item.get("generated_at") == generated_at
        and item.get("status") == "ok"
        and item.get("mode") == "openai"
    ] if isinstance(history, list) else []
    if not matching_history:
        errors.append("歷史紀錄沒有對應的 OpenAI 教導")
    else:
        _check_policy(matching_history[-1].get("policy"), errors, "歷史紀錄")

    candidates = registry.get("candidates") if isinstance(registry, dict) else None
    if not isinstance(candidates, list) or not candidates:
        errors.append("沒有 GPT 候選規則紀錄")
        candidates = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f"候選規則 {index + 1} 格式錯誤")
            continue
        for key in CANDIDATE_FALSE_LOCKS:
            if candidate.get(key) is not False:
                errors.append(f"候選規則 {index + 1} 未鎖定：{key}=false")
    _check_policy(registry.get("policy") if isinstance(registry, dict) else None, errors, "候選登錄")

    return {
        "status": "passed" if not errors else "failed",
        "scope": "只讀取既有影子教導報告、歷史與候選登錄；不呼叫 API、不改排名、權重、下單或原始資料",
        "evidence": {
            "generated_at": generated_at,
            "response_id_present": bool(str(api.get("response_id") or "")),
            "independent_events": int(source.get("independent_events") or 0),
            "candidate_count": len(candidates),
            "trading_days_collected": registry.get("trading_days_collected") if isinstance(registry, dict) else None,
        },
        "errors": errors,
    }


def main() -> int:
    result = inspect_shadow_coaching_evidence()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
