"""Runtime adapter for the TW-only prospective research ledger.

Uses existing public official daily feeds and existing report storage. No model
is run, and no broker or additional authentication is used. Public output is
compact verified observations, never raw provider response records.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from market_calendar import MARKET_ZONES, OfficialMarketCalendar
from tw_daily_shadow_attestation import fetch_official_price_records
from tw_prospective_registry import build_tw_prospective_registry

REGISTRY_FILE = "tw_prospective_registry.json"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid_report_object")
    return value


def _time(value: Any, *, report: bool = False) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None and report:
            stamp = stamp.replace(tzinfo=MARKET_ZONES["TW"])
        return stamp if stamp.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _identity_action_status(reports_dir: Path, symbol: str, session: str, now: datetime) -> tuple[str, str]:
    """Current identity/halts feed is separate from ranged price-basis actions."""
    try:
        payload = _read(reports_dir / "corporate_actions_shadow.json")
        observed = _time(payload.get("generated_at"))
        closed = datetime.fromisoformat(session + "T13:30:00").replace(tzinfo=MARKET_ZONES["TW"])
        exchange = "tpex" if symbol.endswith(".TWO") else "twse"
        sources = payload.get("source_health") or {}
        if (observed is None or not closed <= observed <= now or now - observed >= timedelta(hours=24)
                or any((sources.get(exchange + suffix) or {}).get("ok") is not True
                       for suffix in ("_registry", "_announcements"))
                or not isinstance(payload.get("events"), list)):
            return "unsupported", "identity_actions_refresh_required"
        # Warnings are conservative invalidations, never inferred safe by type.
        if any(isinstance(event, dict) and event.get("symbol") == symbol for event in payload["events"]):
            return "blocked", "current_identity_or_trading_action"
        if (payload.get("summary") or {}).get("unmatched") != 0:
            return "unsupported", "identity_coverage_incomplete"
        return "clear", "current_official_identity_and_announcements"
    except (OSError, ValueError, TypeError):
        return "unsupported", "identity_actions_unavailable"


def build_current_risk_snapshots(reports_dir: Path, report: dict, records: dict,
                                 *, now: datetime, corporate_actions: dict | None = None) -> dict:
    """Bind current risk to the actual coherent hub batch, never the fetch clock.

    Current source/indicator gates must pass; original-plan news/risk is never
    carried forward. Missing corporate-action coverage is an explicit quarantine.
    """
    try:
        index = _read(reports_dir / "decision_hub.json")
        observed = _time(index.get("updated_at"), report=True)
        if (observed is None or observed > now or index.get("updated_at") != report.get("updated_at")
                or not isinstance(index.get("decision_files"), list)):
            return {}
        rows = {}
        for filename in index["decision_files"]:
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.startswith("decision_hub_") or not filename.endswith(".json"):
                return {}
            chunk = _read(reports_dir / filename)
            if chunk.get("updated_at") != index["updated_at"]:
                return {}
            for row in chunk.get("decisions", []):
                key = row.get("symbol")
                if key in rows:
                    return {}
                rows[key] = row
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
    risks = {}
    for compact in report.get("plans", []):
        symbol = compact.get("symbol")
        row, quote = rows.get(symbol), records.get(symbol)
        if compact.get("market") != "TW" or not isinstance(row, dict) or not isinstance(quote, dict):
            continue
        source_ok = (row.get("session_date") == quote.get("source_session_date")
                     and any((plan.get("conclusion", {}).get("plan_assessment") or {}).get("data_gates_passed") is True
                             for plan in compact.get("plans", {}).values()))
        news = [e for e in row.get("evidence", []) if isinstance(e, dict) and e.get("source_id") == "verified_news"]
        news_state = {"status": "unsupported"}
        if len(news) == 1:
            item = news[0]
            scan = _time(item.get("as_of"), report=True)
            if (scan is not None and scan <= observed and item.get("affects_decision") is True
                    and item.get("market") == "TW" and item.get("symbol") == symbol
                    and item.get("provenance") and item.get("status") in {"available", "verified"}
                    and item.get("direction") in {"neutral", "oppose"}):
                expiry = scan + timedelta(hours=18)
                news_state = {"status": "expired" if now >= expiry or (row.get("source_snapshot") or {}).get("news_cache_stale") else "verified",
                              "observed_at": scan.isoformat(), "expires_at": expiry.isoformat(),
                              "source_ids": [str(item["provenance"])]}
        blocks = list(compact.get("risk_blocks") or [])
        if news and any(e.get("direction") == "oppose" for e in news):
            blocks.append("current_verified_news_risk")
        action_state = dict((corporate_actions or {}).get(symbol) or {
            "status": "unsupported", "reason": "dividend_split_coverage_unverified"})
        identity_status, identity_reason = _identity_action_status(reports_dir, symbol, str(row.get("session_date") or ""), now)
        if identity_status == "blocked":
            blocks.append(identity_reason)
        elif identity_status != "clear" and action_state.get("status") != "blocked":
            action_state.update(status="unsupported", reason=identity_reason)
        elif action_state.get("status") == "clear":
            action_state["source_ids"] = list(action_state.get("source_ids") or []) + [identity_reason]
        risks[symbol] = {"market": "TW", "symbol": symbol,
                         "source_session_date": row.get("session_date"),
                         "observed_at": observed.isoformat(),
                         "source_validity": "verified" if source_ok else "unverified",
                         "risk_blocks": blocks, "news": news_state,
                         "corporate_actions": action_state}
    return risks


def attach_registry_summary(report: dict, registry: dict) -> None:
    by_symbol = {}
    next_sessions = []
    for record in registry["records"]:
        frozen = record["frozen"]
        sessions = frozen["valid_sessions"]
        quote_session = ((record["evaluations"][-1].get("quote") or {}).get("source_session_date") or "")
        next_session = next((s for s in sessions if s > quote_session), None) if record["status"] not in {"triggered_close_only", "invalidated", "expired"} else None
        if record["status"] not in {"triggered_close_only", "invalidated", "expired"} and next_session:
            next_sessions.append(next_session)
        by_symbol.setdefault(frozen["symbol"], []).append({
            "plan_id": frozen["plan_id"], "horizon": frozen["horizon"], "status": record["status"],
            "registered_at": frozen["registered_at"], "valid_through_session": frozen["valid_through_session"],
            "evaluation_expires_at": frozen["evaluation_expires_at"], "frozen_levels": frozen["levels"],
            "last_reason": record["last_reason"], "latest_evaluated_at": record["last_evaluated_at"],
            "next_observation_session": next_session,
        })
    for row in report.get("plans", []):
        row["prospective_entries"] = by_symbol.get(row.get("symbol"), [])
    report["tw_prospective_registry"] = {
        "status": "ready", "label": ("台股原始計畫已前瞻登錄，僅以後續正式收盤觀察" if registry["records"] else "尚無通過登錄條件的台股計畫"),
        "version": registry["version"], "evaluated_at": registry["evaluated_at"],
        "registry_digest": registry["registry_digest"], "ledger_file": REGISTRY_FILE,
        "summary": {"registered_total": len(registry["records"]), "status_counts": registry["summary"],
                    "next_observation_session": min(next_sessions) if next_sessions else None},
        "corporate_action_coverage": "requires_verified_dividend_split_coverage_for_each_later_observation",
        "automatic_orders": False, "predictive_efficacy_validated": False,
    }


def update_tw_prospective_report(reports_dir: Path, report: dict, *, now: datetime | None = None,
                                 evaluation_context: dict | None = None, fetch_records=None) -> dict:
    """Persist first, expose registered status only after successful atomic save.

    Injection hooks are for tests. Production has no historical-clock CLI; it
    registers at actual runtime after collection. Corrupt ledgers are preserved.
    """
    path = reports_dir / REGISTRY_FILE
    previous = _read(path) if path.exists() else None
    symbols = {row.get("symbol") for row in report.get("plans", []) if row.get("market") == "TW"}
    symbols.update(r["frozen"]["symbol"] for r in (previous or {}).get("records", []))
    if evaluation_context is None:
        records, audit = (fetch_records or fetch_official_price_records)(symbols) if symbols else ({}, [])
        # Collect only for existing active plans with genuinely later closes.
        # First registration does not need or fabricate a future event check.
        requests = []
        for entry in (previous or {}).get("records", []):
            frozen = entry["frozen"]
            quote = records.get(frozen["symbol"]) or {}
            if (entry.get("status") not in {"triggered_close_only", "invalidated", "expired"}
                    and quote.get("source_session_date", "") > frozen["source_session_date"]
                    and quote.get("source") in {"TWSE OpenAPI", "TPEx OpenAPI"}):
                requests.append({"plan_id": frozen["plan_id"], "symbol": frozen["symbol"],
                                 "original_session": frozen["source_session_date"],
                                 "through_session": quote["source_session_date"]})
        actions = {}
        if requests:
            from tw_price_action_coverage import fetch_tw_price_action_coverage_for_plans
            actions = fetch_tw_price_action_coverage_for_plans(requests, now=now)
        actual_now = now or datetime.now(timezone.utc)
        risks = build_current_risk_snapshots(reports_dir, report, records, now=actual_now)
        per_plan = {}
        for request in requests:
            symbol, plan_id = request["symbol"], request["plan_id"]
            updated = build_current_risk_snapshots(reports_dir, report, records, now=actual_now,
                                                  corporate_actions={symbol: actions.get(plan_id)})
            per_plan[plan_id] = updated.get(symbol)
        evaluation_context = {"official_records": records, "risk_snapshots": risks,
                              "risk_snapshots_by_plan_id": per_plan}
    else:
        actual_now = now or datetime.now(timezone.utc)
    calendar = OfficialMarketCalendar(reports_dir / "official_market_calendar.json", auto_refresh=False, allow_network=False)
    registry = build_tw_prospective_registry(report.get("plans", []), previous, calendar=calendar,
                                             now=actual_now, evaluation_context=evaluation_context)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)
    attach_registry_summary(report, registry)
    return registry
