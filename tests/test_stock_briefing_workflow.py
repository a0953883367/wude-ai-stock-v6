from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/stock-briefing.yml"


def test_stock_briefing_keeps_reports_and_adds_silent_taiwan_close_settlement():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert re.findall(r'- cron: "([^"]+)"', text) == [
        "45 21 * * *",
        "45 3 * * *",
        "15 9 * * 1-5",
        "30 11 * * *",
    ]
    assert "  push:" not in text
    assert '"15 9 * * 1-5")' in text
    assert 'period="evening"' in text
    assert 'no_telegram="true"' in text
    assert "更新台股17:00收盤結算" in text
    assert "args+=(--no-telegram)" in text


def test_fixed_reports_are_prepared_early_and_delivered_only_after_verification():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"30 11 * * *")' in text
    assert 'defer_delivery="true"' in text
    assert 'delivery_target="06:00"' in text
    assert 'delivery_target="12:00"' in text
    assert 'delivery_target="20:00"' in text
    assert "Classify fixed-report delivery window" in text
    assert 'echo "timely=false" >> "$GITHUB_OUTPUT"' in text
    assert "python report_schedule.py" in text
    assert "--period \"${{ steps.period.outputs.value }}\"" in text
    assert "steps.verified_delivery.outcome != 'success'" in text
    assert "timeout-minutes: 60" in text
    assert "Prevent duplicate or expired fixed report" in text
    assert "--mode gate" in text
    assert "inputs.recovery" in text


def test_system_guard_can_dispatch_one_protected_recovery_run():
    text = (WORKFLOW.parent / "system-guard.yml").read_text(encoding="utf-8")

    assert "actions: write" in text
    assert "briefing_watchdog.py" in text
    assert "should_dispatch == 'true'" in text
    assert "gh run list --workflow stock-briefing.yml" in text
    assert "gh workflow run stock-briefing.yml" in text
    assert "-f recovery=true" in text


def test_stale_fixed_report_still_advances_private_ledgers_without_publication():
    text = WORKFLOW.read_text(encoding="utf-8")

    classify = text.index("Classify fixed-report delivery window")
    generate = text.index("name: Generate report")
    save_private = text.index("name: Save private AI prediction database")
    confirm_private = text.index("name: Confirm private settlement for stale fixed report")

    assert classify < generate < save_private < confirm_private
    assert "continuing as private settlement only" in text
    assert "steps.delivery_window.outputs.timely == 'true'" in text
    assert "steps.delivery_window.outputs.timely != 'true'" in text
    assert "Stale public delivery suppressed" in text


def test_delayed_noon_run_becomes_silent_close_settlement():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'taiwan_hour="$(TZ=Asia/Taipei date +%H)"' in text
    assert 'taiwan_weekday="$(TZ=Asia/Taipei date +%u)"' in text
    assert '[ "$taiwan_weekday" -le 5 ] && [ "$taiwan_hour" -ge 16 ]' in text
    assert 'commit_message="補跑延遲的台股收盤結算"' in text
    assert 'echo "save_prediction=$save_prediction"' in text
    assert "steps.period.outputs.save_prediction == 'true'" in text


def test_us_close_settlement_schedule_is_unchanged():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"45 21 * * *")' in text
    assert 'period="morning"' in text
    assert text.count('"45 21 * * *"') == 2


def test_official_report_cannot_be_cancelled_by_a_later_request():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "group: stock-briefing" in text
    assert "cancel-in-progress: false" in text
    assert "--intraday" not in text
    assert "程式更新後安全刷新" not in text


def test_history_archive_phase2_only_removes_verified_duplicate_source() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "python tools/compact_history_archive.py" in text
    assert "--older-than-days 30" in text
    assert "--compress" in text
    assert "--remove-source-after-verify" in text
    assert "-delete" not in text
