"""Offline synthetic fixtures only; no real task provenance or market snapshots."""
from copy import deepcopy
from collections import Counter
import hashlib
import json
import socket
import unittest
from unittest.mock import patch

from public_research_briefing_contract import (
    ContractError, GATES, GEOMETRY_FIELDS, HORIZONS, PUBLIC_RELEASE_POLICY,
    build_public_research_briefing, build_canonical_public_research_briefing, render_research_status_preview,
)

NOW = "2025-01-02T10:00:00Z"
BATCH = "2025-01-02 16:00:00"  # Explicit Asia/Taipei => 08:00 UTC.
EVALUATED = "2025-01-02T09:00:00Z"
TAINT = "TAINT_PRICE_98765.4321_BUY_NOW"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def entry(market="TW", symbol="1000.TW", reference=False):
    security = {"market": market, "symbol": symbol, "name": TAINT, "theme": TAINT}
    if reference:
        security["ranking_mode"] = "reference_only"
    return {"key": f"{market}:{symbol}", "security": security,
            "coverage_role": "reference_only" if reference else "per_symbol", "source_groups": [TAINT]}


def manifests(canonical_entries=None, supplement_entries=None):
    ce = deepcopy(canonical_entries if canonical_entries is not None else [
        entry(), entry("US", "AAA"), entry("US", "BBB", True)])
    se = deepcopy(supplement_entries if supplement_entries is not None else [entry("US", "CCC")])
    for e in se:
        e.pop("source_groups", None)
    refs = sum(e["coverage_role"] == "reference_only" for e in ce)
    source = {"repository": "a0953883367/wude-ai-stock-v6", "path": "watchlist.py",
              "git_blob_sha1": "1" * 40, "sha256": "2" * 64}
    canonical = {"schema_version": 1, "scope": "canonical_watchlist_only", "source": source,
                 "identity_fields": ["market", "symbol"],
                 "source_groups": [{"name": TAINT, "source_row_count": len(ce)}],
                 "counts": {"source_rows": len(ce), "unique_entries": len(ce), "duplicate_rows": 0,
                            "per_symbol_required": len(ce) - refs, "reference_only": refs,
                            "by_market": dict(Counter(e["security"]["market"] for e in ce))},
                 "universe_sha256": digest(ce), "entries": ce}
    supplement = {"schema_version": 1, "scope": "task_report_supplement",
                  "source": {"verification": "stored_prompt_readback", "task_scope": "noon_only",
                             "readback_at": "2025-01-02T07:00:00Z",
                             "tasks": [{"task_id": "synthetic-task", "prompt_sha256": "3" * 64}]},
                  "entry_count": len(se), "entries_sha256": digest(se), "entries": se}
    entries = deepcopy(ce)
    indexed = {e["key"]: e for e in entries}
    for e in entries:
        e["origins"] = ["canonical_watchlist"]
    overlap = 0
    for addition in se:
        if addition["key"] in indexed:
            overlap += 1
            indexed[addition["key"]]["origins"].append("task_supplement")
        else:
            e = deepcopy(addition)
            e.update(source_groups=[], origins=["task_supplement"])
            entries.append(e)
            indexed[e["key"]] = e
    refs = sum(e["coverage_role"] == "reference_only" for e in entries)
    inputs = {"canonical_manifest_sha256": digest(canonical), "task_supplement_sha256": digest(supplement)}
    universe = {"schema_version": 1, "scope": "report_union", "source": deepcopy(source),
                "supplement_source": deepcopy(supplement["source"]), "inputs": inputs,
                "inputs_sha256": digest(inputs), "identity_fields": ["market", "symbol"],
                "counts": {"canonical_entries": len(ce), "supplement_entries": len(se),
                           "overlapping_entries": overlap, "unique_entries": len(entries),
                           "per_symbol_required": len(entries) - refs, "reference_only": refs,
                           "by_market": dict(sorted(Counter(e["security"]["market"] for e in entries).items()))},
                "universe_sha256": digest(entries), "entries": entries}
    return universe, canonical, supplement


def research_row(identity, failed_gate=None):
    market, symbol = identity["security"]["market"], identity["security"]["symbol"]
    row = {"market": market, "symbol": symbol, "session_date": "2025-01-02", "price": 102.0,
           "name": TAINT, "summary": TAINT, "source": TAINT, "forecasts": TAINT,
           "formal_score": 98765.4321, "plans": {}}
    for horizon in HORIZONS:
        plan = {"horizon": horizon, "active_entry_plan": False, "entry_low": 100.0, "entry_high": 101.0,
                "stop": 98.0, "target1": 104.0, "target2": 106.0, "do_not_chase_above": 103.0,
                "action": TAINT, "recommendation": TAINT, "forecast_probability_pct": 99.9}
        frozen = {"market": market, "symbol": symbol, "horizon": horizon,
                  "source_session_date": row["session_date"], "source_batch_at": BATCH,
                  "source_price": row["price"], "levels": {k: plan[k] for k in ("entry_low", "entry_high", "stop", "target1", "target2")}}
        c = {"version": "SHADOW-STOCK-CONCLUSION-V2", "mode": "plan_only", "horizon": horizon,
             "shadow_only": True, "automatic_orders": False, "code": "wait", "label": TAINT,
             "as_of": row["session_date"], "evaluated_at": EVALUATED,
             "expires_at": "2025-01-03T08:00:00Z", "valid_through_session": "2025-01-03",
             "entry_evaluation": {"status": "not_evaluated", "eligible": None, "reason": TAINT},
             "plan_snapshot": {"id": digest(frozen), **frozen, "scope": "frozen_within_this_report_not_a_prospective_registration"},
             "gates": [{"code": g, "passed": g != failed_gate, "reason": TAINT} for g in GATES],
             "data_status": {"detail": TAINT}, "reasons": [TAINT]}
        plan["conclusion"] = c
        row["plans"][horizon] = plan
    return row


def artifact(entries):
    return {"schema_version": 4, "model_version": "TRADE-PLAN-SHADOW-V4", "mode": "shadow_plan_only",
            "source_model_version": "CENTRAL-DECISION-HUB-V6", "updated_at": BATCH,
            "source_decision_hub_updated_at": BATCH, "evaluated_at": EVALUATED,
            "entry_evaluation": {"status": "not_evaluated"}, "summary": TAINT,
            "plans": [research_row(e) for e in entries]}


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.u, self.c, self.s = manifests()
        self.a = artifact(self.u["entries"])

    def build(self, **kwargs):
        return build_public_research_briefing(self.u, self.a, canonical_manifest=self.c,
                                             task_supplement=self.s, as_of=kwargs.get("as_of", NOW))

    def assert_suppressed(self, output):
        self.assertEqual(output["counts"]["eligible_actions"], 0)
        self.assertEqual(output["counts"]["numeric_release_rows"], 0)
        for row in output["rows"]:
            self.assertIsNone(row["action"])
            self.assertIs(row["decision_eligible"], False)
            for p in row["plans"].values():
                self.assertEqual(p["geometry"], dict.fromkeys(GEOMETRY_FIELDS))
                self.assertEqual(p["market_values"], {"price": None})
                self.assertIsNone(p["action"])
                self.assertIs(p["decision_eligible"], False)

    def test_canonical_count_metadata_cannot_smuggle_prices_or_strings(self):
        for bad_counts in ({**self.c["counts"], "price": TAINT}, {**self.c["counts"], "reference_only": True}):
            canonical = deepcopy(self.c)
            canonical["counts"] = bad_counts
            with self.assertRaises(ContractError):
                build_canonical_public_research_briefing(canonical, self.a, as_of=NOW)

    def test_canonical_entry_point_excludes_private_supplement_and_provenance(self):
        out = build_canonical_public_research_briefing(self.c, self.a, as_of=NOW)
        self.assertEqual(out["scope"], "canonical_watchlist_only")
        self.assertNotIn("task_scope", out)
        self.assertEqual(len(out["rows"]), 3)
        self.assertNotIn("US:CCC", [r["key"] for r in out["rows"]])
        self.assertEqual(set(out["provenance"]), {"canonical_manifest_sha256", "universe_sha256",
                         "artifact_sha256", "source_batch_at", "artifact_evaluated_at"})
        self.assertNotIn("task_supplement", json.dumps(out))
        self.assertNotIn("午報", render_research_status_preview(out))
        self.assert_suppressed(out)

    def test_extreme_timezone_dates_do_not_crash_census(self):
        for value in ("0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59", "0001-01-01 00:00:00"):
            self.a["evaluated_at"] = value
            self.a["updated_at"] = value
            out = self.build()
            self.assertEqual(len(out["rows"]), 4)
            self.assertIn("artifact_time_invalid", out["rows"][0]["plans"]["short"]["reason_codes"])

    def test_taipei_date_projection_overflow_fails_closed(self):
        far = "9999-12-31T23:59:59Z"
        self.a.update(updated_at=far, source_decision_hub_updated_at=far, evaluated_at=far)
        for row in self.a["plans"]:
            row["session_date"] = "9999-12-31"
            for p in row["plans"].values():
                c = p["conclusion"]
                c.update(as_of="9999-12-31", evaluated_at=far, expires_at=far, valid_through_session="9999-12-31")
        out = self.build(as_of=far)
        self.assertEqual(len(out["rows"]), 4)
        self.assertIn("observation_time_invalid", out["rows"][0]["plans"]["short"]["reason_codes"])

    def test_identity_census_is_not_quote_or_plan_coverage(self):
        out = self.build()
        self.assertEqual(out["contract_kind"], "plan_eligibility_status_only")
        self.assertIs(out["quote_cache_replacement"], False)
        self.assertEqual(out["counts"]["identity_rows_accounted"], 4)
        self.assertIs(out["counts"]["identity_accounting_complete"], True)
        self.assertEqual(out["counts"]["qualified_plan_inputs"], 0)
        self.assertEqual(out["counts"]["upstream_data_gate_passed_plans"], 12)
        self.assertEqual(out["counts"]["suppressed_numeric_plans"], 12)
        self.assertEqual([r["key"] for r in out["rows"]], sorted(e["key"] for e in self.u["entries"]))
        self.assert_suppressed(out)

    def test_synthetic_eighteen_gate_patterns_remain_inactive(self):
        ce = [entry("US", "Z" + chr(65 + i)) for i in range(9)] + [entry("TW", f"{1100+i}.TW") for i in range(9)]
        self.u, self.c, self.s = manifests(ce, [deepcopy(ce[0])])
        self.a = artifact(self.u["entries"])
        for i, row in enumerate(self.a["plans"]):
            self.a["plans"][i] = research_row(ce[i], "source" if i < 9 else "price" if i < 15 else None)
        out = self.build()
        self.assertEqual(len(out["rows"]), 18)
        self.assertEqual(out["counts"]["qualified_plan_inputs"], 0)
        self.assertEqual(out["counts"]["upstream_data_gate_passed_plans"], 9)
        self.assert_suppressed(out)
        for row in out["rows"]:
            for p in row["plans"].values():
                self.assertIn("global_entry_not_evaluated", p["reason_codes"])
                self.assertIn("entry_not_evaluated", p["reason_codes"])
                self.assertIn("inactive_plan", p["reason_codes"])

    def test_reference_role_and_overlap_preserved(self):
        ce = [entry("US", "AAA", True)]
        self.u, self.c, self.s = manifests(ce, [entry("US", "AAA")])
        self.a = artifact(self.u["entries"])
        row = self.build()["rows"][0]
        self.assertEqual(row["coverage_role"], "reference_only")
        self.assertEqual(row["origins"], ["canonical_watchlist", "task_supplement"])
        self.assertIn("reference_only", row["plans"]["short"]["reason_codes"])

    def test_missing_duplicate_extra_and_invalid_rows_accounted(self):
        self.a["plans"].pop()
        self.a["plans"].append(deepcopy(self.a["plans"][0]))
        self.a["plans"].append(research_row(entry("US", "ZZZ")))
        self.a["plans"].append({"market": "US", "symbol": TAINT})
        c = self.build()["counts"]
        self.assertEqual(c["matched_research_rows"], 2)
        self.assertEqual(c["missing_research_rows"], 1)
        self.assertEqual(c["duplicate_research_identities"], 1)
        self.assertEqual(c["ignored_artifact_rows"], 1)
        self.assertEqual(c["invalid_artifact_rows"], 1)
        self.assertEqual(c["identity_rows_accounted"], 4)

    def test_all_sources_and_permission_claims_are_non_authoritative(self):
        for source in ("TWSE OpenAPI", "TPEx OpenAPI", "Alpaca SIP daily bars", "Yahoo Finance daily bars", TAINT):
            with self.subTest(source=source):
                self.a["public_permission"] = self.a["allow_public_numbers"] = True
                self.a["source"] = source
                self.a["entry_evaluation"] = {"status": "evaluated", "eligible": True}
                for row in self.a["plans"]:
                    row["public_redistribution_permitted"] = True
                    row["source"] = source
                    for p in row["plans"].values():
                        p["active_entry_plan"] = True
                        p["conclusion"]["entry_evaluation"] = {"status": "evaluated", "eligible": True}
                out = self.build()
                self.assert_suppressed(out)
                for row in out["rows"]:
                    if row["market"] == "US":
                        self.assertIn("us_numeric_prohibited", row["plans"]["short"]["reason_codes"])

    def test_contradictory_wait_avoid_insufficient_conclusions_never_qualify(self):
        self.a["entry_evaluation"] = {"status": "evaluated", "eligible": True}
        for code in ("wait", "avoid", "insufficient", TAINT, None):
            for row in self.a["plans"]:
                for p in row["plans"].values():
                    p["active_entry_plan"] = True
                    p["conclusion"]["entry_evaluation"] = {"status": "evaluated", "eligible": True}
                    p["conclusion"]["code"] = code
            out = self.build()
            self.assertEqual(out["counts"]["qualified_plan_inputs"], 0)
            self.assertIn("conclusion_not_eligible", out["rows"][0]["plans"]["short"]["reason_codes"])

    def test_global_not_evaluated_overrides_spoofed_row_evaluation(self):
        for row in self.a["plans"]:
            for p in row["plans"].values():
                p["active_entry_plan"] = True
                p["conclusion"]["entry_evaluation"] = {"status": "evaluated", "eligible": True}
        out = self.build()
        self.assert_suppressed(out)
        self.assertTrue(all("global_entry_not_evaluated" in r["plans"]["short"]["reason_codes"] for r in out["rows"]))

    def test_no_free_text_or_ranks_forecasts_prices_escape(self):
        out = self.build()
        serialized = json.dumps(out, ensure_ascii=False)
        for bad in (TAINT, "forecast_probability", "formal_score", "source_groups", "synthetic-task", "102.0", "100.0"):
            self.assertNotIn(bad, serialized)
        self.assertNotIn(TAINT, render_research_status_preview(out))
        self.assertNotIn("買進區", render_research_status_preview(out))

    def test_timestamps_normalize_taipei_without_refreshing_evidence(self):
        out = self.build()
        self.assertEqual(out["provenance"]["source_batch_at"], "2025-01-02T08:00:00Z")
        self.assertEqual(out["rows"][0]["plans"]["short"]["observed_at"], EVALUATED)
        later = self.build(as_of="2025-01-04T10:00:00Z")
        self.assertEqual(out["provenance"], later["provenance"])
        self.assertIn("expired", later["rows"][0]["plans"]["short"]["reason_codes"])

    def test_missing_malformed_future_and_incoherent_times_fail_closed(self):
        for key, value in (("evaluated_at", None), ("evaluated_at", "2025-01-02 09:00:00"),
                           ("evaluated_at", "2025-01-02T11:00:00Z"), ("updated_at", "2025-01-02 18:00:00"),
                           ("source_decision_hub_updated_at", "2025-01-02T07:00:00Z")):
            with self.subTest(key=key, value=value):
                original = deepcopy(self.a)
                self.a[key] = value
                out = self.build()
                self.assertIn("artifact_time_invalid", out["rows"][0]["plans"]["short"]["reason_codes"])
                self.assertEqual(out["counts"]["qualified_plan_inputs"], 0)
                self.assert_suppressed(out)
                self.a = original

    def test_missing_expiry_fails_closed_without_new_age_threshold(self):
        for row in self.a["plans"]:
            for p in row["plans"].values():
                p["conclusion"]["expires_at"] = None
        out = self.build()
        self.assertEqual(out["counts"]["qualified_plan_inputs"], 0)
        self.assertIn("expiry_missing_or_invalid", out["rows"][0]["plans"]["short"]["reason_codes"])

    def test_snapshot_hash_identity_levels_or_batch_drift_blocked(self):
        for key, value in (("id", "0" * 64), ("market", "US"), ("source_batch_at", "2025-01-02 15:00:00"),
                           ("source_price", "102.0"), ("source_session_date", "2025-01-01"), ("levels", {})):
            with self.subTest(key=key):
                original = deepcopy(self.a)
                p = self.a["plans"][0]["plans"]["short"]
                p["conclusion"]["plan_snapshot"][key] = value
                self.assertIn("source_provenance_invalid", self.build()["rows"][0]["plans"]["short"]["reason_codes"])
                self.a = original

    def test_each_explicit_gate_failure_preserved(self):
        for code in GATES:
            with self.subTest(code=code):
                self.a["plans"][0] = research_row(self.u["entries"][0], code)
                out = self.build()
                self.assertIn(f"gate_{code}_failed", out["rows"][0]["plans"]["short"]["reason_codes"])
                self.assertFalse(out["rows"][0]["plans"]["short"]["data_gates_passed"])

    def test_missing_duplicate_unknown_and_nonboolean_gates(self):
        good = deepcopy(self.a)
        for mode in ("missing", "duplicate", "unknown", "integer"):
            with self.subTest(mode=mode):
                self.a = deepcopy(good)
                g = self.a["plans"][0]["plans"]["short"]["conclusion"]["gates"]
                if mode == "missing": g.pop()
                if mode == "duplicate": g[-1] = deepcopy(g[0])
                if mode == "unknown": g[0]["code"] = TAINT
                if mode == "integer": g[0]["passed"] = 1
                self.assertIn("gate_contract_invalid", self.build()["rows"][0]["plans"]["short"]["reason_codes"])

    def test_artifact_schema_malformed_keeps_every_identity(self):
        for bad in (None, [], {}, {"plans": "wrong"}, {"plans": [], "secret": float("nan")}):
            with self.subTest(bad=type(bad).__name__):
                self.a = bad
                out = self.build()
                self.assertEqual(len(out["rows"]), 4)
                self.assert_suppressed(out)

    def test_version_mismatch_and_missing_conclusion(self):
        self.a["schema_version"] = 3
        self.a["plans"][0]["plans"]["short"]["conclusion"] = None
        p = self.build()["rows"][0]["plans"]["short"]
        self.assertIn("artifact_version_invalid", p["reason_codes"])
        self.assertIn("conclusion_invalid", p["reason_codes"])

    def test_manifest_hash_count_identity_origins_and_unknown_fields_rejected(self):
        original = deepcopy(self.u)
        mutations = [lambda u: u.update(universe_sha256="0" * 64),
                     lambda u: u["counts"].update(unique_entries=999),
                     lambda u: u["entries"].append(deepcopy(u["entries"][0])),
                     lambda u: u["entries"][0].update(origins=[TAINT]),
                     lambda u: u.update(extra=TAINT),
                     lambda u: u.update(schema_version=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                self.u = deepcopy(original)
                mutation(self.u)
                with self.assertRaises(ContractError): self.build()

    def test_manifest_duplicates_and_unsafe_identity_even_rehashed_rejected(self):
        for entries in ([entry(), entry()], [entry("US", TAINT)], [entry("US", "aaa")]):
            with self.subTest(entries=repr(entries)[:15]):
                self.u, self.c, self.s = manifests(entries)
                with self.assertRaises(ContractError): self.build()

    def test_separate_manifest_mismatch_detected(self):
        self.c["entries"][0]["security"]["name"] = "CHANGED"
        self.c["universe_sha256"] = digest(self.c["entries"])
        with self.assertRaisesRegex(ContractError, "union_mismatch"): self.build()

    def test_scope_and_readback_validation(self):
        for key, value in (("task_scope", "all_reports"), ("verification", "claimed"),
                           ("readback_at", "2025-01-02"), ("readback_at", "2030-01-02T00:00:00Z")):
            with self.subTest(key=key):
                u, c, s = manifests()
                s["source"][key] = value
                with self.assertRaises(ContractError):
                    build_public_research_briefing(u, self.a, canonical_manifest=c, task_supplement=s, as_of=NOW)

    def test_generation_time_must_be_explicit_timezone(self):
        for value in (None, "2025-01-02", "2025-01-02T10:00:00", TAINT):
            with self.assertRaisesRegex(ContractError, "generation_time_invalid"): self.build(as_of=value)

    def test_inputs_immutable_and_no_network_or_io(self):
        originals = deepcopy((self.u, self.c, self.s, self.a))
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), patch("builtins.open", side_effect=AssertionError("I/O forbidden")):
            first = self.build()
            second = self.build()
            self.assertEqual(first, second)
            render_research_status_preview(first)
        self.assertEqual(originals, (self.u, self.c, self.s, self.a))
        first["rows"][0]["origins"].append("mutated")
        self.assertEqual(originals, (self.u, self.c, self.s, self.a))

    def test_policy_not_mutable_or_user_overridable(self):
        with self.assertRaises(TypeError): PUBLIC_RELEASE_POLICY["numeric_release_enabled"] = True
        with self.assertRaises(TypeError):
            build_public_research_briefing(self.u, self.a, canonical_manifest=self.c, task_supplement=self.s,
                                           as_of=NOW, public_permission=True)

    def test_preview_deterministic_and_no_quotes_even_for_forged_numbers(self):
        out = self.build()
        expected = render_research_status_preview(out)
        out["rows"].reverse()
        for row in out["rows"]:
            row["name"] = TAINT
            for p in row["plans"].values():
                p["geometry"]["entry_low"] = 98765.4321
                p["action"] = TAINT
        self.assertEqual(expected, render_research_status_preview(out))
        self.assertIn("計畫資格與資料狀態", expected)
        self.assertIn("未取代另存的官方報價資料", expected)

    def test_preview_rejects_injected_identity_reason_origin(self):
        for field in ("symbol", "reason", "origins"):
            out = self.build()
            row = out["rows"][0]
            if field == "symbol": row["symbol"] = TAINT
            if field == "reason": row["plans"]["short"]["reason_codes"].append(TAINT)
            if field == "origins": row["origins"] = [TAINT]
            with self.assertRaisesRegex(ContractError, "preview_invalid"): render_research_status_preview(out)


if __name__ == "__main__":
    unittest.main()
