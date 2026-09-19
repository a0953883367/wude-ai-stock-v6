import json
import zipfile
from pathlib import Path

from presentation_delivery import verify_presentation_delivery, write_delivery_record


def _checks():
    return {
        key: {"passed": True, "evidence": "已完成實際核對"}
        for key in ("source_version", "numbers_dates_units", "cross_page_consistency", "visual_render_review")
    }


def _pptx(path: Path, slides: int) -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        for index in range(1, slides + 1):
            package.writestr(f"ppt/slides/slide{index}.xml", "<slide/>")


def _png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"render-evidence")


def _spec() -> dict:
    return {
        "artifact": "deck.pptx",
        "domain": "zhiying_company",
        "sources": ["source.json"],
        "checks": _checks(),
        "presentation": {
            "expected_slide_count": 2,
            "rendered_slides": ["renders/slide-1.png", "renders/slide-2.png"],
        },
    }


def test_presentation_is_ready_only_with_one_valid_render_per_slide(tmp_path: Path):
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    (tmp_path / "renders").mkdir()
    _pptx(tmp_path / "deck.pptx", 2)
    _png(tmp_path / "renders" / "slide-1.png")
    _png(tmp_path / "renders" / "slide-2.png")

    result = verify_presentation_delivery(_spec(), root=tmp_path)

    assert result["verified"] is True
    assert result["status"] == "ready_for_handoff"
    assert result["slide_count"] == 2
    assert result["rendered_slide_count"] == 2
    assert result["external_publish"] is False


def test_missing_slide_render_blocks_handoff(tmp_path: Path):
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    (tmp_path / "renders").mkdir()
    _pptx(tmp_path / "deck.pptx", 2)
    _png(tmp_path / "renders" / "slide-1.png")

    result = verify_presentation_delivery(_spec(), root=tmp_path)

    assert result["verified"] is False
    assert result["status"] == "draft"
    assert any("slide-2.png" in failure for failure in result["failures"])


def test_slide_count_mismatch_blocks_handoff(tmp_path: Path):
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    (tmp_path / "renders").mkdir()
    _pptx(tmp_path / "deck.pptx", 1)
    _png(tmp_path / "renders" / "slide-1.png")
    _png(tmp_path / "renders" / "slide-2.png")

    result = verify_presentation_delivery(_spec(), root=tmp_path)

    assert result["verified"] is False
    assert any("投影片數不一致" in failure for failure in result["failures"])


def test_delivery_record_is_written_for_auditing(tmp_path: Path):
    (tmp_path / "source.json").write_text("{}", encoding="utf-8")
    (tmp_path / "renders").mkdir()
    _pptx(tmp_path / "deck.pptx", 2)
    _png(tmp_path / "renders" / "slide-1.png")
    _png(tmp_path / "renders" / "slide-2.png")
    (tmp_path / "spec.json").write_text(json.dumps(_spec()), encoding="utf-8")

    target = write_delivery_record(tmp_path / "spec.json", tmp_path / "delivery.json", root=tmp_path)
    record = json.loads(target.read_text(encoding="utf-8"))

    assert record["status_label"] == "簡報可交付"
    assert len(record["artifact_sha256"]) == 64
