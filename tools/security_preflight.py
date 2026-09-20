"""Fail closed when a credential or private key is committed by mistake."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {
    ".env",
    "fubon_local_config.json",
    "reports/owner_private_holding_simulation.json",
}
FORBIDDEN_SUFFIXES = {".p12", ".pfx", ".pem", ".key"}
SECRET_PATTERNS = {
    "OpenAI API Key": re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    "GitHub Token": re.compile(rb"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    "Google API Key": re.compile(rb"AIza[0-9A-Za-z_-]{25,}"),
    "Private Key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Slack Token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
}
MAX_SCAN_BYTES = 8_000_000


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / value.decode("utf-8") for value in output.split(b"\0") if value]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if path.name in FORBIDDEN_NAMES or relative in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"禁止追蹤的敏感檔案：{relative}")
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                findings.append(f"疑似{label}：{relative}")

    if findings:
        print("安全檢查未通過：")
        for finding in findings:
            print(f"- {finding}")
        print("請撤銷外洩憑證、移除檔案並重新產生密鑰；本工具不會輸出密鑰內容。")
        return 1

    print("安全檢查通過：沒有追蹤本機密鑰檔，也未發現高可信度憑證格式。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
