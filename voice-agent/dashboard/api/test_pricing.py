import unittest

from pricing import DEFAULT_PRICES, PricingEngine


class PricingSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine = PricingEngine(DEFAULT_PRICES, secret=b"test-secret")

    def test_ai_payload_contains_no_protected_prices(self):
        payload = self.engine.issue_range("prestige-cutter", "session-1").ai_payload()
        self.assertNotIn("floor_price", payload)
        self.assertNotIn("target_price", payload)

    def test_algorithm_never_crosses_floor(self):
        config = DEFAULT_PRICES["prestige-cutter"]
        for round_number in range(1, 13):
            grant = self.engine.issue_range("prestige-cutter", "session", round_number)
            self.assertGreaterEqual(grant.min_offer, config.floor_price)
            self.assertLessEqual(grant.min_offer, grant.max_offer)

    def test_prompt_injection_or_out_of_range_offer_is_rejected(self):
        grant = self.engine.issue_range("prestige-cutter", "session")
        accepted, reason = self.engine.validate_offer(grant.session_token, grant.min_offer - 1)
        self.assertFalse(accepted)
        self.assertEqual(reason, "offer_outside_approved_range")

    def test_tokens_are_session_scoped_and_not_reusable_as_prices(self):
        first = self.engine.issue_range("prestige-cutter", "a")
        second = self.engine.issue_range("prestige-cutter", "b")
        self.assertNotEqual(first.session_token, second.session_token)
        self.assertFalse(self.engine.validate_offer("floor_price", first.min_offer)[0])


if __name__ == "__main__":
    unittest.main()
