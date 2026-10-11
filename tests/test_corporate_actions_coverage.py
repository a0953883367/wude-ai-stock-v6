import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.check_corporate_actions_coverage import expected_census, load_json_unique, validate_corporate_census
from watchlist import load_watchlist


class CorporateCensusTests(unittest.TestCase):
    def setUp(self):
        self.search = {"data": [
            {"代號": "1234.TW", "市場": "🇹🇼 台灣", "類型": "個股"},
            {"代號": "EXAMPLE_ETF", "市場": "🇺🇸 美國", "類型": "ETF"},
        ]}
        self.watch = [
            {"symbol": "1234.TW", "market": "TW", "type": "個股"},
            {"symbol": "REF", "market": "US", "type": "個股", "ranking_mode": "reference_only"},
        ]
        self.registry = {"records": {
            "1234.TW": {"symbol": "1234.TW", "market": "TW", "asset_type": "STOCK", "present": True},
            "EXAMPLE_ETF": {"symbol": "EXAMPLE_ETF", "market": "US", "asset_type": "ETF", "present": True},
            "REF": {"symbol": "REF", "market": "US", "asset_type": "STOCK", "present": True},
        }}
        self.report = {"summary": {
            "tracked_total": 3, "tracked_stocks": 2, "tracked_etfs": 1,
            "officially_matched": 3, "officially_matched_stocks": 2,
            "officially_matched_etfs": 1, "unmatched": 0,
            "degraded_sources": ["sec_registry"],
        }, "source_health": {"sec_registry": {"ok": False, "error": "blocked"}}}

    def check(self):
        return validate_corporate_census(self.search, self.watch, self.report, self.registry)

    def test_overlapping_sources_preserve_full_census_and_reference(self):
        before = copy.deepcopy((self.search, self.watch, self.report, self.registry))
        self.assertEqual(self.check()["tracked_total"], 3)
        self.assertEqual(before, (self.search, self.watch, self.report, self.registry))
        self.assertEqual(expected_census(self.search, self.watch)[("US", "REF")], "STOCK")

    def test_missing_identity_fails_even_with_forged_matching_counts(self):
        del self.registry["records"]["REF"]
        self.report["summary"].update(tracked_total=2, tracked_stocks=1, officially_matched=2, officially_matched_stocks=1)
        with self.assertRaisesRegex(ValueError, "missing"): self.check()

    def test_same_count_unexpected_identity_fails(self):
        row = self.registry["records"].pop("REF")
        row["symbol"] = "UNEXPECTED"
        self.registry["records"]["UNEXPECTED"] = row
        with self.assertRaisesRegex(ValueError, "unexpected"): self.check()

    def test_duplicate_source_rows_fail_before_deduplication(self):
        self.search["data"].append(dict(self.search["data"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"): self.check()
        self.search["data"].pop()
        self.watch.append(dict(self.watch[0]))
        with self.assertRaisesRegex(ValueError, "duplicate"): self.check()

    def test_duplicate_registry_identity_and_json_key_fail(self):
        self.registry["records"]["DUPLICATE"] = dict(self.registry["records"]["REF"])
        with self.assertRaisesRegex(ValueError, "duplicate"): self.check()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"records":{"REF":{},"REF":{}}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_json_unique(path)

    def test_stock_etf_misclassification_fails_even_when_counts_are_adjusted(self):
        self.registry["records"]["EXAMPLE_ETF"]["asset_type"] = "STOCK"
        self.report["summary"].update(tracked_stocks=3, tracked_etfs=0, officially_matched_stocks=3, officially_matched_etfs=0)
        with self.assertRaisesRegex(ValueError, "classification mismatch"): self.check()

    def test_conflicting_and_unknown_configured_classifications_fail(self):
        self.watch[0]["type"] = "ETF"
        with self.assertRaisesRegex(ValueError, "conflicting"): self.check()
        self.watch[0]["type"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown"): self.check()

    def test_summary_counters_and_integer_types_are_independently_checked(self):
        for field in self.report["summary"]:
            if field == "degraded_sources": continue
            old = self.report["summary"][field]
            for bad in (old + 1, float(old), True):
                self.report["summary"][field] = bad
                with self.assertRaises(ValueError): self.check()
            self.report["summary"][field] = old

    def test_unmatched_remains_visible_and_cannot_be_called_matched(self):
        self.registry["records"]["REF"]["present"] = False
        with self.assertRaisesRegex(ValueError, "officially_matched"): self.check()
        self.report["summary"].update(officially_matched=2, officially_matched_stocks=1, unmatched=1)
        self.assertEqual(self.check()["unmatched"], 1)
        self.assertEqual(self.report["source_health"]["sec_registry"]["ok"], False)
        self.assertEqual(self.report["summary"]["degraded_sources"], ["sec_registry"])

    def test_current_authoritative_source_union_is_complete_without_a_fixed_total(self):
        search = load_json_unique(Path("search_data.json"))
        watch = load_watchlist()
        expected = expected_census(search, watch)
        raw_symbols = {row["代號"] for row in search["data"]} | {row["symbol"] for row in watch}
        self.assertEqual({key[1] for key in expected}, raw_symbols)
        for row in search["data"]:
            key = ("TW" if "台灣" in row["市場"] else "US", row["代號"])
            self.assertEqual(expected[key], "ETF" if row["類型"] == "ETF" else "STOCK")
        for row in watch:
            self.assertEqual(expected[(row["market"], row["symbol"])], "ETF" if row["type"] == "ETF" else "STOCK")

    def test_workflow_preserves_existing_source_and_actual_coverage_guards(self):
        workflow = Path(".github/workflows/corporate-actions-shadow.yml").read_text()
        self.assertIn("if actual != wanted:", workflow)
        self.assertIn("if blocking_failed:", workflow)
        self.assertIn('degraded = set(summary.get("degraded_sources") or [])', workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertIn("validate_corporate_census(search, load_watchlist(), report, registry)", workflow)
        self.assertNotIn("configured 374-symbol", workflow)


if __name__ == "__main__":
    unittest.main()
