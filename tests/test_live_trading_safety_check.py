import unittest

from tools.live_trading_safety_check import inspect_live_trading_gate


class LiveTradingSafetyCheckTests(unittest.TestCase):
    def test_all_live_trading_gate_checks_pass_without_broker_connection(self):
        result = inspect_live_trading_gate()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(item["passed"] for item in result["checks"]))
        self.assertIn("不登入券商", result["scope"])


if __name__ == "__main__":
    unittest.main()
