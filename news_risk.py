"""Recent negative-news risk radar with conservative score penalties."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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
    "data breach", "guidance cut", "cuts guidance", "profit warning", "重大訊息",
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


def fetch_news_risks(rows: list[dict[str, Any]], workers: int = 8) -> dict[str, dict[str, Any]]:
    """Scan a bounded candidate set concurrently."""
    unique = {str(row.get("symbol")): row for row in rows if row.get("symbol")}
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(unique)))) as pool:
        futures = [pool.submit(_fetch_symbol, row) for row in unique.values()]
        for future in as_completed(futures):
            symbol, result = future.result()
            results[symbol] = result
    return results
