"""The offline canonical census must not silently lose any current member."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import watchlist
import watchlist_manifest as wm


class WatchlistManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = wm.build_manifest()

    def report(self):
        return {
            "universe_sha256": self.manifest["universe_sha256"],
            "source_sha256": self.manifest["source"]["sha256"],
            "rows": [
                {
                    "market": entry["security"]["market"],
                    "symbol": entry["security"]["symbol"],
                    "status": "reference_only" if entry["coverage_role"] == "reference_only" else "analyzed",
                }
                for entry in self.manifest["entries"]
            ],
        }

    def test_checked_in_artifact_is_exact_and_deterministic(self):
        self.assertEqual(wm.MANIFEST_PATH.read_text(encoding="utf-8"), wm.render_manifest(self.manifest))
        self.assertEqual(self.manifest, wm.build_manifest())

    def test_all_loaded_rows_and_original_order_are_preserved(self):
        self.assertEqual([e["security"] for e in self.manifest["entries"]], watchlist.load_watchlist())
        self.assertEqual(len(self.manifest["entries"]), len(watchlist.load_watchlist()))
        self.assertEqual(self.manifest["counts"]["duplicate_rows"], 0)
        self.assertEqual(self.manifest["scope"], "canonical_watchlist_only")

    def test_provenance_matches_source_bytes_and_git_blob_identity(self):
        source = Path(watchlist.__file__).read_bytes()
        self.assertEqual(self.manifest["source"]["sha256"], hashlib.sha256(source).hexdigest())
        blob = b"blob " + str(len(source)).encode() + b"\0" + source
        self.assertEqual(self.manifest["source"]["git_blob_sha1"], hashlib.sha1(blob).hexdigest())

    def test_all_extend_groups_are_discovered_and_accounted_for(self):
        groups = wm._source_groups(Path(watchlist.__file__).read_bytes())
        self.assertEqual(sum(len(keys) for _, keys in groups), len(watchlist.load_watchlist()))
        self.assertEqual([e["key"] for e in self.manifest["entries"]], [k for _, keys in groups for k in keys])
        for name, keys in groups:
            self.assertEqual(keys, [e["key"] for e in self.manifest["entries"] if name in e["source_groups"]])

    def test_unknown_runtime_addition_fails_closed(self):
        rows = watchlist.load_watchlist() + [{"market": "US", "symbol": "UNKNOWN"}]
        with patch.object(watchlist, "load_watchlist", return_value=rows):
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                wm.build_manifest()

    def test_truncated_runtime_pool_fails_closed(self):
        with patch.object(watchlist, "load_watchlist", return_value=watchlist.load_watchlist()[:81]):
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                wm.build_manifest()

    def test_unsupported_extend_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            wm._source_groups(b"WATCHLIST = []\nWATCHLIST.extend(fetch_remote())\n")

    def test_market_and_suffix_remain_part_of_identity(self):
        self.assertNotEqual(wm.identity({"market": "TW", "symbol": "SAME"}), wm.identity({"market": "US", "symbol": "SAME"}))
        self.assertNotEqual(wm.identity({"market": "TW", "symbol": "3713.TW"}), wm.identity({"market": "TW", "symbol": "3713.TWO"}))
        self.assertEqual(wm.identity({"market": " tw ", "symbol": "2327.tw "}), ("TW", "2327.TW"))

    def test_reference_metadata_is_preserved_without_eligibility_inference(self):
        ref = next(e for e in self.manifest["entries"] if e["key"] == "US:HNHPF")
        self.assertEqual(ref["coverage_role"], "reference_only")
        self.assertEqual(ref["security"]["ranking_mode"], "reference_only")
        self.assertEqual(ref["security"]["primary_symbol"], "2317.TW")
        self.assertNotIn("rankable", json.dumps(self.manifest))
        self.assertNotIn("buy_eligible", json.dumps(self.manifest))
        counts = self.manifest["counts"]
        self.assertEqual(counts["per_symbol_required"] + counts["reference_only"], counts["unique_entries"])
        self.assertEqual(sum(counts["by_market"].values()), counts["unique_entries"])

    def test_complete_report_passes(self):
        self.assertTrue(wm.check_report_coverage(self.report(), self.manifest)["complete"])

    def test_every_current_symbol_is_required_including_reference(self):
        for index, entry in enumerate(self.manifest["entries"]):
            with self.subTest(key=entry["key"]):
                report = self.report()
                del report["rows"][index]
                result = wm.check_report_coverage(report, self.manifest)
                self.assertFalse(result["complete"])
                self.assertEqual(result["missing"], [entry["key"]])

    def test_same_count_substitution_is_rejected(self):
        report = self.report()
        old = wm._key(report["rows"][0])
        report["rows"][0]["symbol"] = "NOT_IN_CANONICAL"
        result = wm.check_report_coverage(report, self.manifest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], [old])
        self.assertEqual(len(result["unexpected"]), 1)

    def test_duplicate_rows_cannot_pad_coverage_count(self):
        report = self.report()
        report["rows"].append(copy.deepcopy(report["rows"][0]))
        result = wm.check_report_coverage(report, self.manifest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["duplicates"], [wm._key(report["rows"][0])])

    def test_reference_cannot_be_marked_analyzed(self):
        report = self.report()
        next(r for r in report["rows"] if r["symbol"] == "HNHPF")["status"] = "analyzed"
        self.assertFalse(wm.check_report_coverage(report, self.manifest)["complete"])

    def test_unavailable_is_accounted_for_only_with_reason(self):
        report = self.report()
        report["rows"][0].update(status="unavailable", reason="No reliable authorized data")
        self.assertTrue(wm.check_report_coverage(report, self.manifest)["complete"])
        for reason in (None, "", " ", 42):
            report["rows"][0]["reason"] = reason
            self.assertFalse(wm.check_report_coverage(report, self.manifest)["complete"])

    def test_unexpected_prompt_extras_are_not_canonical(self):
        report = self.report()
        report["rows"].append({"market": "US", "symbol": "PROMPT_ONLY", "status": "analyzed"})
        result = wm.check_report_coverage(report, self.manifest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["unexpected"], ["US:PROMPT_ONLY"])

    def test_stale_or_missing_attestation_fails(self):
        for field in ("source_sha256", "universe_sha256"):
            report = self.report()
            del report[field]
            self.assertFalse(wm.check_report_coverage(report, self.manifest)["complete"])
            report[field] = "0" * 64
            self.assertFalse(wm.check_report_coverage(report, self.manifest)["complete"])

    def test_invalid_rows_and_statuses_fail_without_silent_drops(self):
        for invalid in (None, {}, {"market": "EU", "symbol": "A"}, {"market": "US", "symbol": 123}, {"market": "US", "symbol": ""}):
            report = self.report()
            report["rows"][0] = invalid
            self.assertFalse(wm.check_report_coverage(report, self.manifest)["complete"])
        report = self.report()
        report["rows"][0]["status"] = "eligible"
        self.assertFalse(wm.check_report_coverage(report, self.manifest)["complete"])

    def test_non_object_report_and_non_list_rows_fail(self):
        for report in ([], {"rows": None}):
            with self.assertRaises(ValueError):
                wm.check_report_coverage(report, self.manifest)

    def test_manifest_has_no_provider_prices_scores_or_signals(self):
        forbidden = {"price", "buy_price", "score", "rank", "signal", "provider", "token"}
        for entry in self.manifest["entries"]:
            self.assertFalse(forbidden & entry.keys())
            self.assertFalse(forbidden & entry["security"].keys())

    def test_check_cli_passes_without_network_or_optional_dependencies(self):
        result = subprocess.run([sys.executable, str(wm.ROOT / "watchlist_manifest.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
