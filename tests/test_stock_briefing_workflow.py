from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/stock-briefing.yml"


def test_stock_briefing_keeps_reports_and_adds_silent_taiwan_close_settlement():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert re.findall(r'- cron: "([^"]+)"', text) == [
        "35 21 * * *",
        "45 21 * * *",
        "55 21 * * *",
        "35 3 * * *",
        "45 3 * * *",
        "55 3 * * *",
        "15 9 * * 1-5",
        "10 11 * * *",
        "30 11 * * *",
        "50 11 * * *",
    ]
    assert "  push:" not in text
    assert '"15 9 * * 1-5")' in text
    assert 'period="evening"' in text
    assert 'no_telegram="true"' in text
    assert "更新台股17:00收盤結算" in text
    assert "args+=(--no-telegram)" in text


def test_fixed_reports_are_prepared_early_and_delivered_only_after_verification():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"30 11 * * *"' in text
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
    assert 'branches: [main]' in text
    assert '"briefing_watchdog.py"' in text
    assert "briefing_watchdog.py" in text
    assert "python agent_stock_recovery.py --execute-reserved" in text
    assert text.index("Publish reservation before any external action") < text.index("--execute-reserved")
    executor = Path("agent_stock_recovery.py").read_text(encoding="utf-8")
    assert "stock-briefing.yml/dispatches" in executor
    assert "'recovery': 'true'" in executor
    assert "delivery_is_current" in executor
    assert "RETRY_LIMIT = 2" in executor
    assert re.findall(r'- cron: "([^"]+)"', text) == [
        "23 * * * *", "10,25,40,55 4,12,22 * * *",
    ]


def test_stale_fixed_report_publishes_data_but_keeps_delivery_suppressed():
    text = WORKFLOW.read_text(encoding="utf-8")

    classify = text.index("Classify fixed-report delivery window")
    generate = text.index("name: Generate report")
    save_private = text.index("name: Save private AI prediction database")
    confirm_private = text.index("name: Confirm private settlement for stale fixed report")

    assert classify < generate < save_private < confirm_private
    assert "continuing as silent current-data settlement" in text
    assert "steps.delivery_window.outputs.timely == 'true'" in text
    assert "steps.delivery_window.outputs.timely != 'true'" in text
    assert "Stale report delivery suppressed" in text


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


def test_report_publish_rebases_and_retries_when_another_guard_pushes():
    text = WORKFLOW.read_text(encoding="utf-8")
    guard = (WORKFLOW.parent / "system-guard.yml").read_text(encoding="utf-8")

    for workflow in (text, guard):
        assert "for attempt in 1 2 3" in workflow
        assert "git fetch origin main" in workflow
        assert "git rebase" in workflow and "origin/main" in workflow
        assert "git push origin HEAD:main" in workflow
    # The persisted recovery reservation must never be silently overwritten by
    # a conflict-resolution preference: fail closed before external dispatch.
    assert "git rebase origin/main" in guard
    assert "git rebase -X theirs" not in guard


def test_history_archive_phase2_only_removes_verified_duplicate_source() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "python tools/compact_history_archive.py" in text
    assert "--older-than-days 30" in text
    assert "--compress" in text
    assert "--remove-source-after-verify" in text
    assert "-delete" not in text


def test_agent_state_writers_share_non_cancelling_lock():
    import re
    workflows = [WORKFLOW.parent / name for name in ("system-guard.yml", "agent-control.yml")]
    groups = []
    for path in workflows:
        workflow = path.read_text(encoding="utf-8")
        assert "reports/agent_runtime.json" in workflow
        groups.append(re.search(r"^  group: (.+)$", workflow, re.MULTILINE).group(1))
        assert "cancel-in-progress: false" in workflow
    assert groups[0] == groups[1] == "stock-agent-state-${{ github.ref }}"
    assert "group: stock-briefing" in WORKFLOW.read_text(encoding="utf-8")


def test_verified_receipts_are_published_before_optional_ownership_refresh():
    from pathlib import Path
    text = Path('.github/workflows/stock-briefing.yml').read_text()
    assert text.index('name: Keep reports and recent archive') < text.index('name: Refresh read-only Fubon ownership supplement')
    assert text.index('name: Refresh read-only Fubon ownership supplement') < text.index('name: Publish optional ownership supplement')
    supplement = text.split('name: Publish optional ownership supplement')[1].split('name: Confirm private settlement')[0]
    assert 'git add reports/fubon_ownership.json' in supplement


def test_delivery_expiry_cannot_block_successful_data_publication():
    text = WORKFLOW.read_text()
    def condition_for(name):
        block = text.split("      - name: " + name + "\n", 1)[1].split("      - ", 1)[0]
        return block.split("        if:", 1)[1].split("        env:", 1)[0].split("        run:", 1)[0]
    for name in ('Publish sanitized friend-site data', 'Publish complete owner-site data',
                 'Keep reports and recent archive', 'Run independent system guard',
                 'Refresh read-only Fubon ownership supplement'):
        condition = condition_for(name)
        assert "steps.generate.outcome == 'success'" in condition
        assert 'delivery_window' not in condition and 'defer_delivery' not in condition
    for name in ('Wait for official fixed-report delivery window', 'Deliver verified fixed report'):
        assert "steps.delivery_window.outputs.timely == 'true'" in condition_for(name)
        assert "steps.period.outputs.defer_delivery == 'true'" in condition_for(name)


def test_silent_refresh_selection_never_enables_delivery(tmp_path):
    import subprocess
    import textwrap
    block = WORKFLOW.read_text().split('      - name: Select report period and delivery mode', 1)[1].split('      - name:', 1)[0]
    script = textwrap.dedent(block.split('        run: |\n', 1)[1])
    replacements = {'github.event_name': 'workflow_dispatch', 'inputs.period': 'noon',
                    'inputs.data_refresh': 'true', 'inputs.recovery': 'true', 'github.event.schedule': ''}
    for key, value in replacements.items():
        script = script.replace('${{ ' + key + ' }}', value)
    output = tmp_path / 'outputs'
    result = subprocess.run(['bash', '-e', '-c', script], env={'PATH': '/usr/bin:/bin', 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert values['no_telegram'] == 'true'
    assert values['defer_delivery'] == 'false'
    assert values['delivery_target'] == ''
    assert values['save_prediction'] == 'true'
