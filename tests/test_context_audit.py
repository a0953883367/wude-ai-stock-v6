import json
from pathlib import Path

from context_audit import audit_catalog


def _write_catalog(root: Path, domains):
    (root / "agent_workspaces").mkdir()
    (root / "agent_workspaces" / "context_catalog.json").write_text(json.dumps({
        "schema": "wude.context_catalog.v1",
        "domains": domains,
    }), encoding="utf-8")


def _domain(canonical=None):
    return {"canonical": canonical or [], "reference": [], "research": [], "temporary": [], "archive": []}


def test_audit_passes_isolated_complete_catalog(tmp_path: Path):
    (tmp_path / "stock.json").write_text("{}", encoding="utf-8")
    _write_catalog(tmp_path, {
        "stock_shadow": _domain(["stock.json"]),
        "zhiying_company": _domain(),
        "wt_fasteners": _domain(),
        "packaging_startup": _domain(),
    })
    result = audit_catalog(root=tmp_path)
    assert result["status"] == "passed"
    assert result["summary"]["classified_files"] == 1
    assert result["safety"]["files_moved"] == 0


def test_audit_finds_cross_domain_and_cross_layer_conflicts(tmp_path: Path):
    (tmp_path / "shared.json").write_text("{}", encoding="utf-8")
    stock = _domain(["shared.json"])
    stock["reference"] = ["shared.json"]
    _write_catalog(tmp_path, {
        "stock_shadow": stock,
        "zhiying_company": _domain(["shared.json"]),
        "wt_fasteners": _domain(),
        "packaging_startup": _domain(),
    })
    result = audit_catalog(root=tmp_path)
    assert result["status"] == "attention"
    assert len(result["cross_domain_conflicts"]) == 1
    assert len(result["cross_layer_conflicts"]) == 1


def test_unmatched_pattern_is_warning_not_destructive_error(tmp_path: Path):
    _write_catalog(tmp_path, {
        "stock_shadow": _domain(["not-yet-present.json"]),
        "zhiying_company": _domain(),
        "wt_fasteners": _domain(),
        "packaging_startup": _domain(),
    })
    result = audit_catalog(root=tmp_path)
    assert result["status"] == "passed"
    assert result["summary"]["warnings"] == 1
