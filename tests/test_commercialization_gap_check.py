import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.commercialization_gap_check import inspect_manifest


class CommercializationGapCheckTests(unittest.TestCase):
    def test_repository_manifest_passes(self):
        result = inspect_manifest()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["summary"]["verified"], 1)
        self.assertGreater(result["summary"]["blocked_external"], 0)

    def test_verified_without_evidence_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path("commercialization_readiness.json")
            data = json.loads(source.read_text(encoding="utf-8"))
            data["items"][0]["status"] = "verified"
            data["items"][0]["evidence"] = []
            manifest = root / source.name
            manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = inspect_manifest(manifest, root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("沒有證據" in error for error in result["errors"]))

    def test_missing_external_blocker_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path("commercialization_readiness.json")
            data = json.loads(source.read_text(encoding="utf-8"))
            data["items"][0]["blocked_by"] = ""
            manifest = root / source.name
            manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = inspect_manifest(manifest, root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("沒有說明原因" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
