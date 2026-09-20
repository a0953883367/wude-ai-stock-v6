import unittest

from tools.paper_trading_acceptance_check import run_acceptance


class PaperTradingAcceptanceTests(unittest.TestCase):
    def test_fake_end_to_end_paper_trade_passes_without_real_orders(self):
        result = run_acceptance()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(item["passed"] for item in result["checks"]))
        self.assertIn("不連券商", result["scope"])


if __name__ == "__main__":
    unittest.main()
