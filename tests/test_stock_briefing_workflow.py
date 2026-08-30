from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/stock-briefing.yml"


def test_stock_briefing_keeps_reports_and_adds_silent_taiwan_close_settlement():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert re.findall(r'- cron: "([^"]+)"', text) == [
        "0 22 * * *",
        "0 4 * * *",
        "0 9 * * 1-5",
        "0 12 * * *",
    ]
    assert "  push:" not in text
    assert '"0 9 * * 1-5")' in text
    assert 'period="evening"' in text
    assert 'no_telegram="true"' in text
    assert "更新台股17:00收盤結算" in text
    assert "args+=(--no-telegram)" in text


def test_us_close_settlement_schedule_is_unchanged():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"0 22 * * *") period="morning"' in text
    assert text.count('"0 22 * * *"') == 2


def test_official_report_cannot_be_cancelled_by_a_later_request():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "group: stock-briefing" in text
    assert "cancel-in-progress: false" in text
    assert "--intraday" not in text
    assert "程式更新後安全刷新" not in text
