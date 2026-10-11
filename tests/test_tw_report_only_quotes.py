"""Synthetic fixtures only. All HTTP calls are blocked before running a test."""
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import tw_official_data as official
from tw_report_only_quotes import ReportOnlyQuoteCollector


TWSE_ROW = {
    "Code": "2412", "Date": "1150105", "OpeningPrice": "41",
    "HighestPrice": "44", "LowestPrice": "40", "ClosingPrice": "43.5",
    "TradeVolume": "1,234",
}
TPEX_ROW = {
    "SecuritiesCompanyCode": "00998A", "Date": "20260105", "Open": "11",
    "High": "14", "Low": "10", "Close": "13.5", "TradingShares": "4321",
}
OBSERVED_AT = "2026-01-06T04:00:00+08:00"


def fixture_get(url, *, params=None):
    """All numbers are artificial, not observations of the named securities."""
    if url.endswith("STOCK_DAY_ALL"):
        return [dict(TWSE_ROW), dict(TWSE_ROW, Code="2330")]
    if url.endswith("tpex_mainboard_daily_close_quotes"):
        return [dict(TPEX_ROW), dict(TPEX_ROW, SecuritiesCompanyCode="6488")]
    if url == official.TWSE_T86:
        return {"stat": "OK", "date": "20260105", "fields": ["證券代號", "三大法人買賣超股數"], "data": [["2330", "15"]]}
    if url.endswith("tpex_3insti_daily_trading"):
        return [{"SecuritiesCompanyCode": "6488", "Date": "1150105", "TotalDifference": "15"}]
    return []


class ReportOnlyTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "requests.sessions.Session.request"):
            blocker = patch(target, side_effect=AssertionError("network is forbidden in report-only tests"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def collect(self, rows, market="TWSE"):
        collector = ReportOnlyQuoteCollector()
        collector.observe(market, rows)
        return collector.snapshot(OBSERVED_AT)

    def record(self, rows, market="TWSE"):
        return self.collect(rows, market)["records"]["2412" if market == "TWSE" else "00998A"]

    def test_exact_scope_metadata_and_attribution(self):
        data = self.collect([TWSE_ROW])
        self.assertEqual(data["scope"], "report_only")
        self.assertFalse(data["formal_model_input"])
        self.assertFalse(data["ranking_eligible"])
        self.assertEqual(data["response_completeness"], "not_verified")
        record = data["records"]["2412"]
        self.assertEqual(record["code"], "2412")
        self.assertEqual(record["market"], "TWSE")
        self.assertEqual(record["instrument_type"], "ordinary_stock")
        self.assertEqual(record["price_basis"], "official_close")
        self.assertEqual(record["source_url"], official.TWSE_BASE + "/exchangeReport/STOCK_DAY_ALL")
        self.assertEqual(record["dataset_url"], "https://data.gov.tw/dataset/11549")
        self.assertEqual(record["license"]["url"], "https://data.gov.tw/license")
        self.assertIn("Taiwan Stock Exchange", record["attribution"])
        self.assertIsNone(record["dataset_version"])
        self.assertEqual(record["price_unit"], "TWD/share")
        self.assertEqual(record["volume_unit"], "shares")

    def test_etf_symbol_and_share_units_are_preserved(self):
        record = self.record([TPEX_ROW], "TPEx")
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["code"], "00998A")
        self.assertEqual(record["market"], "TPEx")
        self.assertEqual(record["instrument_type"], "ETF")
        self.assertEqual(record["price_unit"], "TWD/beneficial_unit")
        self.assertEqual(record["volume_unit"], "beneficial_units")
        self.assertEqual(record["source_url"], official.TPEX_BASE + "/tpex_mainboard_daily_close_quotes")
        self.assertEqual(record["dataset_url"], "https://data.gov.tw/dataset/11370")
        self.assertEqual(record["dataset_name"], "上櫃股票行情")
        self.assertEqual(record["data_providing_agency"], "金融監督管理委員會證券期貨局")
        self.assertEqual(record["attribution_year"], "2026")
        self.assertIn("Taipei Exchange", record["attribution"])
        self.assertEqual(record["raw_fields"]["volume"]["source_field"], "TradingShares")

    def test_normalization_trims_only_whitespace(self):
        for bad in ("998A", "00998", "00998a", "00998A.TWO", "00998AB", "０0998A", 998, None, "00 998A"):
            with self.subTest(code=bad):
                self.assertIsNone(self.record([dict(TPEX_ROW, SecuritiesCompanyCode=bad)], "TPEx")["quote"])
        record = self.record([dict(TPEX_ROW, SecuritiesCompanyCode=" 00998A\t")], "TPEx")
        self.assertEqual(record["code"], "00998A")
        self.assertEqual(record["raw_fields"]["code"]["raw_value"], " 00998A\t")

    def test_ordinary_symbol_requires_string_and_exact_id(self):
        for bad in (2412, "2412.TW", "02412", "2412A", "24120"):
            with self.subTest(code=bad):
                self.assertIsNone(self.record([dict(TWSE_ROW, Code=bad)])["quote"])

    def test_market_boundaries_and_esb_adr_never_added(self):
        data = self.collect([dict(TWSE_ROW, Code=code) for code in ("00998A", "7415", "7815", "HNHPF")])
        self.assertEqual(set(data["records"]), {"2412", "00998A"})
        self.assertTrue(all(item["quote"] is None for item in data["records"].values()))
        self.assertIsNone(self.record([dict(TPEX_ROW, SecuritiesCompanyCode="2412")], "TPEx")["quote"])

    def test_missing_optional_fields_remain_null(self):
        row = {"Code": "2412", "Date": "1150105", "ClosingPrice": "43.5"}
        record = self.record([row])
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["quote"], {"open": None, "high": None, "low": None, "close": 43.5, "volume": None})
        for key in ("open", "high", "low", "volume"):
            self.assertFalse(record["raw_fields"][key]["present"])
            self.assertFalse(record["raw_fields"][key]["valid"])

    def test_null_invalid_and_absent_evidence_are_distinct(self):
        record = self.record([dict(TWSE_ROW, OpeningPrice=None, HighestPrice="--", TradeVolume="N/A")])
        self.assertTrue(record["raw_fields"]["open"]["present"])
        self.assertIsNone(record["raw_fields"]["open"]["raw_value"])
        self.assertEqual(record["raw_fields"]["high"]["raw_value"], "--")
        self.assertIsNone(record["quote"]["open"])
        self.assertIsNone(record["quote"]["high"])
        self.assertIsNone(record["quote"]["volume"])

    def test_zero_volume_is_valid_only_when_explicit(self):
        for zero in ("0", 0, "0.0"):
            self.assertEqual(self.record([dict(TWSE_ROW, TradeVolume=zero)])["quote"]["volume"], 0)
        for bad in ("-1", "0.1", True, "1,23", "NaN", "Infinity", float("inf"), []):
            with self.subTest(volume=bad):
                self.assertIsNone(self.record([dict(TWSE_ROW, TradeVolume=bad)])["quote"]["volume"])

    def test_volume_is_exact_and_never_rounded_or_underflowed(self):
        record = self.record([dict(TWSE_ROW, TradeVolume="9007199254740993")])
        self.assertEqual(record["quote"]["volume"], 9007199254740993)
        for fractional in ("1.00000000000000001", "0." + "0" * 400 + "1"):
            record = self.record([dict(TWSE_ROW, TradeVolume=fractional)])
            self.assertIsNone(record["quote"]["volume"])
            self.assertFalse(record["raw_fields"]["volume"]["valid"])

    def test_extreme_numbers_remain_json_safe_and_do_not_poison_formal_fetch(self):
        for value in ("9" * 5000, 10 ** 5000):
            data = self.collect([dict(TWSE_ROW, TradeVolume=value)])
            self.assertIsNone(data["records"]["2412"]["quote"]["volume"])
            json.dumps(data, allow_nan=False)
        def oversized_report(url, *, params=None):
            rows = fixture_get(url, params=params)
            if url.endswith("STOCK_DAY_ALL"):
                rows[0]["TradeVolume"] = "9" * 5000
            return rows
        with tempfile.TemporaryDirectory() as temp, patch.object(official, "_get_json", oversized_report):
            path = Path(temp) / "official.json"
            result = official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
            self.assertIn("2330", result["prices"])
            self.assertEqual(json.loads(path.read_text())["data"], result)

    def test_non_utf8_raw_strings_do_not_poison_formal_fetch_or_cache(self):
        malformed = json.loads('"\\ud800"')
        data = self.collect([dict(TWSE_ROW, TradeVolume=malformed)])
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.assertEqual(data["records"]["2412"]["raw_fields"]["volume"]["raw_value"], {"invalid_type": "non_utf8_string"})
        def malformed_report(url, *, params=None):
            rows = fixture_get(url, params=params)
            if url.endswith("STOCK_DAY_ALL"):
                rows[0]["TradeVolume"] = malformed
            return rows
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "official.json"
            with patch.object(official, "_get_json", fixture_get):
                before = official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
            with patch.object(official, "_get_json", malformed_report):
                after = official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
            self.assertEqual(before, after)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["data"], before)

    def test_invalid_close_fails_closed_without_nav_fallback(self):
        for value in (None, "", "--", "0", "-1", True, "NaN", "1,23", "1e3", float("inf")):
            with self.subTest(close=value):
                record = self.record([dict(TPEX_ROW, Close=value, NAV="12.5")], "TPEx")
                self.assertEqual(record["status"], "invalid")
                self.assertIsNone(record["quote"])
        row = dict(TPEX_ROW, NAV="12.5")
        del row["Close"]
        self.assertIsNone(self.record([row], "TPEx")["quote"])

    def test_session_comes_only_from_date_field(self):
        record = self.record([TWSE_ROW])
        self.assertEqual(record["session_date"], "2026-01-05")
        self.assertEqual(record["raw_fields"]["session_date"], {"source_field": "Date", "present": True, "raw_value": "1150105", "valid": True})
        row = dict(TWSE_ROW, updated_at=OBSERVED_AT)
        del row["Date"]
        record = self.record([row])
        self.assertIsNone(record["session_date"])
        self.assertIsNone(record["quote"])

    def test_valid_calendar_dates_and_roc_formats(self):
        for raw, expected in (("115/01/05", "2026-01-05"), ("2026-01-05", "2026-01-05"), ("1130229", "2024-02-29")):
            self.assertEqual(self.record([dict(TWSE_ROW, Date=raw)])["session_date"], expected)

    def test_invalid_dates_and_timestamps_are_not_sessions(self):
        for raw in ("1150230", "20260229", "20260001", "20260132", "2026-1-5", "2026/01-05", OBSERVED_AT, "1150105T00:00:00", 20260105, None):
            with self.subTest(date=raw):
                record = self.record([dict(TWSE_ROW, Date=raw)])
                self.assertIsNone(record["session_date"])
                self.assertIsNone(record["quote"])

    def test_inconsistent_ohlc_is_rejected(self):
        for values in ({"HighestPrice": "42"}, {"LowestPrice": "44"}, {"OpeningPrice": "45"}):
            self.assertEqual(self.record([{**TWSE_ROW, **values}])["reason"], "inconsistent_raw_ohlc")

    def test_identical_duplicates_allowed_and_counted(self):
        record = self.record([TWSE_ROW, dict(TWSE_ROW)])
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["matched_row_count"], 2)

    def test_conflicting_duplicate_fields_fail_closed(self):
        for extra in ({"ClosingPrice": "42"}, {"Date": "1150102"}, {"TradeVolume": "0"}, {"HighestPrice": None}, {"ClosingPrice": "--"}):
            rows = [TWSE_ROW, {**TWSE_ROW, **extra}]
            first, reversed_record = self.record(rows), self.record(rows[::-1])
            self.assertEqual(first, reversed_record)
            self.assertEqual(first["status"], "conflict")
            self.assertIsNone(first["quote"])
            self.assertIsNone(first["raw_fields"])

    def test_unpreservable_duplicate_evidence_fails_closed(self):
        for left, right in (([1], [2]), ({"v": 1}, {"v": 2}), (float("nan"), float("inf")), ([1], [1])):
            record = self.record([dict(TWSE_ROW, TradeVolume=left), dict(TWSE_ROW, TradeVolume=right)])
            self.assertEqual(record["status"], "conflict")
            self.assertIsNone(record["quote"])
            self.assertIsNone(record["raw_fields"])

    def test_incomplete_duplicates_never_merge(self):
        second = dict(TWSE_ROW)
        del second["TradeVolume"]
        self.assertEqual(self.record([TWSE_ROW, second])["status"], "conflict")

    def test_malformed_response_is_not_partial_success(self):
        for rows in (None, {}, {"data": [TWSE_ROW]}, "[{", [TWSE_ROW, None], [TWSE_ROW, []]):
            with self.subTest(rows=rows):
                record = self.record(rows)
                self.assertEqual(record["reason"], "malformed_response")
                self.assertIsNone(record["quote"])

    def test_empty_and_truncated_lists_never_claim_completeness(self):
        data = self.collect([])
        self.assertEqual(data["records"]["2412"]["reason"], "empty_or_unavailable")
        partial = self.collect([dict(TWSE_ROW, Code="2330")])
        self.assertEqual(partial["records"]["2412"]["reason"], "absent_from_received_rows")
        self.assertEqual(partial["response_completeness"], "not_verified")
        one_row = self.collect([TWSE_ROW])
        self.assertEqual(one_row["responses"]["TWSE"]["completeness"], "not_verified")

    def test_collector_never_mutates_input_and_snapshot_is_detached(self):
        rows = [dict(TWSE_ROW)]
        original = copy.deepcopy(rows)
        collector = ReportOnlyQuoteCollector()
        collector.observe("TWSE", rows)
        self.assertEqual(rows, original)
        first = collector.snapshot(OBSERVED_AT)
        first["records"]["2412"]["quote"]["close"] = 999
        self.assertEqual(collector.snapshot(OBSERVED_AT)["records"]["2412"]["quote"]["close"], 43.5)

    def test_reobserving_or_failed_refresh_never_reuses_old_quote(self):
        collector = ReportOnlyQuoteCollector()
        collector.observe("TWSE", [TWSE_ROW])
        failed = collector.snapshot(OBSERVED_AT, refresh_failed=True)
        self.assertEqual(failed["refresh_status"], "failed")
        self.assertTrue(all(row["quote"] is None for row in failed["records"].values()))
        collector.observe("TWSE", [])
        self.assertIsNone(collector.snapshot(OBSERVED_AT)["records"]["2412"]["quote"])

    def test_deterministic_and_strict_json(self):
        first = self.collect([dict(TWSE_ROW, TradeVolume=float("nan"))])
        second = self.collect([dict(TWSE_ROW, TradeVolume=float("nan"))])
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)

    def test_same_response_parser_result_and_requests_unchanged(self):
        for parser, ids in ((official._parse_twse, {"2330"}), (official._parse_tpex, {"6488"})):
            calls = []
            def fake(url, *, params=None):
                calls.append((url, params))
                return fixture_get(url, params=params)
            with patch.object(official, "_get_json", fake):
                expected = parser(ids)
                expected_calls = list(calls)
                calls.clear()
                collector = ReportOnlyQuoteCollector()
                actual = parser(ids, report_only=collector)
            self.assertEqual(actual, expected)
            self.assertEqual(len(actual), 5)
            self.assertCountEqual(calls, expected_calls)
            self.assertEqual(len(calls), 6 if parser == official._parse_twse else 7)
            self.assertEqual(set(actual[0]), ids)
            self.assertEqual(ids, {"2330"} if parser == official._parse_twse else {"6488"})

    def test_full_fetch_cache_only_sidecar_and_consumer_ignores_it(self):
        universe = [{"symbol": "2330.TW", "market": "TW"}, {"symbol": "6488.TWO", "market": "TW"}, {"symbol": "HNHPF", "market": "US"}, {"symbol": "7415", "market": "TW"}, {"symbol": "7815", "market": "TW"}]
        original = copy.deepcopy(universe)
        with tempfile.TemporaryDirectory() as temp, patch.object(official, "_get_json", fixture_get):
            path = Path(temp) / "official.json"
            result = official.fetch_taiwan_official_data(universe, path)
            payload = json.loads(path.read_text())
        self.assertEqual(universe, original)
        self.assertEqual(result, payload["data"])
        self.assertNotIn("report_only_quotes", result)
        self.assertEqual(set(result["prices"]), {"2330", "6488"})
        self.assertEqual(result["institution_status"]["expected_count"], 2)
        self.assertTrue(all(row["status"] == "available" for row in payload["report_only_quotes"]["records"].values()))
        self.assertEqual(set(result), {"prices", "institutions", "credit", "fundamentals", "announcements", "institution_status"})
        # Existing consumers select data; an unknown top-level key is isolated.
        legacy_payload = {key: value for key, value in payload.items() if key != "report_only_quotes"}
        self.assertEqual(payload.get("data") or {}, legacy_payload.get("data") or {})

    def test_empty_market_does_not_trigger_new_request(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(official, "_get_json", side_effect=fixture_get) as get:
            path = Path(temp) / "official.json"
            official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
            payload = json.loads(path.read_text())
        self.assertEqual(get.call_count, 6)
        self.assertTrue(all("tpex.org.tw" not in call.args[0] for call in get.call_args_list))
        self.assertEqual(payload["report_only_quotes"]["records"]["00998A"]["status"], "not_observed")

    def test_failure_invalidates_sidecar_without_changing_formal_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "official.json"
            old = {"updated_at": "2025-01-01T00:00:00+08:00", "data": {"prices": {"2330": {"close": 9}}, "fundamentals": {"2330": {"per": 20}}}, "report_only_quotes": self.collect([TWSE_ROW])}
            path.write_text(json.dumps(old))
            with patch.object(official, "_parse_twse", side_effect=RuntimeError("offline")):
                result = official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
            payload = json.loads(path.read_text())
        self.assertEqual(payload["data"], old["data"])
        self.assertEqual(payload["updated_at"], old["updated_at"])
        self.assertEqual(result["prices"], {})
        self.assertEqual(result["fundamentals"], old["data"]["fundamentals"])
        self.assertEqual(payload["report_only_quotes"]["refresh_status"], "failed")
        self.assertTrue(all(item["quote"] is None for item in payload["report_only_quotes"]["records"].values()))

    def test_failed_atomic_invalidation_preserves_original_bytes(self):
        for phase in ("write", "replace"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "official.json"
                old = {"updated_at": "2025-01-01", "data": {"fundamentals": {"2330": {"per": 20}}}, "report_only_quotes": self.collect([TWSE_ROW])}
                before = json.dumps(old).encode()
                path.write_bytes(before)
                def partial_write(payload, stream, **kwargs):
                    stream.write('{"partial":')
                    raise OSError("simulated full disk")
                failure = patch.object(official.json, "dump", partial_write) if phase == "write" else patch.object(Path, "replace", side_effect=OSError("simulated replace failure"))
                with failure, patch.object(official, "_parse_twse", side_effect=RuntimeError("offline")):
                    result = official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(result["fundamentals"], old["data"]["fundamentals"])
                self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_successful_refresh_does_not_carry_stale_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "official.json"
            path.write_text(json.dumps({"data": {}, "report_only_quotes": self.collect([TWSE_ROW])}))
            def missing_report(url, *, params=None):
                rows = fixture_get(url, params=params)
                return [row for row in rows if row.get("Code") != "2412"] if isinstance(rows, list) else rows
            with patch.object(official, "_get_json", missing_report):
                official.fetch_taiwan_official_data([{"symbol": "2330.TW", "market": "TW"}], path)
            payload = json.loads(path.read_text())
        self.assertIsNone(payload["report_only_quotes"]["records"]["2412"]["quote"])

    def test_documented_consumer_rejects_malformed_cache_and_invalid_close(self):
        from datetime import datetime
        docs = (Path(__file__).resolve().parents[1] / "docs/report_only_quotes.md").read_text()
        example = docs.split("```python\n", 1)[1].split("```", 1)[0]
        snapshot = self.collect([TPEX_ROW], "TPEx")
        def read_close(payload):
            namespace = {"payload": payload, "expected_session": "2026-01-05", "freshness_cutoff": datetime.fromisoformat("2026-01-06T00:00:00+08:00")}
            exec(compile(example, "report_only_quotes.md", "exec"), namespace)
            return namespace["close"]
        self.assertEqual(read_close({"report_only_quotes": snapshot}), 13.5)
        for payload in (None, [], {"report_only_quotes": []}, {"report_only_quotes": "invalid"}):
            self.assertIsNone(read_close(payload))
        for key in ("records", "observed_at"):
            for invalid in ([], ["invalid"], "invalid", 1):
                data = copy.deepcopy(snapshot)
                data[key] = invalid
                self.assertIsNone(read_close({"report_only_quotes": data}))
        for key in ("quote", "raw_fields"):
            data = copy.deepcopy(snapshot)
            data["records"]["00998A"][key] = ["invalid"]
            self.assertIsNone(read_close({"report_only_quotes": data}))
        for value in (True, 0, -1, "13.5", float("nan"), float("inf"), 10 ** 400):
            data = copy.deepcopy(snapshot)
            data["records"]["00998A"]["quote"]["close"] = value
            self.assertIsNone(read_close({"report_only_quotes": data}))
        for field in ("close", "session_date"):
            data = copy.deepcopy(snapshot)
            data["records"]["00998A"]["raw_fields"][field]["valid"] = False
            self.assertIsNone(read_close({"report_only_quotes": data}))

    def test_bad_json_fails_with_only_existing_retry_count(self):
        with patch.object(requests, "get") as get, patch.object(official.time, "sleep"):
            get.return_value.json.side_effect = ValueError("truncated JSON")
            rows = official._safe_rows(official.TWSE_BASE + "/exchangeReport/STOCK_DAY_ALL")
        self.assertEqual(get.call_count, len(official.OFFICIAL_REQUEST_RETRY_DELAYS))
        self.assertEqual(rows, [])
        self.assertIsNone(self.record(rows)["quote"])


if __name__ == "__main__":
    unittest.main()
