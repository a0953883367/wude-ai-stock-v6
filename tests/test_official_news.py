from datetime import datetime, timezone
from unittest.mock import patch

from news_risk import merge_official_announcements


class _FixedAnnouncementClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
        return value if tz else value.replace(tzinfo=None)


def test_neutral_mops_announcement_does_not_create_penalty():
    result = merge_official_announcements(
        {"news_penalty": 0, "news_data_available": False},
        [{"date": "2026-08-21", "title": "公告董事會決議事項"}],
        symbol="2330.TW", name="台灣公司",
    )
    assert result["news_penalty"] == 0
    assert result["official_announcement_count"] == 1
    assert result["news_data_available"] is True


def test_explicit_negative_mops_announcement_is_official_risk():
    # Freeze time so the existing 3/7/14-day decay policy does not make this
    # test change its expected value as the real calendar advances.
    with patch("news_risk.datetime", _FixedAnnouncementClock):
        result = merge_official_announcements(
            {"news_penalty": 0, "news_data_available": False},
            [{"date": "2026-08-21", "title": "公告下修財測並暫停交易"}],
            symbol="2330.TW", name="台灣公司",
        )
    assert result["news_penalty"] == 12
    assert result["news_verified"] is True
