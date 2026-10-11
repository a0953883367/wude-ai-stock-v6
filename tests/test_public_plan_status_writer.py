"""Offline generation/consumer tests; no provider requests or real task inputs."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

import public_plan_status_writer as writer
import public_research_briefing_contract as contract
import watchlist_manifest
from tools import publish_shadow_report_batch as publisher


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network_forbidden")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_writer_uses_canonical_only_and_preserves_other_artifacts(tmp_path):
    original = {"plans": [], "private_prompt": "NEVER_EXPORT_98765.4321"}
    frozen = deepcopy(original)
    protected = ["tw_official_cache.json", "tw_prospective_registry.json", "trade_plan_shadow.json"]
    for filename in protected:
        (tmp_path / filename).write_text("immutable original")
    target = writer.write_public_plan_status(tmp_path, original)
    value = json.loads(target.read_text())
    assert value["scope"] == "canonical_watchlist_only"
    assert value["contract_kind"] == "plan_eligibility_status_only"
    assert value["quote_cache_replacement"] is False
    assert value["counts"]["output_rows"] == len(watchlist_manifest.build_manifest()["entries"])
    assert value["counts"]["eligible_actions"] == value["counts"]["numeric_release_rows"] == 0
    assert "NEVER_EXPORT" not in target.read_text()
    assert "private_prompt" not in target.read_text()
    assert value["provenance"]["artifact_sha256"] == contract._digest(original)
    assert original == frozen
    assert not (tmp_path / "public_plan_status.tmp").exists()
    assert all((tmp_path / name).read_text() == "immutable original" for name in protected)


def test_generation_error_redacts_upstream_exception_and_removes_stale_file(tmp_path, monkeypatch):
    (tmp_path / writer.STATUS_FILE).write_text("stale prior batch")
    def fail():
        raise ValueError("PRIVATE_PRICE_98765.4321")
    monkeypatch.setattr(watchlist_manifest, "build_manifest", fail)
    with pytest.raises(RuntimeError, match="^public_plan_status_generation_failed$") as exc:
        writer.write_public_plan_status(tmp_path, {})
    assert exc.value.__suppress_context__ is True
    assert not (tmp_path / writer.STATUS_FILE).exists()
    assert not (tmp_path / "public_plan_status.tmp").exists()


def test_atomic_replace_failure_cannot_leave_publishable_stale_file(tmp_path, monkeypatch):
    (tmp_path / writer.STATUS_FILE).write_text("stale")
    def fail(*args):
        raise OSError("PRIVATE_123456.789")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(RuntimeError, match="^public_plan_status_generation_failed$"):
        writer.write_public_plan_status(tmp_path, {})
    assert not (tmp_path / writer.STATUS_FILE).exists()
    assert not (tmp_path / "public_plan_status.tmp").exists()


def test_existing_trade_plan_writer_generates_status_from_identical_batch(tmp_path, monkeypatch):
    import trade_plan_shadow
    report = {"status": "ready", "summary": {"total": 0}, "plans": [],
              "updated_at": "2025-01-02 16:00:00", "secret_text": "DO_NOT_EXPORT_12345.678"}
    monkeypatch.setattr(trade_plan_shadow, "build_trade_plan_report", lambda *a, **kw: report)
    def forbidden(*args):
        raise AssertionError("registry must not advance")
    monkeypatch.setitem(sys.modules, "tw_prospective_runtime", SimpleNamespace(
        update_tw_prospective_report=forbidden, attach_registry_summary=forbidden,
        REGISTRY_FILE="tw_prospective_registry.json"))
    target = trade_plan_shadow.write_trade_plan_report(tmp_path, update_registry=False)
    status = json.loads((tmp_path / writer.STATUS_FILE).read_text())
    assert status["provenance"]["artifact_sha256"] == contract._digest(json.loads(target.read_text()))
    assert "DO_NOT_EXPORT" not in (tmp_path / writer.STATUS_FILE).read_text()
    assert not (tmp_path / "tw_prospective_registry.json").exists()


def test_public_status_is_required_allowlisted_and_private_union_is_not():
    assert "reports/public_plan_status.json" in publisher.REPORT_FILES
    assert not any("union" in p or "supplement" in p for p in publisher.REPORT_FILES)
    assert "tests/test_public_plan_status_writer.py" in publisher.FOCUSED_TESTS
    assert "tests/test_public_research_briefing_contract.py" in publisher.FOCUSED_TESTS
