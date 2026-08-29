"""Publish the complete owner-only analysis snapshot."""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


OWNER_INGEST_URL = "https://wude-ai-stock-owner.a0953883367.chatgpt.site/api/ingest"
RETRY_DELAYS = (0, 3, 9)


def build_payload(report_dir: Path = Path("reports")) -> tuple[bytes, int]:
    """Build the private snapshot; the owner site applies a second allowlist."""
    source = json.loads((report_dir / "all_analysis.json").read_text(encoding="utf-8"))
    rows = source.get("data")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("reports/all_analysis.json has no rows")
    rotation_path = report_dir / "market_rotation_shadow.json"
    rotation = None
    if rotation_path.exists():
        candidate = json.loads(rotation_path.read_text(encoding="utf-8"))
        if isinstance(candidate, dict) and isinstance(candidate.get("markets"), dict):
            rotation = candidate
    latest_path = report_dir / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {}
    guard_path = report_dir / "system_guard.json"
    guard = json.loads(guard_path.read_text(encoding="utf-8")) if guard_path.exists() else {}
    integrity = {
        "report": {
            "candidate_count": source.get("candidate_count"),
            "analyzed_count": source.get("analyzed_count", len(rows)),
            "unavailable_count": source.get("unavailable_count"),
            "unavailable": latest.get("unavailable", []),
            "data_status": latest.get("data_status", {}),
        },
        "guard": guard,
    }
    payload = json.dumps({
        "data": rows,
        "rotation": rotation,
        "integrity": integrity,
        "updated_at": source.get("updated_at", "等待更新"),
        "period": source.get("period", "—"),
    }, ensure_ascii=False).encode("utf-8")
    return payload, len(rows)


def _publish(request: urllib.request.Request) -> dict:
    """Retry bounded transient HTTP/network failures without delaying reports."""
    last_error: Exception | None = None
    for attempt, delay in enumerate(RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == len(RETRY_DELAYS):
                raise
            print(
                f"Owner-site publish attempt {attempt} failed with HTTP {exc.code}; retrying."
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == len(RETRY_DELAYS):
                raise
            print(f"Owner-site publish attempt {attempt} failed: {exc}; retrying.")
    raise RuntimeError(f"owner-site publish failed: {last_error}")


def main():
    token=os.getenv("OWNER_SITE_BYPASS_TOKEN","")
    if not token:
        print("Owner-site publish skipped: secret is not configured yet.")
        return 0
    payload, row_count = build_payload()
    request=urllib.request.Request(
        OWNER_INGEST_URL,
        data=payload,method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {token}","OAI-Sites-Authorization":f"Bearer {token}"})
    result = _publish(request)
    if not result.get("ok") or result.get("count") != row_count:
        raise RuntimeError(f"owner-site publish was not accepted: {result}")
    print(f"Published {row_count} complete rows and private rotation data to the owner site.")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
