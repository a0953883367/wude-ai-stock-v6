"""Publish the complete owner-only analysis snapshot."""
from __future__ import annotations
import json, os, urllib.request
from pathlib import Path

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
        "https://wude-ai-stock-owner.a0953883367.chatgpt.site/api/ingest",
        data=payload,method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {token}","OAI-Sites-Authorization":f"Bearer {token}"})
    with urllib.request.urlopen(request,timeout=60) as response:
        result=json.loads(response.read().decode("utf-8"))
    if not result.get("ok") or result.get("count")!=len(rows):
        raise RuntimeError(f"owner-site publish was not accepted: {result}")
    print(f"Published {len(rows)} complete rows to the owner site.")
    return 0
if __name__=="__main__":
    raise SystemExit(main())
