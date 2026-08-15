from datetime import datetime, timedelta, timezone

from news_risk import classify_news


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
            article("Demand weakens and orders are cut", "Reuters"),
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
