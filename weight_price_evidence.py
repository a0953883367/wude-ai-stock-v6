"""Official price evidence for the existing TW weight experiment only.

Never changes feature/ranking rows in place or rewrites observations.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from zoneinfo import ZoneInfo
import math

from market_calendar import OfficialMarketCalendar


def evidence_valid(proof, date, opening=None, closing=None):
    if not isinstance(proof, dict) or proof.get("status") != "verified" or proof.get("date") != date:
        return False
    unsigned = {k: v for k, v in proof.items() if k != "sha256"}
    if proof.get("sha256") != sha256(json.dumps(unsigned, sort_keys=True, ensure_ascii=False).encode()).hexdigest():
        return False
    calendar = proof.get("calendar_status") or {}
    if calendar.get("available") is not True or calendar.get("is_session") is not True or proof.get("session_complete") is not True:
        return False
    if proof.get("source") not in {"TWSE OpenAPI", "TPEx OpenAPI"}:
        return False
    try:
        o, c = float(proof["open"]), float(proof["close"])
        return all(math.isfinite(x) and x > 0 for x in (o, c)) and (opening is None or abs(o - opening) < .0001) and (closing is None or abs(c - closing) < .0001)
    except (KeyError, TypeError, ValueError):
        return False


def prepare_rows(rows, prices, calendar, updated_at):
    output = deepcopy(rows)
    try:
        parsed = datetime.fromisoformat(updated_at)
        at = (parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("Asia/Taipei"))).timestamp()
    except (ValueError, TypeError):
        at = None
    for row in output:
        if row.get("market") != "TW":
            continue
        row["shadow_price_contract_required"] = True
        snapshot = prices.get(str(row.get("symbol", "")).split(".")[0]) or {}
        session = str(snapshot.get("date") or "")
        evidence = {"status": "unverified", "reason": "official_snapshot_missing"}
        if session and at is not None:
            status = calendar.session_status("TW", session)
            complete = calendar.session_complete("TW", session, at_epoch=at)
            valid = (status.get("available") is True and status.get("is_session") is True
                     and complete and snapshot.get("tw_official_price_available") is True
                     and snapshot.get("tw_price_unit") == "TWD/shares"
                     and snapshot.get("tw_price_source") in {"TWSE OpenAPI", "TPEx OpenAPI"}
                     and session == row.get("official_session_date"))
            evidence = {"status": "verified" if valid else "unverified",
                        "reason": None if valid else "official_calendar_or_source_mismatch",
                        "date": session, "open": snapshot.get("open"), "close": snapshot.get("close"),
                        "source": snapshot.get("tw_price_source"), "calendar_status": status,
                        "session_complete": complete, "captured_at": updated_at}
            if valid:
                start = datetime.fromisoformat(session).date()
                for offset in range(1, 16):
                    target = (start + timedelta(days=offset)).isoformat()
                    next_status = calendar.session_status("TW", target)
                    if next_status.get("available") is not True:
                        break
                    if next_status.get("is_session") is True:
                        evidence["next_session_date"] = target
                        break
                evidence["sha256"] = sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row["shadow_price_evidence"] = evidence
    return output


def prepare_from_cache(reports_dir, rows, prices, updated_at):
    # Reuse the existing official calendar cache, fail closed if it is absent.
    calendar = OfficialMarketCalendar(reports_dir / "official_market_calendar.json",
                                     auto_refresh=False, allow_network=False)
    years = sorted({int(str(p.get("date"))[:4]) for p in (prices or {}).values() if str(p.get("date") or "")[:4].isdigit()})
    # One bounded official refresh when a required year is absent; never guess
    # holidays and never spin/retry a blocked source.
    for year in years:
        if not calendar.session_status("TW", f"{year}-01-02").get("available"):
            try:
                calendar.refresh([year])
            except Exception:
                pass  # Unavailable is retained on each row's evidence status.
    return prepare_rows(rows, prices or {}, calendar, updated_at)


def audit_model(model, references=None, calendar=None):
    """Derived attribution, keeping every raw day and position untouched."""
    excluded = {x["raw_index"] for x in model.get("session_validation", {}).get("excluded_days", [])}
    days = [d for i, d in enumerate(model.get("days", [])) if i not in excluded]
    stocks, dates, verified, missing, corrections = {}, [], [], [], []
    official_priced_count, official_revalued_gross = 0, 0.0
    for day in days:
        date = day["session_date"]
        for p in day.get("positions", []):
            if not p.get("data_available"):
                continue
            item = stocks.setdefault(p["symbol"], {"symbol": p["symbol"], "name": p.get("name"), "observations": 0, "gross_profit_twd": 0.0, "net_profit_twd": 0.0})
            item["observations"] += 1
            for key in ("gross_profit_twd", "net_profit_twd"):
                item[key] += float(p.get(key) or 0)
            proof = p.get("shadow_price_evidence") or {}
            reference = (references or {}).get(p["symbol"], {}).get(date) or {}
            price_match = reference.get("source") in {"TWSE", "TPEx"} and (reference.get("response_sha256") or reference.get("evidence_sha256")) and reference.get("open") == p.get("open_price") and reference.get("close") == p.get("sell_price")
            if evidence_valid(proof, date, p.get("open_price"), p.get("sell_price")) or price_match:
                verified.append(p)
                official_priced_count += 1
                official_revalued_gross += float(p.get("gross_profit_twd") or 0)
            else:
                known = reference.get("source") in {"TWSE", "TPEx"} and (reference.get("response_sha256") or reference.get("evidence_sha256"))
                missing.append({"date": date, "symbol": p["symbol"], "reason": "official_price_mismatch" if known else "historical_official_evidence_missing"})
                if known and reference.get("open") and reference.get("close"):
                    corrected = float(p.get("allocation_twd") or 0) * (reference["close"] / reference["open"] - 1)
                    official_priced_count += 1
                    official_revalued_gross += corrected
                    corrections.append({"date": date, "symbol": p["symbol"], "recorded_gross_profit_twd": p.get("gross_profit_twd"),
                                        "official_gross_profit_twd": round(corrected, 2),
                                        "delta_twd": round(corrected-float(p.get("gross_profit_twd") or 0), 2)})
        dates.append({"date": date, "gross_profit_twd": day.get("gross_profit_twd"), "net_profit_twd": day.get("net_profit_twd")})
    full = bool(verified) and not missing
    result = {"status": "prices_verified" if full else "incomplete",
            "raw_records_preserved": True, "verified_positions": len(verified), "unverified_positions": len(missing),
            "official_priced_positions": official_priced_count,
            "official_subset_revalued_gross_profit_twd": round(official_revalued_gross, 2) if official_priced_count else None,
            "verified_subset_gross_profit_twd": round(sum(float(p.get("gross_profit_twd") or 0) for p in verified), 2) if verified else None,
            "verified_portfolio_net_profit_twd": round(sum(float(d.get("net_profit_twd") or 0) for d in days), 2) if full else None,
            "unverified": missing,
            "confirmed_price_mismatches": corrections,
            "confirmed_price_error_delta_twd": round(sum(x["delta_twd"] for x in corrections), 2),
            "worst_stocks": sorted(stocks.values(), key=lambda x: x["gross_profit_twd"]),
            "worst_dates": sorted(dates, key=lambda x: x["gross_profit_twd"] or 0),
            "attribution_basis": "original_recorded_prices_not_fully_verified",
            "horizon_comparison": compare_horizons(days, references or {}, calendar),
            "cost_policy": "legacy_0.685pct_preserved_no_posthoc_discount",
            "promotion_allowed": False,
            "signal_timestamp_verified": False,
            "legacy_cost_components": {"fee_pct": .285, "tax_pct": .3, "slippage_pct": .1,
                                       "actual_cost_verified": False, "reason": "broker_fee_and_day_trade_eligibility_not_saved"}}
    return result


def compare_horizons(days, references, calendar):
    result = {"benchmark_symbol": "0050.TW", "basis": "same_frozen_picks_official_open_to_horizon_close",
              "promotion_allowed": False, "horizons": {}}
    sessions = sorted((calendar or {}).get("sessions") or [])
    as_of = (calendar or {}).get("completed_through") or ""
    for horizon in (1, 5, 20):
        observations, blocked = [], []
        for day in days:
            entry_date = day["session_date"]
            if entry_date not in sessions:
                blocked.append({"date": entry_date, "reason": "official_calendar_missing"}); continue
            index = sessions.index(entry_date) + horizon - 1
            if index >= len(sessions) or sessions[index] > as_of:
                blocked.append({"date": entry_date, "reason": "not_matured"}); continue
            exit_date = sessions[index]
            for p in day.get("positions", []):
                if not p.get("data_available"):
                    continue
                bars = references.get(p["symbol"], {})
                start, end = bars.get(entry_date) or {}, bars.get(exit_date) or {}
                if not all(b.get("source") in {"TWSE", "TPEx"} and (b.get("response_sha256") or b.get("evidence_sha256")) for b in (start, end)):
                    blocked.append({"date": entry_date, "symbol": p["symbol"], "reason": "official_horizon_prices_missing"}); continue
                o, c = start.get("open"), end.get("close")
                if not o or not c:
                    continue
                observation = {"date": entry_date, "symbol": p["symbol"], "exit_date": exit_date,
                               "gross_return_pct": round((c/o-1)*100, 4)}
                benchmark = references.get("0050.TW", {})
                bstart, bend = benchmark.get(entry_date) or {}, benchmark.get(exit_date) or {}
                if all(b.get("source") == "TWSE" and (b.get("response_sha256") or b.get("evidence_sha256")) for b in (bstart, bend)) and bstart.get("open", 0) > 0 and bend.get("close", 0) > 0:
                    observation["benchmark_gross_return_pct"] = round((bend["close"] / bstart["open"] - 1) * 100, 4)
                    observation["excess_return_pct"] = round(observation["gross_return_pct"] - observation["benchmark_gross_return_pct"], 4)
                observations.append(observation)
        benchmark_complete = bool(observations) and all("excess_return_pct" in p for p in observations)
        result["horizons"][str(horizon)] = {"verified_price_positions": len(observations),
            "average_gross_return_pct": round(sum(x["gross_return_pct"] for x in observations)/len(observations), 4) if observations else None,
            "observations": observations, "blocked": blocked,
            "complete_portfolio": bool(days) and not blocked,
            "benchmark_verified": benchmark_complete,
            "excess_return_pct": round(sum(p["excess_return_pct"] for p in observations)/len(observations), 4) if benchmark_complete else None,
            "comparison_status": "price_only_samples_benchmark_verified_no_strategy_verdict" if benchmark_complete else "partial_samples_only_benchmark_unavailable"}
    groups = [result["horizons"][str(h)]["observations"] for h in (1, 5, 20)]
    common = set.intersection(*[{(p["date"], p["symbol"]) for p in group} for group in groups])
    matched = [[p for p in group if (p["date"], p["symbol"]) in common] for group in groups]
    benchmark_complete = bool(common) and all("excess_return_pct" in p for group in matched for p in group)
    result["matched_sample_comparison"] = {"positions": len(common), "benchmark_verified": benchmark_complete,
        "average_gross_return_pct": {str(h): round(sum(p["gross_return_pct"] for p in group if (p["date"], p["symbol"]) in common)/len(common), 4) if common else None for h, group in zip((1, 5, 20), groups)},
        "benchmark_average_gross_return_pct": {str(h): round(sum(p["benchmark_gross_return_pct"] for p in group)/len(common), 4) if benchmark_complete else None for h, group in zip((1, 5, 20), matched)},
        "average_excess_return_pct": {str(h): round(sum(p["excess_return_pct"] for p in group)/len(common), 4) if benchmark_complete else None for h, group in zip((1, 5, 20), matched)},
        "status": "partial_same_cohort_prices_only_no_strategy_verdict"}
    return result
