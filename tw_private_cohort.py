"""Offline-only private Fubon TW cohort feature reconstruction.

No SDK calls, scoring, ranking, plan creation, persistence, or public market-value
projection occurs here. Inputs come from trusted in-process source collectors,
not request JSON. Every numerical feature is rebuilt from the supplied bars;
old formal features, scores and price levels are never copied into the result.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "TW-FUBON-PRIVATE-COHORT-V1"
SOURCE = "Fubon Neo historical candles"
TAIPEI = ZoneInfo("Asia/Taipei")
SUPPORTED = {"TWSE": "TWSE OpenAPI", "TPEX_MAINBOARD": "TPEx OpenAPI"}
MAX_SYMBOLS = 256
BAR_FIELDS = ("open", "high", "low", "close", "volume")
NONPRICE_FIELDS = {
    "fundamental": {"per", "pbr", "dividend_yield", "revenue_yoy_pct", "revenue_mom_pct"},
    "financial": {"eps", "eps_yoy_pct", "gross_margin_pct", "operating_margin_pct", "debt_ratio_pct", "roe_pct", "operating_cash_flow"},
    "institution": {"foreign_net", "trust_net", "dealer_net", "institution_1d", "institution_3d", "institution_5d", "institution_10d"},
    "credit": {"margin_1d_change", "margin_5d_change", "short_1d_change", "short_5d_change", "sbl_1d_change", "sbl_5d_change"},
    "news": {"news_penalty"},
}
STRUCTURAL_EVENTS = {"stock_dividend", "rights", "rights_and_dividend", "split", "reverse_split", "capital_reduction", "par_value_change", "capital_increase"}
REASONS = {
    "unsupported_venue", "history_unavailable", "history_identity", "history_provenance",
    "history_digest", "history_shape", "history_session_coverage", "history_insufficient",
    "official_crosscheck_unavailable", "official_crosscheck_mismatch", "action_coverage_unknown",
    "action_coverage_invalid", "structural_action", "ambiguous_action", "nonprice_unavailable",
    "nonprice_provenance", "nonprice_invalid", "intraday_unavailable", "scoring_not_integrated",
}


class InvalidCohort(ValueError):
    """Only bounded reason codes cross a diagnostic boundary."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _stamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError()
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError()
    return result


def _finite(value: Any, *, positive=False) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (not positive or value > 0))


def _same_number(a: Any, b: Any) -> bool:
    try:
        return not isinstance(a, bool) and not isinstance(b, bool) and Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


def _history(history: Any, item: dict, sessions: list[str], now: datetime) -> list[dict]:
    if not isinstance(history, dict):
        raise InvalidCohort("history_unavailable")
    if (history.get("status") != "validated_in_memory" or history.get("source") != SOURCE
            or history.get("symbol") != item["symbol"] or history.get("market") != "TW"
            or history.get("venue") != item["venue"]):
        raise InvalidCohort("history_identity")
    try:
        requested, observed = _stamp(history.get("requested_at")), _stamp(history.get("observed_at"))
        settled = datetime.combine(date.fromisoformat(sessions[-1]), time(16, 30), TAIPEI)
        if not requested <= observed <= now or observed < settled:
            raise ValueError()
    except (TypeError, ValueError):
        raise InvalidCohort("history_provenance") from None
    request = history.get("request")
    if not isinstance(request, dict):
        raise InvalidCohort("history_provenance")
    if (history.get("price_basis") != "raw_unadjusted" or history.get("volume_unit") != "shares"
            or request.get("timeframe") != "D" or request.get("adjusted") != "false"
            or history.get("adjustment_evidence") != "explicit_request_and_provider_contract"
            or request.get("from") != sessions[0] or request.get("to") != sessions[-1]
            or history.get("source_session_date") != sessions[-1]):
        raise InvalidCohort("history_provenance")
    bars = history.get("bars")
    if not isinstance(bars, list) or not 60 <= len(bars) <= 120:
        raise InvalidCohort("history_insufficient")
    if any(not isinstance(bar, dict) or set(bar) != {"date", *BAR_FIELDS} for bar in bars):
        raise InvalidCohort("history_shape")
    if [bar["date"] for bar in bars] != sessions:
        raise InvalidCohort("history_session_coverage")
    for bar in bars:
        if (any(not _finite(bar[k], positive=True) for k in BAR_FIELDS[:4])
                or not _finite(bar["volume"]) or not 0 <= bar["volume"] <= 2**53
                or not float(bar["volume"]).is_integer()
                or bar["low"] > min(bar["open"], bar["close"])
                or bar["high"] < max(bar["open"], bar["close"])):
            raise InvalidCohort("history_shape")
    # Producer hashes normalized bars with the same canonical JSON settings.
    if history.get("response_sha256") != _digest(bars):
        raise InvalidCohort("history_digest")
    return deepcopy(bars)


def daily_features(bars: list[dict]) -> dict[str, Any]:
    """Measured daily primitives only; no intraday or missing-input defaults.

    ATR follows the existing strategy's 14-day mean high-low definition, not a
    new true-range definition. RSI follows its 14-change simple rolling method,
    with explicit mathematically defined zero-loss and unchanged-price cases.
    """
    close = [bar["close"] for bar in bars]
    volume = [bar["volume"] for bar in bars]
    price = close[-1]
    changes = [close[i] - close[i - 1] for i in range(len(close) - 14, len(close))]
    gain = mean(max(v, 0) for v in changes)
    loss = mean(max(-v, 0) for v in changes)
    rsi = 50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    result = {"price": price, "source_session_date": bars[-1]["date"],
              "change_pct": (price / close[-2] - 1) * 100,
              "rsi": rsi, "atr14": mean(bar["high"] - bar["low"] for bar in bars[-14:]),
              "support1": min(bar["low"] for bar in bars[-5:]),
              "support2": min(bar["low"] for bar in bars[-20:]),
              "resistance1": max(bar["high"] for bar in bars[-5:]),
              "resistance2": max(bar["high"] for bar in bars[-20:]),
              "intraday_available": False, "attack_volume": None,
              "opening_attack_15m": None, "opening_attack_30m": None,
              "volume_pace": None, "feature_scope": "completed_daily_primitives_only"}
    result.update({f"ma{n}": mean(close[-n:]) for n in (5, 10, 20, 60)})
    result.update({f"avg_volume{n}": mean(volume[-n:]) for n in (5, 10, 20)})
    result["daily_volume_ratio"] = volume[-1] / result["avg_volume20"] if result["avg_volume20"] > 0 else None
    result["ma20_distance_pct"] = (price / result["ma20"] - 1) * 100
    result["ma20_slope5_pct"] = (result["ma20"] / mean(close[-25:-5]) - 1) * 100
    for field in BAR_FIELDS:
        result["daily_" + field] = bars[-1][field]
    return result


def _crosscheck(record: Any, item: dict, bars: list[dict], now: datetime) -> str | None:
    # Reparse the official raw evidence rather than trusting a source label.
    from tw_daily_shadow_attestation import SOURCES, parse_official_price_rows
    if not isinstance(record, dict):
        return "official_crosscheck_unavailable"
    source = SUPPORTED[item["venue"]]
    try:
        observed = _stamp(record.get("fetched_at"))
        parsed = parse_official_price_rows(source, [record.get("raw_record")], fetched_at=record["fetched_at"]).get(item["symbol"])
        if (not parsed or observed > now
                or observed < datetime.combine(date.fromisoformat(bars[-1]["date"]), time(13, 30), TAIPEI)
                or record.get("source") != source
                or record.get("source_url") != SOURCES[source]["url"]
                or parsed["source_session_date"] != bars[-1]["date"]
                or record.get("raw_record_sha256") != parsed["raw_record_sha256"]):
            return "official_crosscheck_unavailable"
        if any(not _same_number(parsed["ohlcv"][k], bars[-1][k]) for k in BAR_FIELDS):
            return "official_crosscheck_mismatch"
    except (ValueError, TypeError, KeyError):
        return "official_crosscheck_unavailable"
    return None


def action_policy(evidence: Any, *, symbol: str, first: str, last: str, now: datetime) -> dict:
    """Versioned private raw-price policy; existing frozen-plan rules stay intact.

    The typed collector must prove both complete source families over the whole
    consumed frame. It must merge overlapping inclusive windows BEFORE applying
    this overall first-bar boundary. Cash-only distributions are real raw-price
    gaps; unknown/share/capital changes cannot be adjusted here.
    """
    result = {"version": "TW-RAW-ACTION-POLICY-V1", "basis": "raw_unadjusted_price_return",
              "status": "held", "warnings": [], "reason": "action_coverage_unknown",
              "first_bar_events": 0, "adjustment_policy": "none"}
    if not isinstance(evidence, dict):
        return result
    needed = {"corporate_actions.dividends", "corporate_actions.capital_changes"}
    try:
        observed = _stamp(evidence.get("observed_at"))
        close = datetime.combine(date.fromisoformat(last), time(13, 30), TAIPEI)
        if (evidence.get("status") != "verified_complete" or evidence.get("symbol") != symbol
                or evidence.get("coverage_from") != first or evidence.get("through_session") != last
                or evidence.get("source") != "Fubon Neo corporate actions"
                or not close <= observed <= now or not isinstance(evidence.get("events"), list)
                or not isinstance(evidence.get("coverage"), list)):
            raise ValueError()
        coverage = evidence["coverage"]
        if len(coverage) != 2 or {c.get("method") for c in coverage if isinstance(c, dict)} != needed:
            raise ValueError()
        for item in coverage:
            if (item.get("status") != "verified_complete" or item.get("coverage_from") != first
                    or item.get("through_session") != last
                    or not close <= _stamp(item.get("observed_at")) <= observed
                    or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("response_sha256") or ""))):
                raise ValueError()
        seen = {}
        for event in evidence["events"]:
            if not isinstance(event, dict):
                raise ValueError()
            effective = date.fromisoformat(event.get("effective_date"))
            if (not date.fromisoformat(first) <= effective <= date.fromisoformat(last)
                    or event.get("symbol") != symbol or event.get("source_method") not in needed
                    or event.get("classification_status") != "verified"
                    or event.get("classification_version") != "TW-RAW-ACTION-TYPES-V1"
                    or not re.fullmatch(r"[a-f0-9]{64}", str(event.get("raw_record_sha256") or ""))):
                raise ValueError()
            key = (event["source_method"], event["raw_record_sha256"], effective)
            if key in seen:
                if event != seen[key]:
                    result["reason"] = "ambiguous_action"
                    return result
                continue
            seen[key] = event
            kind = event.get("kind")
            if kind == "cash_dividend":
                if (event["source_method"] != "corporate_actions.dividends"
                        or event.get("dividend_type") != "息"
                        or not _finite(event.get("cash_dividend"), positive=True)
                        or not _same_number(event.get("stock_dividend_shares"), 0)
                        or any(key in event and not _same_number(event[key], 0)
                               for key in ("rights_subscription_shares", "rights_subscription_ratio"))):
                    result["reason"] = "ambiguous_action"
                    return result
            elif kind not in STRUCTURAL_EVENTS:
                result["reason"] = "ambiguous_action"
                return result
            # Classify first, including contradictions. Once identity/basis is
            # known, every input and indicator seed is post-event on first bar.
            if effective.isoformat() == first:
                result["first_bar_events"] += 1
                continue
            if kind == "cash_dividend":
                result["warnings"].append("cash_dividend_raw_price_gap_not_total_return")
            else:
                result["reason"] = "structural_action"
                return result
    except (ValueError, TypeError, AttributeError):
        result["reason"] = "action_coverage_invalid"
        return result
    result.update(status="research_basis_checked", reason=None, warnings=sorted(set(result["warnings"])))
    return result


def _nonprice(envelope: Any, *, symbol: str, batch: str, now: datetime) -> tuple[dict, list[str]]:
    if not isinstance(envelope, dict):
        return {}, ["nonprice_unavailable"]
    try:
        if (envelope.get("symbol") != symbol or envelope.get("market") != "TW"
                or envelope.get("source_batch") != batch or _stamp(envelope.get("observed_at")) > now
                or not isinstance(envelope.get("groups"), dict)):
            return {}, ["nonprice_provenance"]
    except (TypeError, ValueError):
        return {}, ["nonprice_provenance"]
    values, reasons = {}, []
    for group, allowed in NONPRICE_FIELDS.items():
        entry = envelope["groups"].get(group)
        if not isinstance(entry, dict) or entry.get("available") is not True:
            values[group] = None
            reasons.append("nonprice_unavailable")
            continue
        try:
            if (not isinstance(entry.get("source"), str) or not entry["source"]
                    or not _stamp(entry.get("as_of")) <= _stamp(entry.get("observed_at")) <= _stamp(envelope["observed_at"])
                    or not isinstance(entry.get("values"), dict)
                    or not entry["values"] or set(entry["values"]) - allowed
                    or any(not _finite(v) for v in entry["values"].values())):
                raise ValueError()
            if group == "news" and not now < _stamp(entry.get("expires_at")):
                raise ValueError()
            values[group] = {key: deepcopy(entry[key]) for key in
                             ("available", "source", "as_of", "observed_at", "values") if key in entry}
            if group == "news":
                values[group]["expires_at"] = entry["expires_at"]
        except (TypeError, ValueError):
            values[group] = None
            reasons.append("nonprice_invalid")
    return values, sorted(set(reasons))


def build_private_cohort(formal: dict, manifest: list[dict], histories: dict, *,
                         calendar_evidence: dict, official_records: dict | None = None,
                         actions: dict | None = None, nonprice: dict | None = None,
                         now: datetime) -> dict:
    """Reconstruct one exact frozen TW universe; do not score a partial subset."""
    if now.tzinfo is None:
        raise InvalidCohort("cohort_timestamp_requires_timezone")
    before = _digest(formal)
    try:
        report_time = datetime.fromisoformat(str(formal.get("updated_at")))
        if report_time.tzinfo is None:
            report_time = report_time.replace(tzinfo=TAIPEI)
        if report_time > now:
            raise ValueError()
    except ValueError:
        raise InvalidCohort("formal_batch_timestamp") from None
    source_rows = formal.get("data")
    if not isinstance(source_rows, list) or not formal.get("updated_at"):
        raise InvalidCohort("formal_batch_invalid")
    expected = [row.get("symbol") for row in source_rows if isinstance(row, dict) and row.get("market") == "TW"]
    if (not expected or not all(isinstance(s, str) for s in expected)
            or len(expected) > MAX_SYMBOLS or len(set(expected)) != len(expected)
            or not isinstance(manifest, list) or not all(isinstance(x, dict) and isinstance(x.get("symbol"), str) for x in manifest)
            or len(manifest) != len(expected) or {x.get("symbol") for x in manifest} != set(expected)):
        raise InvalidCohort("manifest_mismatch")
    for item in manifest:
        frozen_row = next(row for row in source_rows if isinstance(row, dict) and row.get("symbol") == item["symbol"])
        if (item.get("market") != "TW" or not isinstance(item.get("symbol"), str)
                or not re.fullmatch(r"[0-9][0-9A-Z]{2,7}\.(?:TW|TWO)", item["symbol"])
                or item.get("venue") not in {*SUPPORTED, "TPEX_EMERGING"}
                or item.get("type") not in {"個股", "ETF"}
                or item.get("type") != frozen_row.get("type")
                or ("venue" in frozen_row and item.get("venue") != frozen_row["venue"])
                or ((item["venue"] == "TWSE") != item["symbol"].endswith(".TW"))):
            raise InvalidCohort("manifest_identity")
    sessions = calendar_evidence.get("sessions") if isinstance(calendar_evidence, dict) else None
    try:
        if (calendar_evidence.get("available") is not True or calendar_evidence.get("status") != "verified"
                or calendar_evidence.get("source_statuses") != ["verified_twse_tpex"]
                or not isinstance(sessions, list) or not all(isinstance(s, str) for s in sessions)
                or not 60 <= len(sessions) <= 120
                or sessions != sorted(set(sessions))
                or any(date.fromisoformat(s).isoformat() != s for s in sessions)):
            raise ValueError()
        if (date.fromisoformat(sessions[-1]) - date.fromisoformat(sessions[0])).days >= 120:
            raise ValueError()
        settled = datetime.combine(date.fromisoformat(sessions[-1]), time(16, 30), TAIPEI)
        if settled > now:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InvalidCohort("calendar_contract") from None
    if report_time < datetime.combine(date.fromisoformat(sessions[-1]), time(13, 30), TAIPEI):
        raise InvalidCohort("formal_batch_timestamp")
    if any(row.get("official_session_date") != sessions[-1] for row in source_rows
           if isinstance(row, dict) and row.get("market") == "TW"):
        raise InvalidCohort("formal_source_session_mismatch")
    if not isinstance(histories, dict) or set(histories) - set(expected):
        raise InvalidCohort("history_universe_mismatch")
    results = []
    for item in sorted(manifest, key=lambda x: x["symbol"]):
        symbol = item["symbol"]
        out = {"symbol": symbol, "venue": item["venue"], "asset_type": item["type"], "status": "held", "features": None,
               "nonprice": {}, "reasons": [], "plans": None, "decision_eligible": False}
        results.append(out)
        if item["venue"] not in SUPPORTED:
            out["reasons"] = ["unsupported_venue"]
            continue
        try:
            bars = _history(histories.get(symbol), item, sessions, now)
        except InvalidCohort as exc:
            out["reasons"] = [str(exc)]
            continue
        out.update(status="daily_features_rebuilt", features=daily_features(bars),
                   private_source_provenance={k: deepcopy(histories[symbol].get(k)) for k in
                       ("source", "price_basis", "volume_unit", "requested_at", "observed_at", "response_sha256")})
        reason = _crosscheck((official_records or {}).get(symbol), item, bars, now)
        if reason:
            out["reasons"].append(reason)
        policy = action_policy((actions or {}).get(symbol), symbol=symbol, first=sessions[0], last=sessions[-1], now=now)
        out["action_policy"] = policy
        if policy["reason"]:
            out["reasons"].append(policy["reason"])
        out["nonprice"], missing = _nonprice((nonprice or {}).get(symbol), symbol=symbol, batch=formal["updated_at"], now=now)
        out["reasons"].extend(missing + ["intraday_unavailable", "scoring_not_integrated"])
    after = _digest(formal)
    if before != after:
        raise InvalidCohort("formal_input_mutated")
    return {"version": VERSION, "stage": "offline_private_daily_features", "observed_at": now.isoformat(),
            "source_batch": formal["updated_at"], "manifest_sha256": _digest(manifest),
            "formal_sha256_before": before, "formal_sha256_after": after,
            "rows": results, "network_calls": 0, "decision_eligible": False,
            "formal_v6_unchanged": True, "scoring_executed": False,
            "all_supported_histories_validated": any(r["venue"] in SUPPORTED for r in results)
            and all(r["features"] is not None for r in results if r["venue"] in SUPPORTED)}


def safe_status(private: dict) -> dict:
    """Only a fixed metadata/count projection may leave the private consumer."""
    rows = private.get("rows") if isinstance(private.get("rows"), list) else []
    reasons = Counter(reason for row in rows for reason in set(row.get("reasons", [])) if reason in REASONS)
    return {"version": VERSION, "stage": "offline_private_daily_features",
            "symbol_count": len(rows), "daily_features_rebuilt_count": sum(row.get("status") == "daily_features_rebuilt" for row in rows),
            "unsupported_venue_count": reasons.get("unsupported_venue", 0),
            "reason_counts": dict(sorted(reasons.items())), "decision_eligible": False,
            "scoring_executed": False, "formal_v6_unchanged": bool(re.fullmatch(r"[a-f0-9]{64}", str(private.get("formal_sha256_before") or "")))
            and private.get("formal_sha256_before") == private.get("formal_sha256_after"),
            "raw_bars_exported": False, "market_values_exported": False, "network_calls": 0}
