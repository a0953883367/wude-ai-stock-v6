from evidence_contract import build_unified_evidence_report, make_evidence, validate_evidence


def test_canonical_evidence_has_identity_and_integrity():
    item = make_evidence(
        source_id="formal_v6", source_label="正式排名", horizon="short",
        direction="support", strength=72, confidence=80, as_of="2026-08-29",
        status="locked", reason="只讀", affects_decision=True,
        symbol="2330.TW", market="TW", provenance="reports/all_analysis.json",
    )
    assert not validate_evidence(item)
    report = build_unified_evidence_report([{"evidence": [item]}], updated_at="now")
    assert report["status"] == "ready"
    assert report["evidence_count"] == 1
    assert len(report["integrity_sha256"]) == 64


def test_invalid_direction_is_explicitly_normalized_to_missing():
    item = make_evidence(
        source_id="x", source_label="X", horizon="unknown", direction="bullish",
        strength=999, confidence=-1, as_of=None, status="missing", reason="無",
        affects_decision=False,
    )
    assert item["direction"] == "missing"
    assert item["horizon"] == "all"
    assert item["strength"] == 100
    assert item["confidence"] == 0


def test_same_signal_from_linked_pages_is_counted_once():
    low = make_evidence(
        source_id="capital_flow_shadow", source_label="大量買賣", horizon="short",
        direction="support", strength=60, confidence=60, as_of="2026-08-29",
        status="linked", reason="頁面摘要", affects_decision=False,
        symbol="2330.TW", market="TW", provenance="capital_flow_page",
    )
    high = make_evidence(
        source_id="capital_flow_shadow", source_label="大量買賣", horizon="short",
        direction="support", strength=75, confidence=75, as_of="2026-08-29",
        status="validated", reason="完整收盤證據", affects_decision=True,
        symbol="2330.TW", market="TW", provenance="decision_hub_adapter",
    )
    report = build_unified_evidence_report(
        [{"evidence": [low]}, {"evidence": [high]}], updated_at="now"
    )
    assert report["input_evidence_count"] == 2
    assert report["evidence_count"] == 1
    assert report["deduplicated_count"] == 1
    assert report["evidence"][0]["confidence"] == 75
    assert report["evidence"][0]["affects_decision"] is True
