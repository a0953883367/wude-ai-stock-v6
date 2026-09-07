from __future__ import annotations

from pathlib import Path

from corporate_actions_shadow import (
    POLICY,
    build_shadow_report,
    normalize_sec_registry,
    normalize_tw_announcements,
    normalize_tw_registry,
    parse_nasdaq_halts,
)


NOW = "2026-09-07T00:00:00+00:00"


def _universe(*rows: tuple[str, str, str]) -> dict:
    return {
        "data": [
            {"代號": symbol, "股票": name, "市場": market, "類型": "個股"}
            for symbol, name, market in rows
        ]
    }


def _source(name: str, records: list[dict], *, ok: bool = True) -> dict:
    return {"source": name, "ok": ok, "records": records, "error": "" if ok else "timeout"}


def _tw(symbol: str, entity: str, name: str, source: str = "twse_registry") -> dict:
    return {
        "symbol": symbol,
        "market": "TW",
        "exchange": "TWSE" if symbol.endswith(".TW") else "TPEx",
        "name": name,
        "legal_name": name,
        "entity_id": f"TW-BN-{entity}",
        "source": source,
    }


def _previous(*records: dict, missing: dict | None = None) -> dict:
    return {
        "records": {record["symbol"]: record for record in records},
        "missing_observations": missing or {},
    }


def _build(universe: dict, previous: dict, *sources: dict, announcements=None, halts=None, health=None):
    return build_shadow_report(
        universe,
        previous,
        list(sources),
        announcements or [],
        halts or [],
        generated_at=NOW,
        event_source_health=health,
    )


def test_official_registries_use_stable_company_identifiers() -> None:
    tw = normalize_tw_registry([
        {"公司代號": "2330", "公司簡稱": "台積電", "營利事業統一編號": "22099131"}
    ], exchange="TWSE")
    us = normalize_sec_registry({
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]],
    })

    assert tw[0]["entity_id"] == "TW-BN-22099131"
    assert tw[0]["symbol"] == "2330.TW"
    assert us[0]["entity_id"] == "US-CIK-0000320193"
    assert us[0]["symbol"] == "AAPL"


def test_first_run_is_a_locked_shadow_baseline() -> None:
    universe = _universe(("2330.TW", "台積電", "🇹🇼 台灣"))
    report, registry = _build(
        universe, {}, _source("twse_registry", [_tw("2330.TW", "22099131", "台積電")])
    )

    assert report["status"] == "baseline"
    assert report["events"] == []
    assert report["policy"] == POLICY
    assert report["policy"]["updates_active_universe"] is False
    assert report["policy"]["deletes_history"] is False
    assert registry["records"]["2330.TW"]["entity_id"] == "TW-BN-22099131"


def test_same_entity_name_change_only_creates_display_candidate() -> None:
    old = _tw("2330.TW", "22099131", "舊名稱")
    new = _tw("2330.TW", "22099131", "新名稱")
    report, _ = _build(
        _universe(("2330.TW", "舊名稱", "🇹🇼 台灣")),
        _previous(old),
        _source("twse_registry", [new]),
    )

    event = report["events"][0]
    assert event["type"] == "NAME_CHANGE"
    assert event["level"] == "info"
    assert "顯示名稱" in event["action"]
    assert report["policy"]["joins_price_history"] is False


def test_ticker_change_requires_same_entity_and_never_auto_joins_history() -> None:
    old = _tw("1234.TW", "11111111", "範例公司")
    new = _tw("5678.TW", "11111111", "範例公司")
    report, _ = _build(
        _universe(("1234.TW", "範例公司", "🇹🇼 台灣")),
        _previous(old),
        _source("twse_registry", [new]),
    )

    types = {event["type"] for event in report["events"]}
    assert "SYMBOL_CHANGE" in types
    assert report["policy"]["joins_price_history"] is False
    assert report["policy"]["requires_manual_approval"] is True


def test_coretronic_and_aewin_different_entities_are_never_stitched() -> None:
    old = _tw("5371.TWO", "97331723", "中光電")
    other = _tw("3718.TWO", "12345678", "全訊")
    report, _ = _build(
        _universe(("5371.TWO", "中光電", "🇹🇼 台灣")),
        _previous(old),
        _source("tpex_registry", [other]),
    )

    assert "SYMBOL_CHANGE" not in {event["type"] for event in report["events"]}
    assert report["policy"]["deletes_history"] is False


def test_missing_registry_row_never_deletes_and_requires_two_observations() -> None:
    old = _tw("2330.TW", "22099131", "台積電")
    universe = _universe(("2330.TW", "台積電", "🇹🇼 台灣"))
    first, registry = _build(universe, _previous(old), _source("twse_registry", []))
    second, _ = _build(universe, registry, _source("twse_registry", []))

    assert first["events"][0]["type"] == "MISSING_FROM_REGISTRY"
    assert second["events"][0]["type"] == "CONFIRMED_ABSENT"
    assert registry["records"]["2330.TW"]["entity_id"] == "TW-BN-22099131"
    assert second["policy"]["missing_row_is_not_delisting"] is True
    assert second["policy"]["deletes_history"] is False


def test_failed_required_registry_does_not_create_false_missing_event() -> None:
    old = _tw("2330.TW", "22099131", "台積電")
    report, registry = _build(
        _universe(("2330.TW", "台積電", "🇹🇼 台灣")),
        _previous(old),
        _source("twse_registry", [], ok=False),
    )

    assert report["events"] == []
    assert report["status"] == "warning"
    assert registry["records"]["2330.TW"]["source_unavailable"] is True


def test_official_event_parsers_classify_halt_resume_and_merger() -> None:
    events = normalize_tw_announcements([
        {"公司代號": "2330", "主旨": "董事會通過股份轉換案", "發言日期": "20260907"}
    ], exchange="TWSE")
    rss = """<rss><channel><item><title>Trade Halt</title><description>
        Issue Symbol: AAPL&lt;br/&gt;Resumption Time: N/A
    </description></item></channel></rss>"""
    namespaced_rss = """<rss xmlns:ndaq="urn:nasdaq"><channel><item>
        <title>Halt - News Pending</title><ndaq:IssueSymbol>MSFT</ndaq:IssueSymbol>
        <ndaq:HaltDate>09/07/2026</ndaq:HaltDate><ndaq:ReasonCode>T1</ndaq:ReasonCode>
        <ndaq:ResumptionTradeTime></ndaq:ResumptionTradeTime>
    </item></channel></rss>"""

    assert events[0]["type"] == "MERGER_OR_SHARE_EXCHANGE"
    assert parse_nasdaq_halts(rss)[0]["type"] == "TRADING_HALT"
    parsed = parse_nasdaq_halts(namespaced_rss)[0]
    assert parsed["symbol"] == "MSFT"
    assert parsed["type"] == "TRADING_HALT"


def test_event_source_failure_is_visible_but_does_not_change_formal_data() -> None:
    record = _tw("2330.TW", "22099131", "台積電")
    report, _ = _build(
        _universe(("2330.TW", "台積電", "🇹🇼 台灣")),
        _previous(record),
        _source("twse_registry", [record]),
        health={"twse_announcements": {"ok": False, "error": "timeout"}},
    )

    assert report["status"] == "warning"
    assert report["summary"]["event_source_failure_count"] == 1
    assert report["policy"]["changes_rankings"] is False


def test_workflow_is_shadow_only_and_runs_before_morning_report() -> None:
    workflow = Path(".github/workflows/corporate-actions-shadow.yml").read_text(encoding="utf-8")

    assert 'cron: "15 21 * * *"' in workflow
    assert 'cron: "0 18 * * 6"' in workflow
    assert "reports/corporate_actions_shadow.json" in workflow
    assert "reports/corporate_actions_shadow_history.json" in workflow
    assert "reports/corporate_actions_registry.json" in workflow
    assert "search_data.json" not in workflow
    assert "stock_data.json" not in workflow
