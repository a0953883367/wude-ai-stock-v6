import json
from pathlib import Path

import pytest

from agent_control import AGENTS, UsageLedger, authorize_task, build_report, route_task, task_key


def test_routes_each_business_without_crossing_namespaces():
    assert route_task("檢查台股影子驗證").agent_id == "stock_shadow"
    assert route_task("WT FASTENERS Amazon 商品").agent_id == "wt_fasteners"
    assert route_task("包裝創業成本試算").agent_id == "packaging_startup"
    assert len({agent.namespace for agent in AGENTS.values()}) == len(AGENTS)


def test_explicit_agent_wins_and_unknown_domain_stops():
    assert route_task("股票商品", explicit_agent="wt_fasteners").agent_id == "wt_fasteners"
    with pytest.raises(ValueError):
        route_task("幫我處理一下")


@pytest.mark.parametrize(
    "action",
    ["formal_weight_change", "formal_ranking_change", "broker_order", "external_send", "external_publish", "payment"],
)
def test_high_risk_actions_never_run_automatically(action):
    result = authorize_task("stock_shadow", action, {"secret": "not stored"})
    assert result["decision"] == "waiting_for_approval"
    assert result["executable"] is False
    assert result["payload_stored"] is False
    assert "secret" not in json.dumps(result)


def test_safe_task_is_idempotent_and_payload_is_hashed_only():
    ledger = UsageLedger()
    payload = {"customer": "private", "task": "status"}
    first = authorize_task("wt_fasteners", "read_status", payload, ledger, calls=1, tokens=20)
    second = authorize_task("wt_fasteners", "read_status", payload, ledger, calls=1, tokens=20)
    assert first["decision"] == "safe_to_run"
    assert second["decision"] == "duplicate"
    assert first["task_key"] == task_key("wt_fasteners", "read_status", payload)
    assert "private" not in json.dumps(first)


def test_api_budget_blocks_before_overage():
    ledger = UsageLedger(calls={"wt_fasteners": AGENTS["wt_fasteners"].daily_call_limit})
    result = authorize_task("wt_fasteners", "analyze", ledger=ledger, calls=1)
    assert result["decision"] == "budget_blocked"
    assert result["executable"] is False


def test_report_reads_stock_shadow_status_and_keeps_other_domains_isolated(tmp_path: Path):
    (tmp_path / "model_learning.json").write_text(
        json.dumps({"updated_at": "2026-09-19 20:00:00", "progress": {"trading_days_collected": 20, "promotion_review_days": 60}}),
        encoding="utf-8",
    )
    (tmp_path / "weekly_shadow_coach.json").write_text(
        json.dumps({"generated_at": "2026-09-13T15:56:00Z", "mode": "openai", "api": {"request_count": 1}}),
        encoding="utf-8",
    )
    report = build_report(tmp_path)
    stock = next(item for item in report["agents"] if item["id"] == "stock_shadow")
    assert stock["runtime"]["trading_days_collected"] == 20
    assert stock["runtime"]["target_trading_days"] == 60
    assert stock["runtime"]["formal_v6_locked"] is True
    assert report["data_policy"]["cross_domain_reads"] is False
    assert report["safety"]["automatic_orders"] is False
    assert report["safety"]["automatic_external_messages"] is False
