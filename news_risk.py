"""Recent negative-news risk radar with conservative score penalties."""

from __future__ import annotations

import logging
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

LOG = logging.getLogger(__name__)
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WudeStockRadar/6.27)"}

TRUSTED_PUBLISHERS = (
    "reuters", "associated press", "ap news", "bloomberg", "wall street journal",
    "financial times", "cnbc", "nikkei", "barron", "marketwatch", "business wire",
    "globenewswire", "pr newswire", "u.s. securities and exchange commission", "sec filing", "nasdaq", "nyse", "公司公告",
    "公開資訊觀測站", "證券交易所", "櫃買中心", "中央社",
)
OFFICIAL_PUBLISHERS = (
    "u.s. securities and exchange commission", "sec filing", "nasdaq", "nyse", "business wire", "globenewswire", "pr newswire",
    "公司公告", "公開資訊觀測站", "證券交易所", "櫃買中心",
)
SEVERE_TERMS = (
    "bankruptcy", "chapter 11", "default", "fraud", "accounting irregular",
    "restatement", "subpoena", "criminal investigation", "regulatory investigation",
    "delisting", "trading halt", "suspension", "recall", "cyber breach",
    "data breach", "guidance cut", "cuts guidance", "profit warning",
    "破產", "違約", "財報不實", "重編財報", "搜索", "起訴", "調查",
    "下市", "停止交易", "暫停交易", "召回", "資安事件", "下修財測",
)
NEGATIVE_TERMS = (
    "downgrade", "price target cut", "layoff", "job cuts", "weak demand",
    "order cut", "order cancellation", "misses estimates", "earnings miss",
    "revenue decline", "margin pressure", "customer loss", "lawsuit", "penalty",
    "antitrust", "short seller", "下調評等", "裁員", "需求疲弱", "砍單",
    "取消訂單", "獲利衰退", "營收衰退", "毛利率下滑", "客戶流失",
    "訴訟", "罰款", "反壟斷", "放空報告",
)

GENERIC_IDENTITY_TERMS = {
    "inc", "corp", "corporation", "company", "co", "ltd", "limited",
    "holdings", "group", "plc", "the", "etf",
}


def _identity_terms(symbol: str, name: str) -> set[str]:
    """Return conservative ticker/name terms used to reject unrelated news."""
    raw_symbol = re.sub(r"\.(TW|TWO)$", "", symbol.upper()).casefold()
    normalized_name = re.sub(r"[^\w\u4e00-\u9fff]+", " ", name.casefold()).strip()
    candidates = {raw_symbol}
    if normalized_name:
        candidates.add(normalized_name)
        candidates.update(normalized_name.split())
    return {
        term for term in candidates
        if len(term) >= 2 and term not in GENERIC_IDENTITY_TERMS
    }


def _article_matches_identity(title: str, identity_terms: set[str] | None) -> bool:
    if not identity_terms:
        return True
    normalized = re.sub(r"\s+", " ", title.casefold())
    return any(term in normalized for term in identity_terms)


def _publisher_flags(publisher: str) -> tuple[bool, bool]:
    value = publisher.casefold()
    return (
        any(name in value for name in TRUSTED_PUBLISHERS),
        any(name in value for name in OFFICIAL_PUBLISHERS),
    )


def _article_age_days(article: dict[str, Any], now: datetime) -> float:
    try:
        published = datetime.fromtimestamp(float(article.get("providerPublishTime")), tz=timezone.utc)
        return max(0.0, (now - published).total_seconds() / 86400)
    except (TypeError, ValueError, OSError):
        return 999.0


def classify_news(
    articles: list[dict[str, Any]],
    now: datetime | None = None,
    identity_terms: set[str] | None = None,
) -> dict[str, Any]:
    """Classify recent articles; rumors and one-source claims never reduce scores."""
    now = now or datetime.now(timezone.utc)
    recent: list[dict[str, Any]] = []
    for article in articles:
        age = _article_age_days(article, now)
        if age > 14:
            continue
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        if not _article_matches_identity(title, identity_terms):
            continue
        publisher = str(article.get("publisher") or article.get("provider") or "來源未標示")
        text = title.casefold()
        severity = "severe" if any(term in text for term in SEVERE_TERMS) else (
            "negative" if any(term in text for term in NEGATIVE_TERMS) else "neutral"
        )
        if severity == "neutral":
            continue
        trusted, official = _publisher_flags(publisher)
        published_at = datetime.fromtimestamp(
            float(article.get("providerPublishTime") or 0), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        recent.append({
            "title": title,
            "publisher": publisher,
            "published_at": published_at,
            "url": str(article.get("link") or article.get("url") or ""),
            "severity": severity,
            "trusted": trusted,
            "official": official,
            "age_days": round(age, 1),
        })

    trusted = [item for item in recent if item["trusted"]]
    official_severe = [item for item in trusted if item["official"] and item["severity"] == "severe"]
    severe_sources = {item["publisher"].casefold() for item in trusted if item["severity"] == "severe"}
    negative_sources = {item["publisher"].casefold() for item in trusted}
    newest_age = min((item["age_days"] for item in trusted), default=999)
    decay = 1.0 if newest_age <= 3 else 0.75 if newest_age <= 7 else 0.4

    if official_severe:
        level, base, verified = "🔴 重大風險", 12.0, True
        summary = "官方或公司公告出現重大負面事件"
    elif len(severe_sources) >= 2:
        level, base, verified = "🔴 重大風險", 8.0, True
        summary = "至少兩個可信來源交叉證實重大負面事件"
    elif len(negative_sources) >= 2:
        level, base, verified = "🟡 注意消息", 5.0, True
        summary = "至少兩個可信來源出現一致負面訊號"
    elif trusted:
        level, base, verified = "🟡 注意消息", 0.0, False
        summary = "目前僅一個可信來源，先觀察、不扣分"
    elif recent:
        level, base, verified = "⚪ 未確認", 0.0, False
        summary = "僅見未確認消息，不列入計分"
    else:
        level, base, verified = "🟢 未發現近期負面消息", 0.0, False
        summary = "近14日掃描未發現符合規則的負面消息"

    penalty = round(base * decay, 1)
    return {
        "news_risk_level": level,
        "news_penalty": penalty,
        "news_verified": verified,
        "news_summary": summary,
        "news_articles": sorted(
            recent, key=lambda item: (not item["trusted"], item["age_days"])
        )[:3],
        "news_data_available": True,
    }


def _fetch_symbol(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    symbol = str(row.get("symbol") or "")
    query = f"{symbol} {row.get('name', '')}".strip()
    try:
        response = requests.get(
            SEARCH_URL,
            params={"q": query, "quotesCount": 0, "newsCount": 8, "enableFuzzyQuery": "false"},
            headers=HEADERS,
            timeout=12,
        )
        response.raise_for_status()
        result = classify_news(
            response.json().get("news") or [],
            identity_terms=_identity_terms(symbol, str(row.get("name") or "")),
        )
    except Exception as exc:  # network/data failure must stay neutral
        LOG.warning("news scan failed for %s: %s", symbol, exc)
        result = {
            "news_risk_level": "⚪ 新聞資料未取得",
            "news_penalty": 0.0,
            "news_verified": False,
            "news_summary": "本次新聞來源未取得，不作負面推定、也不扣分",
            "news_articles": [],
            "news_data_available": False,
        }
    result["news_scanned_at"] = datetime.now(timezone.utc).isoformat()
    return symbol, result


def _read_news_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    rows = payload.get("symbols", {}) if isinstance(payload, dict) else {}
    return rows if isinstance(rows, dict) else {}


def _cache_age_hours(result: dict[str, Any], now: datetime) -> float:
    try:
        scanned = datetime.fromisoformat(str(result.get("news_scanned_at")))
        if scanned.tzinfo is None:
            scanned = scanned.replace(tzinfo=timezone.utc)
        return max(0.0, (now - scanned.astimezone(timezone.utc)).total_seconds() / 3600)
    except (TypeError, ValueError):
        return float("inf")


def _write_news_cache(path: Path | None, rows: dict[str, dict[str, Any]]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": rows,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def fetch_news_risks(
    rows: list[dict[str, Any]],
    workers: int = 8,
    *,
    cache_path: Path | None = None,
    max_age_hours: float = 18.0,
    stale_fallback_hours: float = 48.0,
    priority_symbols: set[str] | None = None,
    max_background_refresh: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Scan the full universe while reusing recent, attributable results.

    Priority symbols are refreshed every run.  The remainder are refreshed
    when their cache expires.  A short-lived stale result may be shown when a
    provider has a transient failure, and is explicitly marked as stale.
    """
    unique = {str(row.get("symbol")): row for row in rows if row.get("symbol")}
    priority = {str(symbol) for symbol in (priority_symbols or set())}
    now = datetime.now(timezone.utc)
    cache = _read_news_cache(cache_path)
    cache_fields = (
        "news_risk_level", "news_penalty", "news_verified", "news_summary",
        "news_articles", "news_data_available", "news_scanned_at",
        "official_announcement_count", "official_announcement_source",
    )
    for symbol, row in unique.items():
        if symbol in cache or not row.get("news_data_available"):
            continue
        seeded = {key: row.get(key) for key in cache_fields if key in row}
        if seeded.get("news_scanned_at"):
            seeded["news_cache_stale"] = False
            cache[symbol] = seeded
    results: dict[str, dict[str, Any]] = {}
    priority_refresh: list[dict[str, Any]] = []
    background_refresh: list[dict[str, Any]] = []
    for symbol, row in unique.items():
        cached = cache.get(symbol)
        if (
            symbol not in priority
            and isinstance(cached, dict)
            and cached.get("news_data_available")
            and _cache_age_hours(cached, now) <= max_age_hours
        ):
            results[symbol] = dict(cached)
        elif symbol in priority:
            priority_refresh.append(row)
        else:
            background_refresh.append(row)

    if max_background_refresh is not None:
        background_refresh = background_refresh[:max(0, max_background_refresh)]
    refresh = priority_refresh + background_refresh

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(refresh)))) as pool:
        futures = [pool.submit(_fetch_symbol, row) for row in refresh]
        for future in as_completed(futures):
            symbol, result = future.result()
            if result.get("news_data_available"):
                result["news_cache_stale"] = False
                cache[symbol] = dict(result)
                results[symbol] = result
                continue
            cached = cache.get(symbol)
            if (
                isinstance(cached, dict)
                and cached.get("news_data_available")
                and _cache_age_hours(cached, now) <= stale_fallback_hours
            ):
                fallback = dict(cached)
                fallback["news_cache_stale"] = True
                fallback["news_summary"] = (
                    f"來源暫時連線失敗；沿用 {_cache_age_hours(cached, now):.1f} 小時前的已驗證掃描"
                )
                results[symbol] = fallback
            else:
                results[symbol] = result
    _write_news_cache(cache_path, cache)
    return results


def merge_official_announcements(
    base: dict[str, Any],
    announcements: list[dict[str, Any]],
    *,
    symbol: str,
    name: str,
) -> dict[str, Any]:
    """Add MOPS announcements without treating every announcement as bad news."""
    articles: list[dict[str, Any]] = []
    for item in announcements:
        try:
            published = datetime.fromisoformat(str(item.get("date"))).replace(
                tzinfo=timezone.utc
            )
        except (TypeError, ValueError):
            continue
        articles.append({
            "title": f"{name} {str(item.get('title') or '').strip()}",
            "publisher": "公開資訊觀測站",
            "providerPublishTime": published.timestamp(),
            "link": "",
        })
    official = classify_news(
        articles,
        identity_terms=_identity_terms(symbol, name),
    )
    output = dict(base)
    output["official_announcement_count"] = len(announcements)
    output["official_announcement_source"] = "MOPS"
    output["news_data_available"] = bool(base.get("news_data_available") or articles)
    if float(official.get("news_penalty") or 0) > float(base.get("news_penalty") or 0):
        output.update(official)
        output["official_announcement_count"] = len(announcements)
        output["official_announcement_source"] = "MOPS"
    return output
