from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from weekly_shadow_coach import CoachBlocked, build_weekly_coach


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _learning() -> dict:
    return {
        "updated_at": "2026-09-06 20:00:00",
        "progress": {
            "trading_days_collected": 10,
            "promotion_review_days": 60,
        },
        "error_learning": {
            "raw_error_rows": 12,
            "independent_events": 3,
            "duplicate_rows_collapsed": 9,
            "cause_counts": {"direction_calibration": 2, "event_gap_risk": 1},
            "recent_events": [{
                "event_id": "TW:2330.TW:2026-09-01:2026-09-05",
                "market": "TW",
                "symbol": "2330.TW",
                "cohort": "TW_STOCK",
                "source_start_date": "2026-09-01",
                "evaluated_end_date": "2026-09-05",
                "primary_cause": "direction_calibration",
            }],
        },
        "signal_health": {
            "TW_STOCK": {"direction_samples": 10, "trade_signal_samples": 0}
        },
        "policy": {
            "formal_v6_frozen": True,
            "formal_ranking_unchanged": True,
            "formal_weights_unchanged": True,
            "historical_predictions_never_rewritten": True,
            "broker_orders": False,
        },
    }


def _backup() -> dict:
    return {
        "created_at": "2026-09-06 20:00:00",
        "status": "ok",
        "verified": True,
        "private_backup": True,
        "formal_v6_modified": False,
        "automatic_orders": False,
        "public_database_exposed": False,
        "table_counts": {"predictions": 100, "prices": 20},
    }


def _archive() -> dict:
    return {
        "status": "ok",
        "counts": {"total": 59, "uploaded": 3, "verified_existing": 56, "errors": 0},
    }


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        result = {
            "headline": "本週只做影子診斷",
            "root_causes": [],
            "priority_actions": ["繼續前向驗證"],
            "data_gaps": [],
            "confidence_notes": ["樣本不足"],
        }
        return {
            "id": "resp_test",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(result)}],
            }],
        }


class _Session:
    def __init__(self) -> None:
        self.request = {}

    def post(self, url: str, **kwargs):
        self.request = {"url": url, **kwargs}
        return _Response()


class WeeklyShadowCoachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reports = self.root / "reports"
        self.archive = self.root / "google_drive_archive_result.json"
        _write(self.reports / "model_learning.json", _learning())
        _write(self.reports / "prediction_evidence_backup_health.json", _backup())
        _write(self.archive, _archive())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_dry_run_uses_verified_cloud_archive_and_stays_shadow_only(self) -> None:
        formal = self.root / "formal_v6.json"
        ranking = self.root / "rankings.json"
        _write(formal, {"locked": True})
        _write(ranking, {"rank": 1})
        before = (formal.read_bytes(), ranking.read_bytes())

        report = build_weekly_coach(
            self.reports,
            mode="dry_run",
            archive_result=self.archive,
            generated_at="2026-09-06T19:00:00Z",
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["source"]["cloud_archive"]["total"], 59)
        self.assertEqual(report["source"]["independent_events"], 3)
        self.assertFalse(report["api"]["called"])
        self.assertTrue(report["policy"]["formal_v6_unchanged"])
        self.assertFalse(report["policy"]["automatic_orders"])
        self.assertEqual(before, (formal.read_bytes(), ranking.read_bytes()))
        self.assertTrue((self.reports / "weekly_shadow_coach.json").exists())
        self.assertTrue((self.reports / "weekly_shadow_coach_history.json").exists())

    def test_cloud_archive_error_blocks_coaching(self) -> None:
        broken = _archive()
        broken["counts"]["errors"] = 1
        _write(self.archive, broken)
        with self.assertRaisesRegex(CoachBlocked, "archive verification"):
            build_weekly_coach(
                self.reports,
                mode="dry_run",
                archive_result=self.archive,
            )

    def test_missing_safety_lock_blocks_coaching(self) -> None:
        learning = _learning()
        learning["policy"]["formal_weights_unchanged"] = False
        _write(self.reports / "model_learning.json", learning)
        with self.assertRaisesRegex(CoachBlocked, "safety lock"):
            build_weekly_coach(
                self.reports,
                mode="dry_run",
                archive_result=self.archive,
            )

    def test_openai_mode_uses_structured_output_and_does_not_store_response(self) -> None:
        session = _Session()
        report = build_weekly_coach(
            self.reports,
            mode="openai",
            archive_result=self.archive,
            api_key="test-key",
            model="test-model",
            session=session,
        )
        body = session.request["json"]
        self.assertFalse(body["store"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertNotIn("test-key", json.dumps(body))
        self.assertTrue(report["api"]["called"])
        self.assertEqual(report["api"]["response_id"], "resp_test")

    def test_workflow_defaults_to_free_mode_and_commits_only_shadow_reports(self) -> None:
        workflow = Path(".github/workflows/weekly-shadow-coach.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('cron: "0 19 * * 6"', workflow)
        self.assertIn("default: dry_run", workflow)
        self.assertIn("WEEKLY_SHADOW_OPENAI_ENABLED", workflow)
        self.assertIn("secrets.OPENAI_API_KEY", workflow)
        self.assertIn("--archive-result google_drive_archive_result.json", workflow)
        self.assertIn(
            "git add reports/weekly_shadow_coach.json reports/weekly_shadow_coach_history.json",
            workflow,
        )
        self.assertNotIn("git add reports/\n", workflow)


if __name__ == "__main__":
    unittest.main()
