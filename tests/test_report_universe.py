"""Synthetic task provenance only; real task IDs/prompts are private inputs."""

import copy
import unittest

import report_universe as ru
import watchlist_manifest as wm


class ReportUniverseTests(unittest.TestCase):
    def setUp(self):
        self.canonical = wm.build_manifest()
        entries = [
            {"key": "US:SYNTHETIC_EXTRA", "coverage_role": "per_symbol", "security": {"market": "US", "symbol": "SYNTHETIC_EXTRA", "type": "個股"}},
            {"key": "TW:SYNTHETIC_EXTRA", "coverage_role": "per_symbol", "security": {"market": "TW", "symbol": "SYNTHETIC_EXTRA", "type": "ETF"}},
        ]
        self.supplement = {
            "schema_version": 1,
            "scope": "task_report_supplement",
            "source": {
                "verification": "stored_prompt_readback",
                "readback_at": "2026-10-11T00:00:00Z",
                "task_scope": "synthetic_test_only",
                "tasks": [{"task_id": "synthetic-task", "prompt_sha256": ru.manifest_sha256("synthetic fixture")}],
            },
            "entry_count": len(entries),
            "entries_sha256": ru.manifest_sha256(entries),
            "entries": entries,
        }

    def build(self):
        return ru.build_report_universe(self.canonical, self.supplement)

    def refresh_supplement(self):
        self.supplement["entry_count"] = len(self.supplement["entries"])
        self.supplement["entries_sha256"] = ru.manifest_sha256(self.supplement["entries"])

    def report(self, universe):
        return {
            "source_sha256": universe["source"]["sha256"],
            "universe_sha256": universe["universe_sha256"],
            "inputs_sha256": universe["inputs_sha256"],
            "rows": [{
                "market": e["security"]["market"], "symbol": e["security"]["symbol"],
                "status": "reference_only" if e["coverage_role"] == "reference_only" else "analyzed",
            } for e in universe["entries"]],
        }

    def test_union_counts_are_dynamic_and_inputs_are_unmodified(self):
        before = copy.deepcopy((self.canonical, self.supplement))
        union = self.build()
        self.assertEqual(before, (self.canonical, self.supplement))
        self.assertEqual(union["counts"]["unique_entries"], len(self.canonical["entries"]) + len(self.supplement["entries"]))
        self.assertEqual(union["counts"]["per_symbol_required"], self.canonical["counts"]["per_symbol_required"] + len(self.supplement["entries"]))
        self.assertEqual(union["counts"]["reference_only"], self.canonical["counts"]["reference_only"])
        self.assertEqual(union["scope"], "report_union")

    def test_same_symbol_in_different_markets_is_not_collapsed(self):
        keys = {e["key"] for e in self.build()["entries"]}
        self.assertTrue({"TW:SYNTHETIC_EXTRA", "US:SYNTHETIC_EXTRA"} <= keys)

    def test_canonical_order_metadata_and_origins_are_preserved(self):
        union = self.build()
        for expected, actual in zip(self.canonical["entries"], union["entries"]):
            self.assertEqual(actual["security"], expected["security"])
            self.assertEqual(actual["key"], expected["key"])
            self.assertEqual(actual["origins"], ["canonical_watchlist"])
        for extra in union["entries"][len(self.canonical["entries"]):]:
            self.assertEqual(extra["origins"], ["task_supplement"])

    def test_overlap_does_not_duplicate_or_promote_reference(self):
        self.supplement["entries"].append({"key": "US:HNHPF", "coverage_role": "per_symbol", "security": {"market": "US", "symbol": "HNHPF"}})
        self.refresh_supplement()
        union = self.build()
        ref = next(e for e in union["entries"] if e["key"] == "US:HNHPF")
        self.assertEqual(ref["coverage_role"], "reference_only")
        self.assertEqual(ref["security"]["ranking_mode"], "reference_only")
        self.assertEqual(ref["origins"], ["canonical_watchlist", "task_supplement"])
        self.assertEqual(union["counts"]["overlapping_entries"], 1)
        self.assertEqual(union["counts"]["unique_entries"], len(self.canonical["entries"]) + 2)

    def test_each_union_member_is_required_including_supplement(self):
        union = self.build()
        for i, entry in enumerate(union["entries"]):
            with self.subTest(key=entry["key"]):
                report = self.report(union)
                del report["rows"][i]
                result = wm.check_report_coverage(report, union)
                self.assertFalse(result["complete"])
                self.assertEqual(result["missing"], [entry["key"]])

    def test_full_union_coverage_passes(self):
        union = self.build()
        self.assertTrue(wm.check_report_coverage(self.report(union), union)["complete"])

    def test_union_requires_both_input_and_universe_attestations(self):
        union = self.build()
        report = self.report(union)
        del report["inputs_sha256"]
        self.assertFalse(wm.check_report_coverage(report, union)["complete"])
        report["inputs_sha256"] = "0" * 64
        self.assertFalse(wm.check_report_coverage(report, union)["complete"])

    def test_changed_prompt_provenance_changes_inputs_hash(self):
        original = self.build()
        self.supplement["source"]["tasks"][0]["prompt_sha256"] = "1" * 64
        changed = self.build()
        self.assertEqual(original["universe_sha256"], changed["universe_sha256"])
        self.assertNotEqual(original["inputs_sha256"], changed["inputs_sha256"])
        self.assertFalse(wm.check_report_coverage(self.report(original), changed)["complete"])

    def test_union_exact_validation_rejects_drift_in_any_field(self):
        union = self.build()
        ru.validate_report_universe(union, self.canonical, self.supplement)
        variants = []
        altered = copy.deepcopy(union); altered["entries"].pop(); variants.append(altered)
        altered = copy.deepcopy(union); altered["entries"][0]["origins"] = ["task_supplement"]; variants.append(altered)
        altered = copy.deepcopy(union); altered["counts"]["per_symbol_required"] += 1; variants.append(altered)
        altered = copy.deepcopy(union); altered["inputs_sha256"] = "0" * 64; variants.append(altered)
        altered = copy.deepcopy(union); altered["supplement_source"]["task_scope"] = "another_task"; variants.append(altered)
        for altered in variants:
            with self.assertRaises(ValueError):
                ru.validate_report_universe(altered, self.canonical, self.supplement)

    def test_invalid_input_manifest_hashes_and_counts_fail(self):
        for target, field in ((self.canonical, "universe_sha256"), (self.supplement, "entries_sha256")):
            old = target[field]
            target[field] = "0" * 64
            with self.assertRaises(ValueError): self.build()
            target[field] = old
        self.canonical["counts"]["reference_only"] += 1
        with self.assertRaises(ValueError): self.build()

    def test_duplicate_supplement_identity_fails_instead_of_silent_loss(self):
        self.supplement["entries"].append(copy.deepcopy(self.supplement["entries"][0]))
        self.refresh_supplement()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.build()

    def test_missing_provenance_timezone_and_hash_fail(self):
        for key, invalid in (("verification", "unverified"), ("readback_at", "2026-10-11"), ("tasks", [])):
            source = self.supplement["source"]
            original = source[key]
            source[key] = invalid
            with self.assertRaises(ValueError): self.build()
            source[key] = original
        self.supplement["source"]["tasks"][0]["prompt_sha256"] = "unverified"
        with self.assertRaises(ValueError): self.build()

    def test_supplement_reference_status_requires_matching_metadata(self):
        self.supplement["entries"][0]["coverage_role"] = "reference_only"
        self.refresh_supplement()
        with self.assertRaisesRegex(ValueError, "reference role"):
            self.build()

    def test_boolean_and_float_cannot_impersonate_integer_schema_or_counts(self):
        for value in (True, 1.0):
            union = self.build()
            union["schema_version"] = value
            with self.assertRaises(ValueError):
                ru.validate_report_universe(union, self.canonical, self.supplement)
            union = self.build()
            union["counts"]["reference_only"] = value
            with self.assertRaises(ValueError):
                ru.validate_report_universe(union, self.canonical, self.supplement)
            self.canonical["schema_version"] = value
            with self.assertRaises(ValueError): self.build()
            self.canonical["schema_version"] = 1
            self.supplement["schema_version"] = value
            with self.assertRaises(ValueError): self.build()
            self.supplement["schema_version"] = 1
        self.canonical["counts"]["reference_only"] = True
        with self.assertRaises(ValueError): self.build()


if __name__ == "__main__":
    unittest.main()
