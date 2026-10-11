"""Pure, default-deny plan eligibility/status projection of frozen inputs.

No I/O, runtime imports, provider calls, ranking, recommendation, or activation.
All market/plan numbers are intentionally null in contract v1. See the companion
contract document for the trusted-input boundary and timestamp interpretation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import re
from types import MappingProxyType
from zoneinfo import ZoneInfo

PUBLIC_RELEASE_POLICY = MappingProxyType({
    "version": "status_only_v1", "numeric_release_enabled": False,
    "us_numeric_release_enabled": False, "action_release_enabled": False,
})
HORIZONS = ("short", "medium", "long")
GATES = ("market", "source", "source_date", "ohlcv", "calendar", "completed",
         "freshness", "price", "snapshot", "core_data", "evidence", "levels", "expiry")
GEOMETRY_FIELDS = ("entry_low", "entry_high", "do_not_chase_above", "stop", "target1", "target2")
REASONS = MappingProxyType({
    "public_numeric_release_disabled": "未開放公開數值",
    "us_numeric_prohibited": "美股數值不公開",
    "reference_only": "僅供對照",
    "artifact_invalid": "研究檔案格式無法確認",
    "artifact_version_invalid": "研究版本無法確認",
    "artifact_time_invalid": "研究批次時間無法確認",
    "global_entry_not_evaluated": "整體進場尚未評估",
    "missing_research": "尚無對應研究",
    "duplicate_research": "研究識別重複待核對",
    "missing_horizon": "此週期尚無研究",
    "conclusion_invalid": "研究結論格式無法確認",
    "source_provenance_invalid": "來源快照待核對",
    "observation_time_invalid": "觀察時間待核對",
    "expiry_missing_or_invalid": "有效期限待核對",
    "expired": "研究已超過有效期限",
    "gate_contract_invalid": "資料檢核格式無法確認",
    "inactive_plan": "計畫未啟用",
    "entry_not_evaluated": "進場尚未評估",
    "conclusion_not_eligible": "既有結論尚未符合資格",
    "existing_plan_blockers": "既有計畫品質或風險條件未通過",
    **{f"gate_{gate}_failed": label for gate, label in zip(GATES, (
        "市場資料待核對", "來源待核對", "來源日期待核對", "日線完整性待核對",
        "交易日曆待核對", "正式收盤待確認", "資料新鮮度待核對", "價格基準待核對",
        "批次一致性待核對", "關鍵資料待補齊", "證據待核對", "計畫幾何待核對", "失效時間待核對"))},
})
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_AWARE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class ContractError(ValueError):
    """Fixed error code only: never echo tainted input in errors."""


def _require(ok, code="manifest_invalid"):
    if not ok:
        raise ContractError(code)


def _digest(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise ContractError("input_not_json") from None


def _sha(value):
    return isinstance(value, str) and bool(_HEX64.fullmatch(value))


def _identity(value):
    if not isinstance(value, dict):
        return None
    market, symbol = value.get("market"), value.get("symbol")
    if not isinstance(symbol, str) or market not in ("TW", "US"):
        return None
    pattern = r"[0-9]{4,6}[A-Z]?\.TW(?:O)?" if market == "TW" else r"[A-Z]{1,6}(?:[.-][A-Z]{1,2})?"
    return (market, symbol) if re.fullmatch(pattern, symbol) else None


def _aware(value):
    if not isinstance(value, str) or not _AWARE.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _batch(value):
    aware = _aware(value)
    if aware:
        return aware
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Taipei")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, OverflowError):
        return None


def _taipei_date(value):
    try:
        return value.astimezone(ZoneInfo("Asia/Taipei")).date() if value else None
    except (ValueError, OverflowError):
        return None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def _entries(manifest, *, require_source_groups=True):
    _require(isinstance(manifest, dict))
    entries = manifest.get("entries")
    _require(isinstance(entries, list) and bool(entries))
    seen = set()
    for entry in entries:
        _require(isinstance(entry, dict))
        identity = _identity(entry.get("security"))
        _require(identity is not None)
        key = ":".join(identity)
        _require(entry.get("key") == key and key not in seen, "manifest_identity_invalid")
        seen.add(key)
        role = entry.get("coverage_role")
        _require(role in ("per_symbol", "reference_only"))
        _require((entry["security"].get("ranking_mode") == "reference_only") == (role == "reference_only"))
        _require((not require_source_groups and "source_groups" not in entry)
                 or isinstance(entry.get("source_groups"), list))
    return entries


def _validate_canonical(canonical):
    ce = _entries(canonical)
    _require(type(canonical.get("schema_version")) is int and canonical["schema_version"] == 1)
    _require(canonical.get("scope") == "canonical_watchlist_only")
    _require(canonical.get("identity_fields") == ["market", "symbol"])
    source = canonical.get("source")
    _require(isinstance(source, dict) and source.get("repository") == "a0953883367/wude-ai-stock-v6"
             and source.get("path") == "watchlist.py" and _sha(source.get("sha256"))
             and isinstance(source.get("git_blob_sha1"), str)
             and bool(re.fullmatch(r"[a-f0-9]{40}", source["git_blob_sha1"])))
    _require(canonical.get("universe_sha256") == _digest(ce), "manifest_hash_mismatch")
    counts = canonical.get("counts")
    _require(isinstance(counts, dict))
    refs = sum(e["coverage_role"] == "reference_only" for e in ce)
    expected_counts = {"unique_entries": len(ce), "per_symbol_required": len(ce) - refs,
                       "reference_only": refs, "by_market": dict(Counter(e["security"]["market"] for e in ce))}
    _require(all(counts.get(k) == v for k, v in expected_counts.items()), "manifest_count_mismatch")
    _require(set(counts) == set(expected_counts) | {"source_rows", "duplicate_rows"}
             and all(type(counts[k]) is int for k in counts if k != "by_market")
             and all(type(v) is int for v in counts["by_market"].values()), "manifest_count_mismatch")
    _require(type(counts.get("source_rows")) is int and type(counts.get("duplicate_rows")) is int
             and counts["duplicate_rows"] >= 0
             and counts["source_rows"] == len(ce) + counts["duplicate_rows"], "manifest_count_mismatch")
    _digest(canonical)
    return ce


def _validate_universe(universe, canonical, supplement, as_of):
    """Exact union reconstruction; no runtime/watchlist import or I/O."""
    ce, se = _validate_canonical(canonical), _entries(supplement, require_source_groups=False)
    source = canonical["source"]
    _require(type(supplement.get("schema_version")) is int and supplement["schema_version"] == 1
             and supplement.get("scope") == "task_report_supplement")
    ss = supplement.get("source")
    _require(isinstance(ss, dict) and ss.get("verification") == "stored_prompt_readback")
    readback = _aware(ss.get("readback_at"))
    _require(readback is not None and readback <= as_of)
    # Only the presently attested task scope is supported, never inferred for other reports.
    _require(ss.get("task_scope") == "noon_only", "supplement_scope_invalid")
    tasks = ss.get("tasks")
    _require(isinstance(tasks, list) and bool(tasks))
    ids = set()
    for task in tasks:
        _require(isinstance(task, dict) and isinstance(task.get("task_id"), str)
                 and bool(task["task_id"].strip()) and task["task_id"] not in ids
                 and _sha(task.get("prompt_sha256")))
        ids.add(task["task_id"])
    _require(supplement.get("entries_sha256") == _digest(se), "manifest_hash_mismatch")
    _require(type(supplement.get("entry_count")) is int and supplement["entry_count"] == len(se), "manifest_count_mismatch")
    entries = deepcopy(ce)
    by_key = {e["key"]: e for e in entries}
    for entry in entries:
        entry["origins"] = ["canonical_watchlist"]
    overlap = 0
    for addition in se:
        if addition["key"] in by_key:
            overlap += 1
            by_key[addition["key"]]["origins"].append("task_supplement")
        else:
            entry = deepcopy(addition)
            entry.update(source_groups=[], origins=["task_supplement"])
            entries.append(entry)
            by_key[entry["key"]] = entry
    inputs = {"canonical_manifest_sha256": _digest(canonical), "task_supplement_sha256": _digest(supplement)}
    refs = sum(e["coverage_role"] == "reference_only" for e in entries)
    expected = {
        "schema_version": 1, "scope": "report_union", "source": source,
        "supplement_source": ss, "inputs": inputs, "inputs_sha256": _digest(inputs),
        "identity_fields": ["market", "symbol"], "universe_sha256": _digest(entries),
        "counts": {"canonical_entries": len(ce), "supplement_entries": len(se),
                   "overlapping_entries": overlap, "unique_entries": len(entries),
                   "per_symbol_required": len(entries) - refs, "reference_only": refs,
                   "by_market": dict(sorted(Counter(e["security"]["market"] for e in entries).items()))},
        "entries": entries,
    }
    _require(universe == expected and _digest(universe) == _digest(expected), "union_mismatch")
    # Hashing the complete union also rejects non-JSON hidden metadata.
    _digest(universe)
    return sorted(entries, key=lambda e: e["key"])


def _global_state(artifact, as_of):
    reasons = []
    if not isinstance(artifact, dict):
        return ["artifact_invalid"], None, None
    if (type(artifact.get("schema_version")) is not int or artifact["schema_version"] != 4
            or artifact.get("model_version") != "TRADE-PLAN-SHADOW-V4"
            or artifact.get("source_model_version") != "CENTRAL-DECISION-HUB-V6"
            or artifact.get("mode") != "shadow_plan_only"):
        reasons.append("artifact_version_invalid")
    updated = _batch(artifact.get("updated_at"))
    source = _batch(artifact.get("source_decision_hub_updated_at"))
    evaluated = _aware(artifact.get("evaluated_at"))
    if not (updated and source and evaluated and updated == source and source <= evaluated <= as_of):
        reasons.append("artifact_time_invalid")
    # Status contract never upgrades the existing global entry evaluation.
    evaluation = artifact.get("entry_evaluation")
    if not (isinstance(evaluation, dict) and evaluation.get("status") == "evaluated"
            and evaluation.get("eligible") is True):
        reasons.append("global_entry_not_evaluated")
    return reasons, source, evaluated


def _plan_status(row, horizon, common, batch_at, artifact_at, as_of):
    reasons = list(common)
    result = {"status": "blocked", "reason_codes": reasons,
              "data_gates_passed": False, "source_session_date": None,
              "observed_at": None, "expires_at": None,
              "market_values": {"price": None},
              "geometry": dict.fromkeys(GEOMETRY_FIELDS),
              "action": None, "decision_eligible": False}
    if row is None:
        return result
    plans = row.get("plans")
    plan = plans.get(horizon) if isinstance(plans, dict) else None
    if not isinstance(plan, dict):
        reasons.append("missing_horizon")
        return result
    if plan.get("active_entry_plan") is not True:
        reasons.append("inactive_plan")
    quality = plan.get("plan_quality")
    if (not isinstance(quality, dict) or quality.get("entry_eligible") is not True
            or row.get("risk_blocks") != [] or row.get("unresolved_conflicts") != []
            or plan.get("no_buy_reason")):
        reasons.append("existing_plan_blockers")
    c = plan.get("conclusion")
    if not isinstance(c, dict):
        reasons.append("conclusion_invalid")
        return result
    if (c.get("version") != "SHADOW-STOCK-CONCLUSION-V2" or c.get("mode") != "plan_only"
            or c.get("horizon") != horizon or plan.get("horizon") != horizon
            or c.get("shadow_only") is not True or c.get("automatic_orders") is not False):
        reasons.append("conclusion_invalid")
    if c.get("code") != "eligible":
        reasons.append("conclusion_not_eligible")
    evaluation = c.get("entry_evaluation")
    if not (isinstance(evaluation, dict) and evaluation.get("status") == "evaluated"
            and evaluation.get("eligible") is True):
        reasons.append("entry_not_evaluated")
    observed, expires = _aware(c.get("evaluated_at")), _aware(c.get("expires_at"))
    session, valid_through = _date(row.get("session_date")), _date(c.get("valid_through_session"))
    snapshot = c.get("plan_snapshot")
    provenance_ok = False
    if isinstance(snapshot, dict):
        keys = ("market", "symbol", "horizon", "source_session_date", "source_batch_at", "source_price", "levels")
        frozen = {k: snapshot.get(k) for k in keys}
        expected_levels = {k: plan.get(k) for k in ("entry_low", "entry_high", "stop", "target1", "target2")}
        provenance_ok = (snapshot.get("scope") == "frozen_within_this_report_not_a_prospective_registration"
                         and snapshot.get("id") == _digest(frozen)
                         and snapshot.get("market") == row.get("market")
                         and snapshot.get("symbol") == row.get("symbol")
                         and snapshot.get("horizon") == horizon
                         and snapshot.get("source_session_date") == row.get("session_date")
                         and snapshot.get("levels") == expected_levels
                         and snapshot.get("source_price") == row.get("price")
                         and batch_at is not None and _batch(snapshot.get("source_batch_at")) == batch_at)
    if not provenance_ok:
        reasons.append("source_provenance_invalid")
    batch_date = _taipei_date(batch_at)
    observation_ok = (session and _date(c.get("as_of")) == session and batch_at and observed and artifact_at
                      and batch_date and session <= batch_date
                      and batch_at <= observed <= artifact_at <= as_of)
    if not observation_ok:
        reasons.append("observation_time_invalid")
    else:
        result.update(source_session_date=session.isoformat(), observed_at=_iso(observed))
    expiry_ok = expires and observed and valid_through and session and expires >= observed and valid_through >= session
    if not expiry_ok:
        reasons.append("expiry_missing_or_invalid")
    else:
        result["expires_at"] = _iso(expires)
        if as_of >= expires:
            reasons.append("expired")
    gates = c.get("gates")
    gate_map = {}
    gate_valid = isinstance(gates, list) and len(gates) == len(GATES)
    if gate_valid:
        for gate in gates:
            if (not isinstance(gate, dict) or gate.get("code") not in GATES
                    or gate["code"] in gate_map or type(gate.get("passed")) is not bool):
                gate_valid = False
                break
            gate_map[gate["code"]] = gate["passed"]
    if not gate_valid or set(gate_map) != set(GATES):
        reasons.append("gate_contract_invalid")
    else:
        reasons.extend(f"gate_{code}_failed" for code in GATES if not gate_map[code])
        result["data_gates_passed"] = all(gate_map.values())
    result["reason_codes"] = list(dict.fromkeys(reasons))
    return result


def build_public_research_briefing(universe, artifact, *, canonical_manifest, task_supplement, as_of):
    """LOCAL-ONLY union projection; no upstream field can enable release.

    The caller attests the frozen canonical/task inputs. Hashes detect drift;
    they do not establish task authorization. Never publish a private union.
    """
    now = _aware(as_of)
    _require(now is not None, "generation_time_invalid")
    entries = _validate_universe(universe, canonical_manifest, task_supplement, now)
    return _project(entries, artifact, now,
                    {"scope": "report_union", "task_scope": "noon_only"}, universe["counts"],
                    {"union_manifest_sha256": _digest(universe),
                     "universe_sha256": universe["universe_sha256"],
                     "inputs_sha256": universe["inputs_sha256"], **universe["inputs"]})


def build_canonical_public_research_briefing(canonical_manifest, artifact, *, as_of):
    """Public candidate limited to the public canonical manifest; pure, no I/O.

    No task supplement is synthesized or read. This is identity/status coverage,
    not a replacement for the independently maintained official quote cache.
    """
    now = _aware(as_of)
    _require(now is not None, "generation_time_invalid")
    entries = sorted(_validate_canonical(canonical_manifest), key=lambda e: e["key"])
    entries = [{**entry, "origins": ["canonical_watchlist"]} for entry in entries]
    return _project(entries, artifact, now, {"scope": "canonical_watchlist_only"},
                    canonical_manifest["counts"],
                    {"canonical_manifest_sha256": _digest(canonical_manifest),
                     "universe_sha256": canonical_manifest["universe_sha256"]})


def _project(entries, artifact, now, scope_info, census_counts, input_provenance):
    common, batch_at, artifact_at = _global_state(artifact, now)
    try:
        artifact_digest = _digest(artifact)
    except ContractError:
        artifact_digest = None
        common = ["artifact_invalid"]
        artifact = None
    raw_rows = artifact.get("plans") if isinstance(artifact, dict) else None
    if not isinstance(raw_rows, list):
        raw_rows = []
        common.append("artifact_invalid")
    index = {}
    invalid_count = 0
    for row in raw_rows:
        identity = _identity(row)
        if identity is None:
            invalid_count += 1
        else:
            index.setdefault(":".join(identity), []).append(row)
    rows = []
    matched = missing = duplicate = 0
    for entry in entries:
        candidates = index.get(entry["key"], [])
        row = candidates[0] if len(candidates) == 1 else None
        status = "research_status_only"
        reasons = ["public_numeric_release_disabled", *common]
        if entry["security"]["market"] == "US":
            reasons.append("us_numeric_prohibited")
        if entry["coverage_role"] == "reference_only":
            status = "reference_only"
            reasons.append("reference_only")
        if not candidates:
            missing += 1
            reasons.append("missing_research")
        elif len(candidates) > 1:
            duplicate += 1
            reasons.append("duplicate_research")
        else:
            matched += 1
        plans = {h: _plan_status(row, h, reasons, batch_at, artifact_at, now) for h in HORIZONS}
        rows.append({"key": entry["key"], "market": entry["security"]["market"],
                     "symbol": entry["security"]["symbol"], "coverage_role": entry["coverage_role"],
                     "origins": list(entry["origins"]), "status": status,
                     "action": None, "decision_eligible": False, "plans": plans})
    known = {entry["key"] for entry in entries}
    return {"schema_version": 1, "contract": "PUBLIC-RESEARCH-STATUS-V1",
            **scope_info, "generated_at": _iso(now),
            "contract_kind": "plan_eligibility_status_only", "quote_cache_replacement": False,
            "release_policy": dict(PUBLIC_RELEASE_POLICY),
            "provenance": {**input_provenance, "artifact_sha256": artifact_digest,
                           "source_batch_at": _iso(batch_at) if "artifact_time_invalid" not in common else None,
                           "artifact_evaluated_at": _iso(artifact_at) if "artifact_time_invalid" not in common else None},
            "counts": {**deepcopy(census_counts), "output_rows": len(rows),
                       "matched_research_rows": matched, "missing_research_rows": missing,
                       "duplicate_research_identities": duplicate,
                       "identity_rows_accounted": matched + missing + duplicate,
                       "identity_accounting_complete": matched + missing + duplicate == len(rows),
                       "upstream_data_gate_passed_plans": sum(p["data_gates_passed"] for row in rows for p in row["plans"].values()),
                       "qualified_plan_inputs": sum(
                           p["data_gates_passed"] and all(
                               r in ("public_numeric_release_disabled", "us_numeric_prohibited")
                               for r in p["reason_codes"])
                           for row in rows for p in row["plans"].values()),
                       "suppressed_numeric_plans": sum(
                           "missing_research" not in p["reason_codes"]
                           and "duplicate_research" not in p["reason_codes"]
                           and "missing_horizon" not in p["reason_codes"]
                           for row in rows for p in row["plans"].values()),
                       "ignored_artifact_rows": sum(len(v) for k, v in index.items() if k not in known),
                       "invalid_artifact_rows": invalid_count, "eligible_actions": 0,
                       "numeric_release_rows": 0}, "rows": rows}


def render_research_status_preview(briefing):
    """Identity-sorted fixed plaintext, with no upstream free text or numbers.

    This renderer never prints geometry even if passed a forged contract-shaped
    object. Invalid identity/reason codes reject with a fixed error.
    """
    _require(isinstance(briefing, dict) and briefing.get("contract") == "PUBLIC-RESEARCH-STATUS-V1", "preview_invalid")
    scope = briefing.get("scope")
    _require(scope in ("canonical_watchlist_only", "report_union"), "preview_invalid")
    heading = "原始清單範圍" if scope == "canonical_watchlist_only" else "午報範圍，僅限本機"
    rows = briefing.get("rows")
    _require(isinstance(rows, list), "preview_invalid")
    validated = []
    seen = set()
    for row in rows:
        identity = _identity(row)
        _require(identity is not None, "preview_invalid")
        key = ":".join(identity)
        _require(row.get("key") == key and key not in seen, "preview_invalid")
        seen.add(key)
        _require(row.get("coverage_role") in ("per_symbol", "reference_only"), "preview_invalid")
        origins = row.get("origins")
        _require(origins in (["canonical_watchlist"], ["task_supplement"], ["canonical_watchlist", "task_supplement"]), "preview_invalid")
        _require(scope != "canonical_watchlist_only" or origins == ["canonical_watchlist"], "preview_invalid")
        reasons = set()
        plans = row.get("plans")
        _require(isinstance(plans, dict) and set(plans) == set(HORIZONS), "preview_invalid")
        for plan in plans.values():
            _require(isinstance(plan, dict) and isinstance(plan.get("reason_codes"), list), "preview_invalid")
            for code in plan["reason_codes"]:
                _require(isinstance(code, str) and code in REASONS, "preview_invalid")
                reasons.add(code)
        role = "對照" if row["coverage_role"] == "reference_only" else "逐項研究"
        origin = "原始清單＋午報補充" if len(origins) == 2 else ("原始清單" if origins[0] == "canonical_watchlist" else "午報補充")
        labels = "；".join(REASONS[c] for c in REASONS if c in reasons)
        validated.append((key, f"{key}｜{origin}｜{role}｜{labels}"))
    return "\n".join([f"計畫資格與資料狀態（{heading}）", "未經驗證；不提供買進指示或可下單價位。",
                      "本表僅核對清單身分與計畫狀態；未取代另存的官方報價資料。",
                      f"清單身分共 {len(rows)} 項；此表公開計畫數值 0 項；可執行指示 0 項。",
                      *(text for _, text in sorted(validated))]) + "\n"
