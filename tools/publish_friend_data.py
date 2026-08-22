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
    if (
        row.get("trade_guard_blocked") is True
        or row.get("market_contract_valid") is False
        or _number(row.get("overall_rank_tier")) == 0
        or "暫不買" in action
        or "避開" in str(row.get("risk") or "")
    ):
        return "看跌"
    if score is not None and score >= 70:
        return "看漲"
    if score is not None and score < 55:
        return "看跌"
    return "震盪"


def sanitize(row):
    market = str(row.get("market") or "")
    return {
        "rank": _number(row.get("overall_display_rank")) or _number(row.get("overall_rank")) or _number(row.get("rank")),
        "qualifiedRank": _number(row.get("overall_rank")),
        "groupCount": _number(row.get("ranking_group_count")),
        "rankTier": _number(row.get("overall_rank_tier")),
        "rankingScore": _number(row.get("overall_ranking_score")),
        "eligible": row.get("overall_eligible") is True,
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


def sanitize_accuracy(value):
    """Publish aggregate verification only; never expose owner-side records."""
    if not isinstance(value, dict):
        return {}
    groups = {}
    for key in ("TW_STOCK", "TW_ETF", "US_STOCK", "US_ETF"):
        source = value.get("groups", {}).get(key, {})
        groups[key] = {
            "tracks": source.get("tracks", {}),
            "top_k": source.get("top_k", {}),
        }
    return {
        "methodology_version": value.get("methodology_version"),
        "updated_at": value.get("updated_at"),
        "immutable_rule": value.get("immutable_rule"),
        "integrity": value.get("integrity", {}),
        "calibration": value.get("calibration", {}),
        "tw_threshold_calibration": value.get("tw_threshold_calibration", {}),
        "groups": groups,
    }


def main():
    site_url = os.getenv("FRIEND_SITE_URL", "").strip()
    for prefix in ("網址：", "網址:", "URL：", "URL:"):
        if site_url.startswith(prefix):
            site_url = site_url[len(prefix):].strip()
    if site_url and not site_url.startswith(("https://", "http://")):
        site_url = "https://" + site_url
    site_url = site_url.rstrip("/")
    bypass_token = os.getenv("FRIEND_SITE_BYPASS_TOKEN", "")
    ingest_token = bypass_token
    if not all((site_url, bypass_token, ingest_token)):
        print("Friend-site publish skipped: required secrets are not configured yet.")
        return 0

    source = json.loads(Path("reports/all_analysis.json").read_text(encoding="utf-8"))
    rows = source.get("data") if isinstance(source, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("reports/all_analysis.json has no publishable rows")
    stocks = [sanitize(row) for row in rows if isinstance(row, dict)]
    try:
        accuracy = json.loads(Path("reports/accuracy.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        accuracy = {}
    payload = json.dumps({
        "stocks": stocks,
        "accuracy": sanitize_accuracy(accuracy),
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
