from datetime import datetime, timedelta, timezone

import news_risk
from news_risk import classify_news, fetch_news_risks


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def article(title, publisher, days=1):
    return {
        "title": title,
        "publisher": publisher,
        "providerPublishTime": int((NOW - timedelta(days=days)).timestamp()),
        "link": "https://example.com/news",
    }


def test_official_severe_event_reduces_score():
    result = classify_news(
        [article("Company cuts guidance after major recall", "Business Wire")], NOW
    )
    assert result["news_risk_level"].startswith("🔴")
    assert result["news_verified"] is True
    assert result["news_penalty"] == 12.0


def test_two_independent_trusted_sources_are_cross_verified():
    result = classify_news(
        [
            article("Weak demand leads to order cut", "Reuters"),
            article("Analyst downgrade follows weak demand", "CNBC"),
        ],
        NOW,
    )
    assert result["news_risk_level"].startswith("🟡")
    assert result["news_penalty"] == 5.0


def test_single_untrusted_rumor_never_reduces_score():
    result = classify_news(
        [article("Rumor claims weak demand and an order cut", "Unknown Blog")], NOW
    )
    assert result["news_risk_level"].startswith("⚪")
    assert result["news_penalty"] == 0.0


def test_old_articles_expire_after_fourteen_days():
    result = classify_news(
        [article("Company cuts guidance", "Reuters", days=20)], NOW
    )
    assert result["news_risk_level"].startswith("🟢")
    assert result["news_penalty"] == 0.0


def test_one_trusted_report_is_display_only():
    result = classify_news(
        [article("Analyst downgrade after margin pressure", "Reuters")], NOW
    )
    assert result["news_risk_level"].startswith("🟡")
    assert result["news_verified"] is False
    assert result["news_penalty"] == 0.0


def test_unrelated_negative_article_is_filtered_by_stock_identity():
    result = classify_news(
        [article(
            "Social Security's 2027 COLA Just Got Downgraded: What Retirees Need to Know",
            "Motley Fool",
        )],
        NOW,
        identity_terms={"8222", "寶一"},
    )
    assert result["news_articles"] == []
    assert result["news_penalty"] == 0.0
    assert result["news_risk_level"].startswith("🟢")


def test_relevant_negative_article_is_kept_by_stock_identity():
    result = classify_news(
        [article("Amazon faces analyst downgrade after margin pressure", "Reuters")],
        NOW,
        identity_terms={"amzn", "amazon"},
    )
    assert len(result["news_articles"]) == 1
    assert result["news_risk_level"].startswith("🟡")


def test_full_universe_news_cache_skips_fresh_non_priority_rows(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(row):
        calls.append(row["symbol"])
        return row["symbol"], {
            "news_risk_level": "🟢 測試",
            "news_penalty": 0.0,
            "news_verified": False,
            "news_summary": "完整掃描",
            "news_articles": [],
            "news_data_available": True,
            "news_scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr(news_risk, "_fetch_symbol", fake_fetch)
    path = tmp_path / "news.json"
    rows = [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}]
    first = fetch_news_risks(rows, cache_path=path)
    second = fetch_news_risks(rows, cache_path=path, priority_symbols={"B"})
    assert set(first) == {"A", "B", "C"}
    assert set(second) == {"A", "B", "C"}
    assert calls == ["A", "B", "C", "B"]


def test_recent_verified_cache_is_used_on_provider_failure(tmp_path, monkeypatch):
    path = tmp_path / "news.json"
    good = {
        "news_risk_level": "🟢 測試", "news_penalty": 0.0,
        "news_verified": False, "news_summary": "完整掃描",
        "news_articles": [], "news_data_available": True,
        "news_scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(
        __import__("json").dumps({"symbols": {"A": good}}), encoding="utf-8"
    )

    def fail(_row):
        return "A", {
            "news_data_available": False, "news_penalty": 0.0,
            "news_scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr(news_risk, "_fetch_symbol", fail)
    result = fetch_news_risks(
        [{"symbol": "A"}], cache_path=path, priority_symbols={"A"}
    )["A"]
    assert result["news_data_available"] is True
    assert result["news_cache_stale"] is True


def test_existing_verified_rows_seed_cache_without_network(tmp_path, monkeypatch):
    def unexpected(_row):
        raise AssertionError("seed-only update must not call provider")

    monkeypatch.setattr(news_risk, "_fetch_symbol", unexpected)
    path = tmp_path / "news.json"
    result = fetch_news_risks([{
        "symbol": "A", "news_data_available": True,
        "news_risk_level": "🟢 已掃描", "news_penalty": 0,
        "news_verified": False, "news_summary": "既有驗證結果",
        "news_articles": [],
        "news_scanned_at": datetime.now(timezone.utc).isoformat(),
    }], cache_path=path, max_background_refresh=0)
    assert result["A"]["news_data_available"] is True
    assert path.exists()
