from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.commercial_readiness_check import (
    REQUIRED_FILES,
    REQUIRED_GITIGNORE_LINES,
    REQUIRED_WORKFLOW_COMMANDS,
    SECRET_EXAMPLE_KEYS,
    inspect_repository,
)


class CommercialReadinessCheckTests(unittest.TestCase):
    def make_repository(self, root: Path) -> None:
        (root / ".env.example").write_text(
            "DEPLOYMENT_ENVIRONMENT=test\n"
            "TRADING_MODE=paper\n"
            "LIVE_TRADING_ENABLED=false\n"
            "LIVE_PUBLIC_READ=0\n"
            "FUBON_AUTO_GIT=0\n"
            "TRADE_STATE_PATH=/data/trading_state.json\n"
            + "".join(f"{key}=\n" for key in SECRET_EXAMPLE_KEYS),
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(
            "\n".join(REQUIRED_GITIGNORE_LINES) + "\n",
            encoding="utf-8",
        )
        workflow = root / ".github/workflows/security-preflight.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("\n".join(REQUIRED_WORKFLOW_COMMANDS) + "\n", encoding="utf-8")
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
                (root / ".env.example").read_text(encoding="utf-8")
                .replace("DEPLOYMENT_ENVIRONMENT=test", "DEPLOYMENT_ENVIRONMENT=production")
                .replace("TRADING_MODE=paper", "TRADING_MODE=live")
                .replace("LIVE_TRADING_ENABLED=false", "LIVE_TRADING_ENABLED=true"),
                encoding="utf-8",
            )
            result = inspect_repository(root)
            self.assertEqual(result["status"], "blocked")

    def test_secret_in_example_env_blocks_readiness_without_exposing_value(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            env_path = root / ".env.example"
            env_path.write_text(
                env_path.read_text(encoding="utf-8").replace("FUBON_API_KEY=", "FUBON_API_KEY=should-not-be-here"),
                encoding="utf-8",
            )
            result = inspect_repository(root)
            self.assertEqual(result["status"], "blocked")
            output = str(result)
            self.assertNotIn("should-not-be-here", output)

    def test_missing_automatic_check_blocks_readiness(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            workflow = root / ".github/workflows/security-preflight.yml"
            workflow.write_text("python tools/security_preflight.py\n", encoding="utf-8")
            result = inspect_repository(root)
            self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
