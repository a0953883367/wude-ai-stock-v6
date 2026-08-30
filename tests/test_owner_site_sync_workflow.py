from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/owner-site-sync.yml"


def test_owner_sync_republishes_existing_snapshot_without_price_fetch() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "python tools/publish_owner_data.py" in text
    assert "briefing.py" not in text
    assert "data_fetcher" not in text
    assert "group: stock-briefing" in text
    assert "cancel-in-progress: false" in text
    assert "--owner-publish success" in text
    assert "OWNER_SITE_BYPASS_TOKEN" in text
