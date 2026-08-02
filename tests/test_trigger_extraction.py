import unittest

from decision_bridge import apply_trade_quality, build_decision_support


class TriggerExtractionTests(unittest.TestCase):
    def _bullish(self):
        return build_decision_support(
            {"market_direction": "상방 우위"},
            {"account_equity": 1000, "today_fee_paid": 0, "today_rebate_received": 0},
        )

    def test_breakout_and_retest_forces_wait(self):
        decision = self._bullish()
        apply_trade_quality(
            decision,
            63212,
            {"entry": 63212, "stop": 62930, "target": 64000, "resistance": 63606},
            "$63,610 15m 종가 돌파 후 $63,480~$63,606 리테스트 지지 시 롱",
        )
        self.assertEqual(decision["setup_action_verdict"], "wait_for_trigger")
        self.assertEqual(decision["entry_expectancy"], "conditional_good")
        self.assertEqual(decision["trigger_price"], 63610)
        self.assertTrue(decision["trigger_condition"])
        self.assertFalse(decision["setup_immediate_entry_allowed"])

    def test_support_confirmation_forces_wait(self):
        decision = self._bullish()
        apply_trade_quality(
            decision,
            63212,
            {"entry": 63212, "stop": 62930, "target": 64000},
            "$63,120~$63,220 지지 확인 시 롱, $62,930 이탈 시 무효",
        )
        self.assertEqual(decision["setup_action_verdict"], "wait_for_trigger")
        self.assertIn(decision["entry_expectancy"], {"conditional_good", "acceptable"})
        self.assertEqual(decision["entry_zone"], [63120.0, 63220.0])
        self.assertIsNotNone(decision["invalidation"])

    def test_rr_below_one_is_never_good_or_enter_now(self):
        decision = self._bullish()
        apply_trade_quality(decision, 100, {"entry": 100, "stop": 95, "target": 103}, "")
        self.assertLess(decision["risk_reward"], 1.0)
        self.assertEqual(decision["setup_action_verdict"], "wait_for_trigger")
        self.assertEqual(decision["entry_expectancy"], "poor")

    def test_explicit_immediate_entry_requires_valid_rr(self):
        decision = self._bullish()
        apply_trade_quality(
            decision,
            100,
            {"entry": 100, "stop": 95, "target": 110, "resistance": 120},
            "지금 롱 진입. 손절 95, 목표 110.",
        )
        self.assertEqual(decision["setup_action_verdict"], "enter_long_now")
        self.assertIn(decision["entry_expectancy"], {"good", "excellent"})
        self.assertTrue(decision["setup_immediate_entry_allowed"])

    def test_do_not_chase_blocks_immediate_and_adds_code(self):
        decision = self._bullish()
        apply_trade_quality(
            decision,
            100,
            {"entry": 100, "stop": 95, "target": 110},
            "지금 롱 진입이 아니라 미완성봉 추격 금지. 종가 돌파 후 확인.",
        )
        self.assertNotEqual(decision["setup_action_verdict"], "enter_long_now")
        self.assertNotEqual(decision["entry_expectancy"], "good")
        self.assertIn("chase_entry", decision["forbidden_action_codes"])

    def test_wait_state_never_keeps_good(self):
        decision = self._bullish()
        decision["entry_expectancy"] = "good"
        apply_trade_quality(
            decision,
            100,
            {"entry": 105, "stop": 95, "target": 120},
            "105 돌파 확인 후 진입",
        )
        self.assertEqual(decision["setup_action_verdict"], "wait_for_trigger")
        self.assertNotEqual(decision["entry_expectancy"], "good")


if __name__ == "__main__":
    unittest.main()
