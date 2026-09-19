import json
import zipfile
from pathlib import Path

from artifact_validation import verify_artifact


def _checks(passed=True):
    return {
        key: {"passed": passed, "evidence": "已核對測試證據" if passed else ""}
        for key in ("source_version", "numbers_dates_units", "cross_page_consistency", "visual_render_review")
    }


def test_unverified_ppt_remains_draft(tmp_path: Path):
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "slides.pptx"
    artifact.write_bytes(b"not-a-pptx")
    result = verify_artifact({"artifact": "slides.pptx", "domain": "stock_shadow", "sources": ["source.json"], "checks": {}}, root=tmp_path)
    assert result["verified"] is False
    assert result["status"] == "draft"
    assert "Office 檔案封裝損壞" in result["failures"]


def test_validated_ppt_requires_sources_evidence_and_package(tmp_path: Path):
    (tmp_path / "source.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "slides.pptx", "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("ppt/slides/slide1.xml", "<slide/>")
    result = verify_artifact({
        "artifact": "slides.pptx",
        "domain": "zhiying_company",
        "sources": ["source.json"],
        "checks": _checks(),
    }, root=tmp_path)
    assert result["verified"] is True
    assert result["status_label"] == "可正式交付"
    assert result["formal_system_changed"] is False


def test_cross_domain_or_missing_source_cannot_pass(tmp_path: Path):
    (tmp_path / "report.md").write_text("report", encoding="utf-8")
    result = verify_artifact({"artifact": "report.md", "domain": "all", "sources": ["missing.json"], "checks": _checks()}, root=tmp_path)
    assert result["verified"] is False
    assert "未指定有效的單一事業領域" in result["failures"]
