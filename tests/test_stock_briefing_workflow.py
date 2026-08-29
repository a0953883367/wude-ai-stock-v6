from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/stock-briefing.yml"


def test_stock_briefing_runs_only_at_fixed_taiwan_report_times():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert re.findall(r'- cron: "([^"]+)"', text) == [
        "0 22 * * *",
        "0 4 * * *",
        "0 12 * * *",
    ]
    assert "  push:" not in text


def test_official_report_cannot_be_cancelled_by_a_later_request():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "group: stock-briefing" in text
    assert "cancel-in-progress: false" in text
    assert "--intraday" not in text
    assert "程式更新後安全刷新" not in text
