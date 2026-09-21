import unittest

from tools.publication_isolation_acceptance_check import (
    inspect_publication_isolation,
)


class PublicationIsolationAcceptanceTests(unittest.TestCase):
    def test_fake_owner_and_friend_payloads_are_isolated(self):
        result = inspect_publication_isolation()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
