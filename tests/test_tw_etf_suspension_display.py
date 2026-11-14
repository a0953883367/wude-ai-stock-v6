import ast
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

import notifier
from report_research import finalize_research_markdown
from tw_etf_suspension_display import EVENT, SCHEMA, project_report_quotes, quote_display_for

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/suspended_00662_20261114.json").read_text())


def report():
    row = deepcopy(FIXTURE["row"])
    return {"updated_at": FIXTURE["updated_at"], "period": "morning", "market": {},
            "watchlist": [row], "top": [row], "watchlist_count": 1,
            "watchlist_analyzed_count": 1, "universe_count": 1}


@pytest.mark.parametrize("stamp,status", [
    ("2026-11-10 23:59:59", None),
    ("2026-11-11 00:00:00", "official_suspension"),
    ("2026-11-16 23:59:59", "official_suspension"),
    ("2026-11-17 00:00:00", "resumption_unconfirmed"),
    ("2026-12-01 15:00:00", "resumption_unconfirmed"),
    ("2026-11-10T16:00:00Z", "official_suspension"),
    ("2026-11-16T16:00:00Z", "resumption_unconfirmed"),
    (None, "report_time_unverified"),
    ("invalid", "report_time_unverified"),
])
def test_taipei_boundaries_do_not_confirm_planned_resumption(stamp, status):
    result = quote_display_for(FIXTURE["row"], stamp)
    assert (result or {}).get("status") == status
    if result:
        assert result["event"]["resumption_confirmed"] is False


def test_real_flat_zero_volume_fixture_is_not_relabelled_or_backdated():
    original = deepcopy(FIXTURE["row"])
    display = quote_display_for(original, FIXTURE["updated_at"])
    assert original == FIXTURE["row"]
    assert display["source_reference"] == {
        "price": 117.8, "reported_session_date": "2026-11-12", "verified_as_trade": False}
    assert display["event"]["last_scheduled_trading_date"] == "2026-11-10"
    assert display["price"] is None and display["session_date"] is None
    assert display["change_pct"] is None and display["volume"] is None
    assert display["official_close_available"] is False
    assert "23.56" not in json.dumps(display)


@pytest.mark.parametrize("updates", [
    {}, {"official_volume": 1000},
    {"official_session_date": "2026-11-17", "official_volume": 1000,
     "source_daily_ohlcv_complete": True, "tw_official_price_available": True,
     "tw_price_source": "TWSE OpenAPI", "price": 23.56},
    {"official_session_date": "2026-11-18", "official_volume": 1000},
    {"official_stock_splits": [{"date": "2026-11-17", "ratio": 5}]},
])
def test_unverified_provider_fields_cannot_release_resumption(updates):
    row = {**FIXTURE["row"], **updates}
    display = quote_display_for(row, "2026-11-17 20:00:00")
    assert display["status"] == "resumption_unconfirmed"
    assert display["price"] is None


@pytest.mark.parametrize("updates", [
    {"symbol": "0050.TW"}, {"symbol": "00662.TWO"},
    {"market": "US"}, {"type": "個股"}, {"symbol": "662.TW"},
])
def test_exact_identity_only(updates):
    assert quote_display_for({**FIXTURE["row"], **updates}, FIXTURE["updated_at"]) is None


def test_copy_projection_preserves_aliases_models_ranks_and_nested_inputs():
    payload = report()
    payload["data"] = payload["watchlist"]
    before = deepcopy(payload)
    projected = project_report_quotes(payload)
    assert payload == before and "quote_display" not in payload["watchlist"][0]
    assert projected["watchlist"][0]["quote_display"]["schema"] == SCHEMA
    assert project_report_quotes(projected) == projected
    for key in ("data", "watchlist", "top"):
        row = dict(projected[key][0]); row.pop("quote_display")
        assert row == before[key][0]
    projected["watchlist"][0]["layered_score_weights"]["individual"] = -1
    assert payload == before
    projected["watchlist"][0]["quote_display"]["event"]["split_ratio"] = -1
    assert EVENT["split_ratio"] == 5


def test_stale_display_annotation_is_recomputed_for_historical_report():
    payload = project_report_quotes(report())
    payload["updated_at"] = "2026-11-10 20:00:00"
    assert "quote_display" not in project_report_quotes(payload)["watchlist"][0]


def test_live_pinned_full_report_preserves_every_original_field():
    payload = json.loads((ROOT / "reports/latest.json").read_text())
    before = deepcopy(payload)
    projected = project_report_quotes(payload)
    for key in ("watchlist", "top"):
        for old, new in zip(payload.get(key, []), projected.get(key, [])):
            stripped = dict(new); stripped.pop("quote_display", None)
            assert stripped == old
    assert payload == before


def test_initial_and_final_research_markdown_keep_suspension_warning(tmp_path):
    payload = report(); original = deepcopy(payload)
    text = notifier.render_markdown(payload)
    assert "分割停牌中" in text and "117.80" not in text and "+0.00%" not in text
    assert "2026-11-12 僅供稽核" in text
    assert "買進區" not in text
    latest = tmp_path / "latest.md"; latest.write_text(text)
    full = {"updated_at": payload["updated_at"], "data": [
        {**payload["watchlist"][0], "research": {"horizons": []}}],
        "research_integration": {"status": "complete"}}
    final = finalize_research_markdown(payload, full, latest, text)
    assert "分割停牌中" in final and "117.80" not in final
    assert latest.read_text() == final and payload == original


def test_save_report_projects_json_without_mutating_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(notifier, "SETTINGS", replace(notifier.SETTINGS, reports_dir=tmp_path))
    payload = report(); before = deepcopy(payload)
    latest, _ = notifier.save_report(payload, notifier.render_markdown(payload))
    assert payload == before
    for path in (latest, tmp_path / "morning.json", tmp_path / "archive/2026-11-14-morning.json"):
        saved = json.loads(path.read_text())
        assert saved["watchlist"][0]["quote_display"]["suppress_current_quote"] is True
        assert saved["watchlist"][0]["official_session_date"] == "2026-11-12"


def test_briefing_projection_only_wraps_publication_serialization():
    tree = ast.parse((ROOT / "briefing.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "project_report_quotes"]
    assert sorted(ast.unparse(node.args[0]) for node in calls) == ["all_analysis_payload", "ranking_payload"]
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    assert all(isinstance(parents[node], ast.Call) and ast.unparse(parents[node].func) == "json.dumps" for node in calls)
