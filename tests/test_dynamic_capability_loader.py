from dynamic_capability_loader import build_capability_plan


def test_stock_shadow_loads_only_relevant_capabilities():
    plan = build_capability_plan("股票影子驗證", "shadow_validate", explicit_agent="stock_shadow")
    assert "stock_shadow_analysis" in plan["skills"]
    assert "presentations" not in plan["skills"]
    assert {tool["name"] for tool in plan["tools"]} == {"context_plan", "shadow_validator", "evidence_check"}


def test_draft_report_loads_presentation_pipeline():
    plan = build_capability_plan("製作至盈簡報", "draft_report", explicit_agent="zhiying_company")
    assert "presentations" in plan["skills"]
    assert "presentation_pipeline" in {tool["name"] for tool in plan["tools"]}


def test_blocked_action_never_loads_executor():
    plan = build_capability_plan("寫入 ERP", "erp_write", explicit_agent="zhiying_company")
    assert plan["blocked_action"] is True
    assert plan["executor_loaded"] is False
    assert plan["skills"] == []
    assert plan["tools"] == []


def test_capability_limits_are_enforced():
    plan = build_capability_plan(
        "股票影子驗證",
        "shadow_validate",
        explicit_agent="stock_shadow",
        max_skills=1,
        max_tools=1,
        max_schema_bytes=400,
    )
    assert plan["usage"]["skills"] == 1
    assert plan["usage"]["tools"] <= 1
    assert plan["usage"]["schema_bytes"] <= 400
    assert plan["usage"]["omitted_skills"] >= 1
