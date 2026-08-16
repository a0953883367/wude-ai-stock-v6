"""Publish only the friend-safe stock fields to the private friend site."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _midpoint(row, low_key, high_key, fallback_key=None):
    low, high = _number(row.get(low_key)), _number(row.get(high_key))
    if low is not None and high is not None:
        return round((low + high) / 2, 4)
    return _number(row.get(fallback_key)) if fallback_key else low or high


def _trend(row):
    action = str(row.get("action") or "")
    score = _number(row.get("score"))
    if "暫不買" in action or "避開" in str(row.get("risk") or ""):
        return "看跌"
    if score is not None and score >= 70:
        return "看漲"
    if score is not None and score < 55:
        return "看跌"
    return "震盪"


def sanitize(row):
    market = str(row.get("market") or "")
    return {
        "rank": _number(row.get("rank")),
        "name": str(row.get("name") or row.get("symbol") or "—"),
        "symbol": str(row.get("symbol") or "—"),
        "market": "台灣" if market == "TW" else "美國" if market == "US" else market,
        "kind": str(row.get("type") or "個股"),
        "theme": str(row.get("theme") or "一般"),
        "sector": str(row.get("sector") or row.get("industry") or "—"),
        "score": _number(row.get("score")),
        "timing": _number(row.get("entry_score")),
        "price": _number(row.get("price")),
        "buy": _number(row.get("buy_price")) or _midpoint(row, "buy_zone_low", "buy_zone_high"),
        "betterBuy": _midpoint(row, "better_buy_low", "better_buy_high"),
        "support": _number(row.get("support1")),
        "resistance": _number(row.get("resistance1")),
        "advice": str(row.get("action") or "觀察"),
        "risk": str(row.get("risk") or "依停損紀律控管"),
        "heat": "過熱" if (_number(row.get("rsi")) or 0) >= 78 else "正常",
        "trend": _trend(row),
    }


def main():
    site_url = os.getenv("FRIEND_SITE_URL", "").rstrip("/")
    bypass_token = os.getenv("FRIEND_SITE_BYPASS_TOKEN", "")
    ingest_token = os.getenv("FRIEND_INGEST_TOKEN", "")
    if not all((site_url, bypass_token, ingest_token)):
        print("Friend-site publish skipped: required secrets are not configured yet.")
        return 0

    source = json.loads(Path("reports/all_analysis.json").read_text(encoding="utf-8"))
    rows = source.get("data") if isinstance(source, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("reports/all_analysis.json has no publishable rows")
    stocks = [sanitize(row) for row in rows if isinstance(row, dict)]
    payload = json.dumps({
        "stocks": stocks,
        "updated": source.get("updated_at", "等待更新"),
        "version": "AI股票助理・朋友版",
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{site_url}/api/ingest",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ingest_token}",
            "OAI-Sites-Authorization": f"Bearer {bypass_token}",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok") or result.get("count") != len(stocks):
        raise RuntimeError(f"friend-site publish was not accepted: {result}")
    print(f"Published {len(stocks)} sanitized rows to the friend site.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
