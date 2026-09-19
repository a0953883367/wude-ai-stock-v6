import json
from pathlib import Path

import pytest

from context_governance import build_context_plan, load_text_context


def _catalog(root: Path) -> None:
    workspace = root / "agent_workspaces"
    workspace.mkdir()
    (workspace / "context_catalog.json").write_text(json.dumps({
        "schema": "wude.context_catalog.v1",
        "policy": {"default_max_files": 3, "default_max_bytes": 1000},
        "layers": {},
        "domains": {
            "stock_shadow": {"canonical": ["stock/*.json"], "reference": [], "research": ["research/*.json"], "temporary": [], "archive": ["archive/*.json"]},
            "zhiying_company": {"canonical": ["company/*.json"], "reference": [], "research": [], "temporary": [], "archive": []},
            "wt_fasteners": {"canonical": [], "reference": [], "research": [], "temporary": [], "archive": []},
            "packaging_startup": {"canonical": [], "reference": [], "research": [], "temporary": [], "archive": []}
        },
        "task_profiles": {"formal_answer": ["canonical", "reference"], "research": ["reference", "research"], "archive_review": ["archive"], "bad": ["archive"]}
    }, ensure_ascii=False), encoding="utf-8")


def test_plan_reads_only_routed_domain_and_selected_layers(tmp_path: Path):
    _catalog(tmp_path)
    for folder in ("stock", "company", "research"):
        (tmp_path / folder).mkdir()
    (tmp_path / "stock" / "formal.json").write_text('{"formal": true}', encoding="utf-8")
    (tmp_path / "company" / "private.json").write_text('{"company": true}', encoding="utf-8")
    (tmp_path / "research" / "candidate.json").write_text('{"candidate": true}', encoding="utf-8")
    plan = build_context_plan("檢查股票正式排名", root=tmp_path)
    assert [item["path"] for item in plan["files"]] == ["stock/formal.json"]
    assert plan["cross_domain_reads"] is False
    loaded = load_text_context(plan, root=tmp_path)
    assert "stock/formal.json" in loaded
    assert "company/private.json" not in loaded
    assert "research/candidate.json" not in loaded


def test_research_is_separate_and_archive_requires_explicit_profile(tmp_path: Path):
    _catalog(tmp_path)
    (tmp_path / "research").mkdir()
    (tmp_path / "archive").mkdir()
    (tmp_path / "research" / "candidate.json").write_text("{}", encoding="utf-8")
    (tmp_path / "archive" / "old.json").write_text("{}", encoding="utf-8")
    research = build_context_plan("股票影子研究", profile="research", root=tmp_path)
    assert research["files"][0]["layer"] == "research"
    with pytest.raises(ValueError):
        build_context_plan("股票稽核", profile="bad", root=tmp_path)
    archive = build_context_plan("股票歷史稽核", profile="archive_review", root=tmp_path)
    assert archive["files"][0]["path"] == "archive/old.json"


def test_file_and_byte_limits_are_enforced(tmp_path: Path):
    _catalog(tmp_path)
    (tmp_path / "stock").mkdir()
    for index in range(5):
        (tmp_path / "stock" / f"{index}.json").write_text("x" * 20, encoding="utf-8")
    plan = build_context_plan("股票", root=tmp_path, max_files=2, max_bytes=30)
    assert plan["usage"]["files"] == 1
    assert plan["usage"]["truncated"] is True
