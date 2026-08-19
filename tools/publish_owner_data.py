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
    source=json.loads(Path("reports/all_analysis.json").read_text(encoding="utf-8"))
    rows=source.get("data")
    if not isinstance(rows,list) or not rows:
        raise RuntimeError("reports/all_analysis.json has no rows")
    payload=json.dumps({"data":rows,"updated_at":source.get("updated_at","等待更新"),"period":source.get("period","—")},ensure_ascii=False).encode("utf-8")
    request=urllib.request.Request(
        OWNER_INGEST_URL,
        data=payload,method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {token}","OAI-Sites-Authorization":f"Bearer {token}"})
    result = _publish(request)
    if not result.get("ok") or result.get("count")!=len(rows):
        raise RuntimeError(f"owner-site publish was not accepted: {result}")
    print(f"Published {len(rows)} complete rows to the owner site.")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
