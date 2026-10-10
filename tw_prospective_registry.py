"""TW-only prospective frozen-plan ledger; no fetches, orders, or outcome claims.

The caller supplies current official observations and atomically persists the
returned payload. Raw exchange records are verified at the input boundary and
never included in this compact ledger. ``now`` is an injectable runtime clock,
not a historical replay cutoff; production callers must use the real clock.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any

from market_calendar import MARKET_ZONES, OfficialMarketCalendar
from tw_daily_shadow_attestation import OHLCV, SOURCES, parse_official_price_rows

VERSION = "TW-PROSPECTIVE-FROZEN-PLAN-V1"
STATUSES = {"enrolled_pending", "observed_wait", "triggered_close_only",
            "invalidated", "expired", "quarantined"}
TERMINAL = {"triggered_close_only", "invalidated", "expired"}
CALENDAR_STATUSES = {"verified_twse_tpex", "verified_conservative_union",
                     "verified_twse_only", "verified_tpex_only"}
IDENTITY_KEYS = ("market", "symbol", "horizon", "source_session_date",
                 "source_batch_at", "source_price", "levels")
LEVEL_KEYS = ("entry_low", "entry_high", "stop", "target1", "target2")
PROVENANCE_KEYS = ("symbol", "market", "source", "source_url", "source_session_date",
                   "fetched_at", "unit", "interval", "ohlcv", "raw_record_sha256",
                   "source_payload_sha256")


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _compact_fields(value: dict, keys: tuple[str, ...]) -> dict:
    """A malformed attestation must not leak arbitrary provider payloads."""
    return {key: ([item for item in value[key] if isinstance(item, str)]
                  if key == "source_ids" and isinstance(value.get(key), list)
                  else value.get(key) if isinstance(value.get(key), str) else None)
            for key in keys}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _time(value: Any, *, report: bool = False) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None and report:
            result = result.replace(tzinfo=MARKET_ZONES["TW"])
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _close(calendar: OfficialMarketCalendar, session: str) -> datetime | None:
    status = calendar.session_status("TW", session)
    if status.get("available") is not True or status.get("status") not in CALENDAR_STATUSES:
        raise ValueError("official_calendar_unavailable")
    parsed = _date(session)
    if parsed is None:
        raise ValueError("invalid_session_date")
    if status.get("is_session") is not True:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, 13, 30, tzinfo=MARKET_ZONES["TW"])


def _future_sessions(calendar: OfficialMarketCalendar, original: str, now: datetime,
                     count: int) -> list[tuple[str, datetime]]:
    """Inspect all dates, including holiday and cross-year coverage; never guess."""
    cursor = _date(original)
    if cursor is None or (now.astimezone(MARKET_ZONES["TW"]).date() - cursor).days > 370:
        raise ValueError("invalid_or_stale_source_session")
    cursor += timedelta(days=1)
    found = []
    for _ in range(740):
        closed = _close(calendar, cursor.isoformat())
        if closed is not None and closed > now:
            found.append((cursor.isoformat(), closed))
            if len(found) == count:
                return found
        cursor += timedelta(days=1)
    raise ValueError("official_calendar_window_unavailable")


def _fresh_until(calendar: OfficialMarketCalendar, session: str, now: datetime) -> datetime:
    parsed = _date(session)
    if parsed is None or (now.astimezone(MARKET_ZONES["TW"]).date() - parsed).days > 370:
        raise ValueError("invalid_or_stale_source_session")
    cursor = parsed + timedelta(days=1)
    for _ in range(370):
        closed = _close(calendar, cursor.isoformat())
        if closed is not None:
            return closed
        cursor += timedelta(days=1)
    raise ValueError("official_calendar_window_unavailable")


def _official(record: Any, symbol: str, calendar: OfficialMarketCalendar,
              now: datetime) -> tuple[dict[str, Any], datetime]:
    if not isinstance(record, dict):
        raise ValueError("official_quote_unavailable")
    source = record.get("source")
    spec = SOURCES.get(source) if isinstance(source, str) else None
    parsed = parse_official_price_rows(source, [record.get("raw_record")],
                                       fetched_at=record.get("fetched_at")).get(symbol)
    if (not spec or not parsed or not symbol.endswith(spec["suffix"])
            or any(record.get(key) != parsed.get(key) for key in PROVENANCE_KEYS
                   if key != "source_payload_sha256")
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("source_payload_sha256") or ""))
            or any(_number(_mapping(record.get("ohlcv")).get(key)) is None for key in OHLCV)):
        raise ValueError("invalid_official_record")
    closed = _close(calendar, parsed["source_session_date"])
    fetched = _time(parsed["fetched_at"])
    if closed is None:
        raise ValueError("quote_not_official_session")
    if closed > now:
        raise ValueError("quote_session_not_completed")
    if fetched is None or not closed <= fetched <= now:
        raise ValueError("invalid_quote_observation_time")
    compact = {key: deepcopy(record[key]) for key in PROVENANCE_KEYS}
    return compact, closed


def _identity(plan: dict, row: dict, horizon: str) -> dict:
    conclusion = _mapping(plan.get("conclusion"))
    snapshot = _mapping(conclusion.get("plan_snapshot"))
    identity = {key: deepcopy(snapshot.get(key)) for key in IDENTITY_KEYS}
    levels = identity.get("levels")
    if (identity["market"] != "TW" or not isinstance(identity["symbol"], str)
            or identity["symbol"] != row.get("symbol")
            or identity["horizon"] != horizon or not isinstance(levels, dict)
            or set(levels) != set(LEVEL_KEYS) or snapshot.get("id") != _digest(identity)
            or any(levels.get(key) != plan.get(key) for key in LEVEL_KEYS)
            or _number(identity["source_price"]) is None
            or any(_number(levels.get(key)) is None or float(levels[key]) <= 0
                   for key in LEVEL_KEYS if key != "target2")
            or (levels.get("target2") is not None and
                (_number(levels["target2"]) is None or float(levels["target2"]) <= 0))):
        raise ValueError("invalid_plan_identity_or_levels")
    if not 0 < float(levels["stop"]) < float(levels["entry_low"]) <= float(levels["entry_high"]) < float(levels["target1"]):
        raise ValueError("invalid_frozen_level_geometry")
    return {"plan_id": snapshot["id"], **identity}


def _new_frozen(row: dict, plan: dict, horizon: str, official: Any,
                calendar: OfficialMarketCalendar, now: datetime) -> dict:
    conclusion = _mapping(plan.get("conclusion"))
    if (row.get("market") != "TW" or horizon not in {"short", "medium", "long"}
            or conclusion.get("code") != "wait"
            or _mapping(conclusion.get("plan_assessment")).get("data_gates_passed") is not True
            or plan.get("active_entry_plan") is not True
            or _mapping(plan.get("plan_quality")).get("entry_eligible") is not True
            or row.get("risk_blocks")):
        raise ValueError("not_enrollment_candidate")
    identity = _identity(plan, row, horizon)
    window = plan.get("buy_window_sessions")
    if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= 10:
        raise ValueError("invalid_buy_window")
    hold = plan.get("max_hold_sessions")
    if not isinstance(hold, int) or isinstance(hold, bool) or hold <= 0:
        raise ValueError("invalid_holding_horizon")
    batch, assessed = _time(identity["source_batch_at"], report=True), _time(conclusion.get("evaluated_at"))
    if batch is None or assessed is None or batch > now or assessed > now:
        raise ValueError("invalid_plan_observation_time")
    provenance, closed = _official(official, identity["symbol"], calendar, now)
    if (provenance["source_session_date"] != identity["source_session_date"]
            or _number(identity["source_price"]) != _number(provenance["ohlcv"]["close"])
            or batch < closed or assessed < closed):
        raise ValueError("original_source_mismatch")
    price_expires = _fresh_until(calendar, identity["source_session_date"], now)
    if now >= price_expires:
        raise ValueError("original_price_snapshot_stale")
    future = _future_sessions(calendar, identity["source_session_date"], now, window + 1)
    data_expiry = _time(conclusion.get("expires_at"))
    news_expiry = _time(_mapping(conclusion.get("evidence")).get("news_expires_at"))
    return {
        **identity, "registered_at": now.isoformat(), "buy_window_sessions": window,
        "max_hold_sessions": hold, "provenance": provenance,
        "original_assessed_at": assessed.isoformat(),
        "data_freshness_expires_at": data_expiry.isoformat() if data_expiry else None,
        "original_price_freshness_expires_at": price_expires.isoformat(),
        "original_news_expires_at": news_expiry.isoformat() if news_expiry else None,
        "valid_sessions": [session for session, _ in future[:-1]],
        "valid_through_session": future[-2][0], "last_valid_close_at": future[-2][1].isoformat(),
        "evaluation_expires_at": future[-1][1].isoformat(),
    }


def _sources(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)


def _risk(risk: Any, frozen: dict, quote: dict, closed: datetime,
          now: datetime) -> tuple[str | None, str | None, dict]:
    if not isinstance(risk, dict):
        return "quarantined", "current_risk_unavailable", {}
    stamp = _time(risk.get("observed_at"))
    audit = _compact_fields(risk, ("market", "symbol", "source_session_date", "observed_at", "source_validity"))
    if (risk.get("market") != "TW" or risk.get("symbol") != frozen["symbol"]
            or risk.get("source_session_date") != quote["source_session_date"]
            or risk.get("source_validity") != "verified" or stamp is None
            or not closed <= stamp <= now or not isinstance(risk.get("risk_blocks"), list)):
        return "quarantined", "current_risk_unverified", audit
    if risk["risk_blocks"]:
        audit["risk_block_count"] = len(risk["risk_blocks"])
        return "invalidated", "current_risk_block", audit
    ca = _mapping(risk.get("corporate_actions"))
    ca_stamp = _time(ca.get("observed_at"))
    audit["corporate_actions"] = _compact_fields(ca, ("status", "observed_at", "source_ids", "coverage_from", "through_session"))
    audit["corporate_actions"]["reason"] = ca["reason"][:200] if isinstance(ca.get("reason"), str) else None
    audit["corporate_actions"]["reasons"] = [value[:200] for value in ca.get("reasons", [])
                                             if isinstance(value, str)][:8] if isinstance(ca.get("reasons"), list) else []
    if (ca.get("status") not in {"clear", "blocked"} or ca_stamp is None
            or not closed <= ca_stamp <= now or not _sources(ca.get("source_ids"))
            or not _date(ca.get("coverage_from"))
            or ca["coverage_from"] > frozen["source_session_date"]
            or ca.get("through_session") != quote["source_session_date"]):
        return "quarantined", "corporate_actions_unverified", audit
    if ca["status"] == "blocked":
        return "invalidated", "corporate_action_changes_price_basis", audit
    news = _mapping(risk.get("news"))
    news_stamp, news_expiry = _time(news.get("observed_at")), _time(news.get("expires_at"))
    audit["news"] = _compact_fields(news, ("status", "observed_at", "expires_at", "source_ids"))
    if (news.get("status") not in {"verified", "expired"} or news_stamp is None
            or news_expiry is None or news_stamp > now or news_stamp > stamp
            or not news_stamp < news_expiry <= news_stamp + timedelta(hours=18)
            or not _sources(news.get("source_ids"))):
        return "quarantined", "news_source_unverified", audit
    if news["status"] == "expired" or now >= news_expiry:
        return "observed_wait", "news_refresh_required", audit
    return None, None, audit


def _event(record: dict, status: str, reason: str, now: datetime,
           *, quote: dict | None = None, risk: dict | None = None,
           price_observation_valid: bool = False, missed_sessions: list[str] | None = None) -> None:
    history = record["evaluations"]
    event = {"sequence": len(history) + 1, "evaluated_at": now.isoformat(),
             "status": status, "reason": reason, "quote": quote, "risk": risk or {},
             "price_observation_valid": price_observation_valid,
             "missed_sessions": missed_sessions or [],
             "frozen_digest": record["frozen_digest"],
             "previous_event_digest": history[-1]["event_digest"] if history else None}
    event["event_digest"] = _digest(event)
    history.append(event)
    record.update(status=status, last_evaluated_at=event["evaluated_at"], last_reason=reason)


def _missed_sessions(record: dict, calendar: OfficialMarketCalendar, now: datetime) -> list[str]:
    """A skipped close cannot later be supplied from already-known history."""
    observed = {event["quote"]["source_session_date"] for event in record["evaluations"]
                if event.get("price_observation_valid") is True and isinstance(event.get("quote"), dict)}
    # Once a gap was recorded, it stays a gap even if later inputs contain that
    # session. Actual observation time, never fetched-at backdating, controls it.
    missed = {session for event in record["evaluations"] for session in event.get("missed_sessions", [])}
    for session in record["frozen"]["valid_sessions"]:
        if session not in observed and now >= _fresh_until(calendar, session, now):
            missed.add(session)
    return sorted(missed)


def _evaluate(record: dict, official: Any, risk: Any, calendar: OfficialMarketCalendar,
              now: datetime) -> None:
    frozen = record["frozen"]
    if record["status"] in TERMINAL:
        return
    if now >= _time(frozen["evaluation_expires_at"]):
        _event(record, "expired", "buy_window_elapsed", now)
        return
    quote, missed = None, []
    try:
        quote, closed = _official(official, frozen["symbol"], calendar, now)
        if (quote["source_session_date"] <= frozen["source_session_date"]
                or closed <= _time(frozen["registered_at"])):
            missed = _missed_sessions(record, calendar, now)
            if missed:
                _event(record, "quarantined", "missed_prospective_session", now, quote=quote, missed_sessions=missed)
                return
            _event(record, "enrolled_pending", "awaiting_genuinely_later_close", now, quote=quote)
            return
        if quote["source_session_date"] not in frozen["valid_sessions"]:
            _event(record, "expired", "quote_outside_buy_window", now, quote=quote)
            return
        # A known unadjusted close below the frozen stop permanently rules out
        # this plan, even if news/CA verification cannot establish why it fell.
        # This is conservative invalidation, never an inferred trade or return.
        if float(quote["ohlcv"]["close"]) <= float(frozen["levels"]["stop"]):
            status, _, audit = _risk(risk, frozen, quote, closed, now)
            _event(record, "invalidated", "close_breached_frozen_stop" if status is None else
                   "observed_price_basis_or_stop_breach", now, quote=quote, risk=audit,
                   price_observation_valid=False)
            return
        missed = _missed_sessions(record, calendar, now)
        if missed:
            _event(record, "quarantined", "missed_prospective_session", now, quote=quote, missed_sessions=missed)
            return
        if now >= _fresh_until(calendar, quote["source_session_date"], now):
            raise ValueError("evaluation_quote_stale")
    except ValueError as exc:
        reason = str(exc)
        if not missed:
            try:
                missed = _missed_sessions(record, calendar, now)
            except ValueError:
                pass
        if missed:
            _event(record, "quarantined", "missed_prospective_session", now, quote=quote, missed_sessions=missed)
            return
        _event(record, "enrolled_pending" if reason == "official_quote_unavailable" else "quarantined",
               reason, now, quote=quote)
        return
    status, reason, audit = _risk(risk, frozen, quote, closed, now)
    if status:
        _event(record, status, reason, now, quote=quote, risk=audit, price_observation_valid=True)
        return
    price, levels = float(quote["ohlcv"]["close"]), frozen["levels"]
    if float(levels["entry_low"]) <= price <= float(levels["entry_high"]):
        status, reason = "triggered_close_only", "close_inside_frozen_entry_range"
    else:
        status, reason = "observed_wait", "close_outside_frozen_entry_range"
    _event(record, status, reason, now, quote=quote, risk=audit, price_observation_valid=True)


def _load(previous: Any, now: datetime) -> dict:
    if previous is None:
        return {"version": VERSION, "created_at": now.isoformat(), "records": []}
    if not isinstance(previous, dict):
        raise ValueError("invalid_existing_registry")
    state = deepcopy(previous)
    supplied = state.pop("registry_digest", None)
    if (state.get("version") != VERSION or supplied != _digest(state)
            or not isinstance(state.get("records"), list)
            or _time(state.get("created_at")) is None
            or _time(state.get("evaluated_at")) is None
            or _time(state["evaluated_at"]) > now):
        raise ValueError("invalid_existing_registry")
    ids = set()
    for record in state["records"]:
        if not isinstance(record, dict):
            raise ValueError("invalid_frozen_registry_record")
        frozen = _mapping(record.get("frozen"))
        plan_id = frozen.get("plan_id")
        if (not plan_id or plan_id in ids or frozen.get("market") != "TW"
                or record.get("frozen_digest") != _digest(frozen)
                or _time(frozen.get("registered_at")) is None
                or _time(frozen["registered_at"]) > now
                or not isinstance(record.get("evaluations"), list) or not record["evaluations"]):
            raise ValueError("invalid_frozen_registry_record")
        ids.add(plan_id)
        previous_event, terminal = None, False
        last_time = _time(frozen["registered_at"])
        for sequence, event in enumerate(record["evaluations"], 1):
            if not isinstance(event, dict):
                raise ValueError("invalid_registry_evaluation_chain")
            content = {key: value for key, value in event.items() if key != "event_digest"}
            evaluated = _time(event.get("evaluated_at"))
            if (terminal or event.get("event_digest") != _digest(content)
                    or event.get("sequence") != sequence
                    or event.get("frozen_digest") != record["frozen_digest"]
                    or event.get("previous_event_digest") != previous_event
                    or event.get("status") not in STATUSES or evaluated is None
                    or not last_time <= evaluated <= now):
                raise ValueError("invalid_registry_evaluation_chain")
            previous_event, terminal, last_time = event["event_digest"], event["status"] in TERMINAL, evaluated
        if (record.get("status") != event["status"] or record.get("last_reason") != event["reason"]
                or record.get("last_evaluated_at") != event["evaluated_at"]):
            raise ValueError("invalid_registry_status")
    return state


def validate_tw_prospective_registry(previous: dict, *, now: datetime | None = None) -> dict:
    """Return a validated copy for read-only publication; never run evaluation."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must include timezone")
    if not isinstance(previous, dict):
        raise ValueError("invalid_existing_registry")
    state = _load(previous, now.astimezone(timezone.utc))
    state["registry_digest"] = _digest(state)
    return state


def build_tw_prospective_registry(plan_rows: list[dict], previous_registry: dict | None = None,
                                  *, calendar: OfficialMarketCalendar,
                                  now: datetime | None = None,
                                  evaluation_context: dict | None = None) -> dict:
    """Register now and assess prior registrations against later official closes.

    ``evaluation_context`` carries ``official_records`` and ``risk_snapshots``
    keyed by TW symbol, plus optional ``risk_snapshots_by_plan_id`` overrides
    for each frozen plan's distinct corporate-action coverage window.
    A newly registered plan is never evaluated in this call.
    Existing plan IDs/levels are immutable, and terminal records never restart.
    Repeating identical inputs at the same runtime timestamp is idempotent.
    Missing current inputs cannot be inferred from the original assessment.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must include timezone")
    now = now.astimezone(timezone.utc)
    if not isinstance(plan_rows, list):
        raise ValueError("plan_rows must be a list")
    context = evaluation_context or {}
    if (not isinstance(context, dict) or not isinstance(context.get("official_records", {}), dict)
            or not isinstance(context.get("risk_snapshots", {}), dict)
            or not isinstance(context.get("risk_snapshots_by_plan_id", {}), dict)):
        raise ValueError("invalid_evaluation_context")
    state = _load(previous_registry, now)
    official, risks = context.get("official_records", {}), context.get("risk_snapshots", {})
    plan_risks = context.get("risk_snapshots_by_plan_id", {})
    existing = {record["frozen"]["plan_id"]: record for record in state["records"]}
    batches = {(r["frozen"]["symbol"], r["frozen"]["horizon"], r["frozen"]["source_batch_at"]): r
               for r in state["records"]}
    candidates = [(row, horizon, plan) for row in plan_rows if isinstance(row, dict) and row.get("market") == "TW"
                  for horizon, plan in _mapping(row.get("plans")).items() if isinstance(plan, dict)]
    counts = Counter((row.get("symbol"), horizon) for row, horizon, _ in candidates)
    rejected = []
    for row, horizon, plan in candidates:
        try:
            if counts[row.get("symbol"), horizon] > 1:
                raise ValueError("duplicate_plan_candidate")
            identity = _identity(plan, row, horizon)
            found = existing.get(identity["plan_id"])
            batch_record = batches.get((row.get("symbol"), horizon, identity["source_batch_at"]))
            if found is not None or batch_record is not None:
                old = (found or batch_record)["frozen"]
                if (any(old.get(key) != value for key, value in identity.items())
                        or old["buy_window_sessions"] != plan.get("buy_window_sessions")
                        or old["max_hold_sessions"] != plan.get("max_hold_sessions")):
                    raise ValueError("amended_frozen_plan_rejected")
                continue
            frozen = _new_frozen(row, plan, horizon, official.get(row.get("symbol")), calendar, now)
            record = {"frozen": frozen, "frozen_digest": _digest(frozen), "evaluations": []}
            _event(record, "enrolled_pending", "registered_now_awaiting_later_close", now)
            state["records"].append(record)
            batches[row.get("symbol"), horizon, identity["source_batch_at"]] = record
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"symbol": row.get("symbol"), "horizon": horizon, "reason": str(exc)})
    # Only records present on entry can be evaluated. Repeated calls at the same
    # timestamp are no-ops, preventing a same-run retry from creating a trigger.
    for record in existing.values():
        if _time(record["last_evaluated_at"]) < now:
            plan_id = record["frozen"]["plan_id"]
            current_risk = (plan_risks[plan_id] if plan_id in plan_risks else
                            risks.get(record["frozen"]["symbol"]))
            _evaluate(record, official.get(record["frozen"]["symbol"]),
                      current_risk, calendar, now)
    state.update(evaluated_at=now.isoformat(), rejections=rejected,
                 summary={status: sum(r["status"] == status for r in state["records"])
                          for status in sorted(STATUSES)},
                 policy={"shadow_only": True, "automatic_orders": False, "formal_v6_unchanged": True,
                         "tw_only": True, "close_only_not_execution": True,
                         "intraday_touch_evaluated": False, "historical_reconstruction": False,
                         "continuous_prospective_close_observations_required": True,
                         "missed_sessions_never_backfilled": True,
                         "accuracy_claim": None, "probability_pct": None,
                         "raw_provider_records_published": False})
    state["registry_digest"] = _digest(state)
    return state
