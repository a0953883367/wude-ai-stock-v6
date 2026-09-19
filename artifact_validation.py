"""Evidence-first validation contract for reports, PPTX, PDF and documents."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any


SUPPORTED_SUFFIXES = {".pptx", ".pdf", ".docx", ".xlsx", ".html", ".md", ".json"}
REQUIRED_CHECKS = (
    "source_version",
    "numbers_dates_units",
    "cross_page_consistency",
    "visual_render_review",
)


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _package_check(path: Path) -> tuple[bool, str]:
    if path.suffix.casefold() not in {".pptx", ".docx", ".xlsx"}:
        return True, "不需要 Office 封裝檢查"
    if not zipfile.is_zipfile(path):
        return False, "Office 檔案封裝損壞"
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
    if "[Content_Types].xml" not in names:
        return False, "Office 檔案缺少必要結構"
    if path.suffix.casefold() == ".pptx" and not any(name.startswith("ppt/slides/slide") for name in names):
        return False, "PPTX 沒有投影片"
    return True, "Office 封裝正常"


def verify_artifact(spec: dict[str, Any], *, root: Path = Path(".")) -> dict[str, Any]:
    artifact = root / str(spec.get("artifact") or "")
    domain = str(spec.get("domain") or "")
    allowed_domains = {"stock_shadow", "zhiying_company", "wt_fasteners", "packaging_startup"}
    failures: list[str] = []
    if domain not in allowed_domains:
        failures.append("未指定有效的單一事業領域")
    if not _inside(root, artifact) or not artifact.is_file():
        failures.append("輸出檔案不存在或超出專案範圍")
    elif artifact.suffix.casefold() not in SUPPORTED_SUFFIXES:
        failures.append("不支援的輸出格式")

    sources = spec.get("sources") if isinstance(spec.get("sources"), list) else []
    if not sources:
        failures.append("沒有列出來源檔案")
    for source in sources:
        source_path = root / str(source)
        if not _inside(root, source_path) or not source_path.is_file():
            failures.append(f"來源不存在：{source}")

    checks = spec.get("checks") if isinstance(spec.get("checks"), dict) else {}
    for check in REQUIRED_CHECKS:
        evidence = checks.get(check)
        if not isinstance(evidence, dict) or evidence.get("passed") is not True or not str(evidence.get("evidence") or "").strip():
            failures.append(f"驗收未通過：{check}")

    if artifact.is_file():
        package_ok, package_message = _package_check(artifact)
        if not package_ok:
            failures.append(package_message)
    else:
        package_message = "尚無輸出檔案"

    verified = not failures
    return {
        "schema": "wude.artifact_validation.v1",
        "artifact": str(spec.get("artifact") or ""),
        "domain": domain,
        "status": "verified" if verified else "draft",
        "status_label": "可正式交付" if verified else "草稿；不可標示完成",
        "verified": verified,
        "package_check": package_message,
        "failures": failures,
        "formal_system_changed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="驗收報表、PPT、PDF 或文件")
    parser.add_argument("spec", type=Path, help="驗收規格 JSON")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    print(json.dumps(verify_artifact(spec, root=args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
