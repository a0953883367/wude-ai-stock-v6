from pathlib import Path
import re

WORKFLOW = Path(".github/workflows/stock-briefing.yml")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_only_three_fixed_report_schedules_are_enabled():
    crons = re.findall(
        r'^\\s*-\\s+cron:\\s+"([^"]+)"',
        _workflow_text(),
        flags=re.MULTILINE,
    )
    assert crons == [
        "7 22 * * *",
        "7 4 * * *",
        "7 12 * * *",
    ]


def test_scheduled_period_router_matches_all_three_crons():
    text = _workflow_text()
    assert '"7 22 * * *") period="morning"' in text
    assert '"7 4 * * *") period="noon"' in text
    assert '"7 12 * * *") period="evening"' in text
    assert "靜默盤中更新" not in text
    assert "靜默美股盤中更新" not in text


def test_code_push_cannot_cancel_formal_reports():
    text = _workflow_text()
    assert (
        "group: stock-briefing-${{ github.event_name }}-"
        "${{ github.event.schedule || github.run_id }}"
    ) in text
    assert "cancel-in-progress: false" in text
