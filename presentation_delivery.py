"""Evidence-backed delivery gate for PowerPoint presentations.

This module never publishes or sends a deck.  It turns a PPTX validation spec
into a durable handoff record only after every slide has matching render
evidence and the shared artifact contract passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_validation import verify_artifact


PRESENTATION_SCHEMA = "wude.presentation_delivery.v1"
SLIDE_PATTERN = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _slide_count(path: Path) -> int:
    if not path.is_file() or not zipfile.is_zipfile(path):
        return 0
    with zipfile.ZipFile(path) as package:
        indexes = {
            int(match.group(1))
            for name in package.namelist()
            if (match := SLIDE_PATTERN.match(name))
        }
    return len(indexes)


def _valid_render(path: Path) -> bool:
    if path.suffix.casefold() not in IMAGE_SUFFIXES or not path.is_file():
        return False
    header = path.read_bytes()[:12]
    if path.suffix.casefold() == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if path.suffix.casefold() in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    return header.startswith(b"RIFF") and header[8:12] == b"WEBP"


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_presentation_delivery(spec: dict[str, Any], *, root: Path = Path(".")) -> dict[str, Any]:
    """Verify PPTX structure, review evidence and one render per slide."""

    base = verify_artifact(spec, root=root)
    failures = list(base["failures"])
    artifact = root / str(spec.get("artifact") or "")
    slide_count = _slide_count(artifact)
    if artifact.suffix.casefold() != ".pptx":
        failures.append("簡報交付流程只接受 PPTX")

    presentation = spec.get("presentation") if isinstance(spec.get("presentation"), dict) else {}
    expected = presentation.get("expected_slide_count")
    if not isinstance(expected, int) or expected <= 0:
        failures.append("未指定有效的預期投影片數")
    elif expected != slide_count:
        failures.append(f"投影片數不一致：預期 {expected} 頁，實際 {slide_count} 頁")

    rendered = presentation.get("rendered_slides") if isinstance(presentation.get("rendered_slides"), list) else []
    normalized = [str(item) for item in rendered]
    if len(normalized) != len(set(normalized)):
        failures.append("逐頁渲染證據含有重複檔案")
    if len(normalized) != slide_count:
        failures.append(f"逐頁渲染證據不足：需要 {slide_count} 張，目前 {len(normalized)} 張")

    valid_render_count = 0
    for item in normalized:
        render_path = root / item
        if not _inside(root, render_path) or not _valid_render(render_path):
            failures.append(f"渲染證據不存在、超出專案範圍或格式損壞：{item}")
        else:
            valid_render_count += 1

    verified = not failures
    result = {
        "schema": PRESENTATION_SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifact": str(spec.get("artifact") or ""),
        "domain": base["domain"],
        "status": "ready_for_handoff" if verified else "draft",
        "status_label": "簡報可交付" if verified else "簡報草稿；不可交付",
        "verified": verified,
        "slide_count": slide_count,
        "rendered_slide_count": valid_render_count,
        "artifact_sha256": _fingerprint(artifact) if artifact.is_file() else None,
        "sources": [str(item) for item in spec.get("sources", [])] if isinstance(spec.get("sources"), list) else [],
        "checks": base.get("package_check"),
        "failures": failures,
        "external_publish": False,
        "formal_system_changed": False,
    }
    return result


def write_delivery_record(spec_path: Path, output_path: Path, *, root: Path = Path(".")) -> Path:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result = verify_presentation_delivery(spec, root=root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="產生 PPTX 逐頁驗收與交付紀錄")
    parser.add_argument("spec", type=Path, help="PPTX 驗收規格 JSON")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("reports/presentation_delivery.json"))
    args = parser.parse_args()
    target = write_delivery_record(args.spec, args.output, root=args.root)
    print(target)


if __name__ == "__main__":
    main()
