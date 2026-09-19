from context_efficiency import build_efficiency_report


def test_context_efficiency_has_no_raw_payloads():
    report = build_efficiency_report()
    assert report["status"] == "passed"
    assert report["exact_provider_tokens"] is False
    assert report["privacy"]["raw_messages_stored"] is False
    assert len(report["scenarios"]) == 4
    assert all(row["after"]["bytes"] <= row["before"]["bytes"] for row in report["scenarios"])
