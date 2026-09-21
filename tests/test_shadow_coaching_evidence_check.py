from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.shadow_coaching_evidence_check import inspect_shadow_coaching_evidence


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _policy() -> dict:
    return {
        "shadow_only": True,
        "formal_v6_unchanged": True,
        "formal_rankings_unchanged": True,
        "formal_weights_unchanged": True,
        "historical_data_never_deleted": True,
        "automatic_orders": False,
        "automatic_merge": False,
        "automatic_formal_promotion": False,
    }


class ShadowCoachingEvidenceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        report = root / "report.json"
        history = root / "history.json"
        registry = root / "registry.json"
        generated_at = "2026-09-13T15:56:13+00:00"
        _write(report, {
            "status": "ok",
            "mode": "openai",
            "generated_at": generated_at,
            "api": {"called": True, "store": False, "response_id": "resp_test"},
            "source": {
                "independent_events": 3,
                "backup": {"verified": True},
                "cloud_archive": {"verified": True, "errors": 0},
            },
            "policy": _policy(),
        })
        _write(history, [{
            "generated_at": generated_at,
            "status": "ok",
            "mode": "openai",
            "policy": _policy(),
        }])
        candidate = {
            "visible_in_predictions": False,
            "affects_formal_v6": False,
            "affects_formal_rankings": False,
            "affects_formal_weights": False,
            "automatic_orders": False,
            "arbitrary_code_allowed": False,
            "automatic_formal_promotion": False,
        }
        registry_policy = _policy()
        registry_policy["arbitrary_code_allowed"] = False
        _write(registry, {
            "trading_days_collected": 21,
            "candidates": [candidate],
            "policy": registry_policy,
        })
        return report, history, registry

    def test_valid_openai_shadow_evidence_passes(self):
        with TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            result = inspect_shadow_coaching_evidence(*paths)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["evidence"]["candidate_count"], 1)

    def test_formal_weight_unlock_is_rejected(self):
        with TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            report = json.loads(paths[0].read_text(encoding="utf-8"))
            report["policy"]["formal_weights_unchanged"] = False
            _write(paths[0], report)
            result = inspect_shadow_coaching_evidence(*paths)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("formal_weights_unchanged" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
