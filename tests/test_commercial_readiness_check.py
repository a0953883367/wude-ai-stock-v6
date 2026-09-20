from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.commercial_readiness_check import REQUIRED_FILES, inspect_repository


class CommercialReadinessCheckTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        (root / ".env.example").write_text(
            "DEPLOYMENT_ENVIRONMENT=test\nTRADING_MODE=paper\nLIVE_TRADING_ENABLED=false\n",
            encoding="utf-8",
        )
        for relative in REQUIRED_FILES.values():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# present\n", encoding="utf-8")

    def test_safe_repository_is_ready_for_testing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            result = inspect_repository(root)
            self.assertEqual(result["status"], "ready_for_testing")
            self.assertTrue(all(item["passed"] for item in result["checks"]))

    def test_live_default_blocks_readiness(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            (root / ".env.example").write_text(
                "DEPLOYMENT_ENVIRONMENT=production\nTRADING_MODE=live\nLIVE_TRADING_ENABLED=true\n",
                encoding="utf-8",
            )
            result = inspect_repository(root)
            self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
