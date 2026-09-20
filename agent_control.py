"""Central control plane for the user's isolated AI agents.

This module deliberately stores only routing, authorization and health metadata.
Business records remain in their own systems.  It never calls a paid model,
sends a message, changes a formal stock score or places an order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


CONTROL_VERSION = "CENTRAL-AGENT-CONTROL-V1"


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    namespace: str
    mode: str
    description: str
    keywords: tuple[str, ...]
    safe_actions: tuple[str, ...]
    connectors: tuple[str, ...] = ()
    daily_call_limit: int = 100
    daily_token_limit: int = 250_000
    retry_limit: int = 2


AGENTS: dict[str, AgentDefinition] = {
    "stock_shadow": AgentDefinition(
        agent_id="stock_shadow",
        name="武得股票影子驗證 Agent",
        namespace="stock_shadow",
        mode="active_shadow",
        description="讀取既有股票資料、執行隔離驗證、整理衝突與風險；不改正式 V6。",
        keywords=("股票", "台股", "美股", "etf", "v6", "影子", "排名", "股價", "法人"),
        safe_actions=("read_status", "analyze", "shadow_validate", "draft_report"),
        connectors=("github_reports", "railway_read_only"),
        daily_call_limit=120,
        daily_token_limit=300_000,
    ),
    "wt_fasteners": AgentDefinition(
        agent_id="wt_fasteners",
        name="WT 螺絲電商 Agent",
        namespace="wt_fasteners",
        mode="sandbox_waiting_source",
        description="可建立商品、競品與營運分析草稿；商店資料來源與對外發布尚未啟用。",
        keywords=("wt", "fasteners", "螺絲", "amazon", "電商", "商品", "上架", "廣告"),
        safe_actions=("read_status", "analyze", "draft_report", "simulate_workflow"),
        daily_call_limit=60,
        daily_token_limit=160_000,
    ),
    "packaging_startup": AgentDefinition(
        agent_id="packaging_startup",
        name="包裝創業 Agent",
        namespace="packaging_startup",
        mode="sandbox_ready",
        description="可整理市場、成本與投資評估草稿；付款、採購與對外聯絡保持鎖定。",
        keywords=("包裝", "創業", "紙箱", "包材", "成本", "投資評估", "供應商"),
        safe_actions=("read_status", "analyze", "draft_report", "simulate_workflow"),
        daily_call_limit=60,
        daily_token_limit=160_000,
    ),
}


BLOCKED_ACTIONS: dict[str, str] = {
    "formal_weight_change": "正式股票權重必須由本人審核，不由 Agent 自動修改。",
    "formal_ranking_change": "正式排名維持鎖定，只能先做影子驗證。",
    "broker_order": "不得連券商或自動下單。",
    "external_send": "寄信、訊息或通知前必須取得本次明確核准。",
    "external_publish": "上架或公開發布前必須取得本次明確核准。",
    "payment": "付款與採購必須由本人確認。",
    "delete_history": "不可自動刪除歷史或稽核資料。",
}


@dataclass
class UsageLedger:
    calls: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    task_keys: set[str] = field(default_factory=set)

    def reserve(self, agent: AgentDefinition, calls: int, tokens: int) -> tuple[bool, str]:
        next_calls = self.calls.get(agent.agent_id, 0) + max(0, calls)
        next_tokens = self.tokens.get(agent.agent_id, 0) + max(0, tokens)
        if next_calls > agent.daily_call_limit:
            return False, "已達每日 API 呼叫上限"
        if next_tokens > agent.daily_token_limit:
            return False, "已達每日 Token 上限"
        self.calls[agent.agent_id] = next_calls
        self.tokens[agent.agent_id] = next_tokens
        return True, "用量在限制內"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def route_task(text: str, explicit_agent: str | None = None) -> AgentDefinition:
    if explicit_agent:
        if explicit_agent not in AGENTS:
            raise ValueError(f"未知 Agent：{explicit_agent}")
        return AGENTS[explicit_agent]
    normalized = str(text or "").casefold()
    scores = {
        key: sum(1 for keyword in agent.keywords if keyword.casefold() in normalized)
        for key, agent in AGENTS.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        raise ValueError("任務領域不明，必須先指定 Agent，避免資料送錯事業。")
    return AGENTS[best]


def task_key(agent_id: str, action: str, payload: Mapping[str, Any] | None = None) -> str:
    canonical = json.dumps(
        {"agent_id": agent_id, "action": action, "payload": payload or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def authorize_task(
    agent_id: str,
    action: str,
    payload: Mapping[str, Any] | None = None,
    ledger: UsageLedger | None = None,
    calls: int = 0,
    tokens: int = 0,
) -> dict[str, Any]:
    agent = route_task("", explicit_agent=agent_id)
    digest = task_key(agent_id, action, payload)
    result: dict[str, Any] = {
        "agent_id": agent_id,
        "namespace": agent.namespace,
        "action": action,
        "task_key": digest,
        "retry_limit": agent.retry_limit,
        "payload_stored": False,
    }
    if action in BLOCKED_ACTIONS:
        result.update(
            decision="waiting_for_approval",
            executable=False,
            reason=BLOCKED_ACTIONS[action],
        )
        return result
    if action not in agent.safe_actions:
        result.update(
            decision="blocked_unknown_action",
            executable=False,
            reason="此動作未列入安全清單，必須先人工核對。",
        )
        return result
    if ledger is not None:
        if digest in ledger.task_keys:
            result.update(decision="duplicate", executable=False, reason="相同任務已登錄，不重複執行。")
            return result
        allowed, reason = ledger.reserve(agent, calls=calls, tokens=tokens)
        if not allowed:
            result.update(decision="budget_blocked", executable=False, reason=reason)
            return result
        ledger.task_keys.add(digest)
    result.update(decision="safe_to_run", executable=True, reason="安全、可逆且未跨越授權邊界。")
    return result


def _stock_status(reports_dir: Path) -> dict[str, Any]:
    learning = _read_json(reports_dir / "model_learning.json")
    validation = _read_json(reports_dir / "validation_60d.json")
    coach = _read_json(reports_dir / "weekly_shadow_coach.json")
    guard = _read_json(reports_dir / "system_guard.json")
    progress = learning.get("progress") or {}
    calls = ((coach.get("api") or {}).get("request_count"))
    return {
        "status": "running_shadow",
        "status_label": "影子驗證運作中",
        "source_updated_at": learning.get("updated_at") or validation.get("updated_at"),
        "trading_days_collected": int(
            validation.get("trading_days_collected") or progress.get("trading_days_collected") or 0
        ),
        "target_trading_days": int(validation.get("target_trading_days") or progress.get("promotion_review_days") or 60),
        "weekly_coach_last_run": coach.get("generated_at"),
        "weekly_coach_mode": coach.get("mode") or "尚無紀錄",
        "weekly_coach_api_requests": int(calls or 0),
        "system_guard": guard.get("status") or "尚無紀錄",
        "formal_v6_locked": True,
        "places_orders": False,
    }


def build_report(reports_dir: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    stock_status = _stock_status(reports_dir)
    statuses = {
        "stock_shadow": stock_status,
        "wt_fasteners": {
            "status": "waiting_source",
            "status_label": "測試模式；等待商店資料來源",
            "available_now": ["商品分析草稿", "競品整理", "流程模擬"],
            "blocked": ["自動上架", "廣告付款", "對外寄送"],
        },
        "packaging_startup": {
            "status": "sandbox_ready",
            "status_label": "測試模式可用",
            "available_now": ["市場分析", "成本試算", "投資評估草稿"],
            "blocked": ["採購付款", "聯絡供應商", "公開發布"],
        },
    }
    agents = []
    for agent_id, agent in AGENTS.items():
        agents.append(
            {
                "id": agent_id,
                "name": agent.name,
                "namespace": agent.namespace,
                "mode": agent.mode,
                "description": agent.description,
                "safe_actions": list(agent.safe_actions),
                "connectors": list(agent.connectors),
                "limits": {
                    "api_calls_per_day": agent.daily_call_limit,
                    "tokens_per_day": agent.daily_token_limit,
                    "retries_per_task": agent.retry_limit,
                },
                "runtime": statuses[agent_id],
            }
        )
    return {
        "schema": "wude.central_agent_control.v1",
        "version": CONTROL_VERSION,
        "generated_at": now,
        "status": "safe_first_version",
        "status_label": "中央 Agent 安全控制層已建立",
        "data_policy": {
            "cross_domain_reads": False,
            "shared_business_database": False,
            "stores_sensitive_payloads": False,
            "stores_only": ["路由", "權限", "用量", "核准狀態", "稽核摘要"],
        },
        "safety": {
            "formal_v6_locked": True,
            "formal_rankings_locked": True,
            "automatic_orders": False,
            "automatic_payments": False,
            "automatic_external_messages": False,
        },
        "blocked_actions": [
            {"action": action, "reason": reason} for action, reason in BLOCKED_ACTIONS.items()
        ],
        "agents": agents,
        "audit": {
            "event_count": 1,
            "events": [
                {
                    "event": "control_report_generated",
                    "at": now,
                    "payload_stored": False,
                    "result": "ok",
                }
            ],
        },
    }


def write_report(reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "agent_control.json"
    target.write_text(json.dumps(build_report(reports_dir), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="建立中央 Agent 安全控制層狀態報告")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--route", help="只預覽任務會交給哪個 Agent")
    parser.add_argument("--agent", choices=tuple(AGENTS), help="明確指定 Agent")
    args = parser.parse_args()
    if args.route is not None:
        agent = route_task(args.route, explicit_agent=args.agent)
        print(json.dumps({"agent_id": agent.agent_id, "name": agent.name, "namespace": agent.namespace}, ensure_ascii=False))
        return
    print(write_report(args.reports_dir))


if __name__ == "__main__":
    main()
