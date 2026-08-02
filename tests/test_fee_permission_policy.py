import unittest

from decision_bridge import apply_trade_quality, build_decision_support, build_fee_summary


class FeePermissionPolicyTests(unittest.TestCase):
    def _account(self, gross, received=0, pending=0, equity=1000, trades=0, **extra):
        account = {
            "account_equity": equity,
            "today_fee_paid": gross,
            "today_rebate_received": received,
            "expected_rebate_pending": pending,
            "recent_2h_trade_count": trades,
        }
        account.update(extra)
        return account

    def _decision(self, account):
        return build_decision_support({"market_direction": "상방 우위"}, account)

    def test_reduce_threshold_uses_conservative_net_fee(self):
        self.assertEqual(
            self._decision(self._account(30))["account_execution_permission"],
            "reduce_size_only",
        )

    def test_block_threshold_requires_cooldown_without_repeat_trading(self):
        decision = self._decision(self._account(60))
        self.assertEqual(decision["account_execution_permission"], "cooldown_required")
        self.assertNotIn("open_new_position", decision["forbidden_action_codes"])

    def test_hard_threshold_blocks_new_entry(self):
        decision = self._decision(self._account(110))
        self.assertEqual(decision["account_execution_permission"], "blocked")
        self.assertIn("new_entry_when_blocked", decision["forbidden_action_codes"])

    def test_hard_threshold_plus_repeated_trading_is_hard_block(self):
        decision = self._decision(self._account(110, trades=8))
        self.assertEqual(decision["account_execution_permission"], "hard_block")

    def test_high_gross_low_net_is_warning_not_hard_block(self):
        decision = self._decision(self._account(100, received=99))
        fee = decision["fee_summary"]
        self.assertTrue(fee["overtrading_fee_warning"])
        self.assertLess(fee["conservative_net_fee_to_equity_ratio"], 0.02)
        self.assertEqual(decision["account_execution_permission"], "manual_confirm_required")

    def test_browser_example_is_not_allow(self):
        decision = self._decision(
            self._account(
                384.007332,
                received=243.98274,
                pending=24.8223924,
                equity=1195.25,
            )
        )
        self.assertGreater(decision["fee_summary"]["conservative_net_fee_to_equity_ratio"], 0.10)
        self.assertIn(decision["account_execution_permission"], {"blocked", "hard_block"})
        self.assertEqual(decision["market_direction"], "상방 우위")
        self.assertNotEqual(decision["setup_action_verdict"], "no_trade")

    def test_missing_fee_data_does_not_block(self):
        decision = self._decision({"account_equity": 1000})
        self.assertEqual(decision["fee_summary"]["reason"], "fee history unavailable")
        self.assertNotIn(decision["account_execution_permission"], {"blocked", "hard_block"})

    def test_fee_summary_exposes_gross_net_and_conservative(self):
        fee = build_fee_summary(self._account(100, received=40, pending=20))
        self.assertEqual(fee["gross_fee"], 100)
        self.assertEqual(fee["net_fee"], 40)
        self.assertEqual(fee["conservative_net_fee"], 50)
        self.assertEqual(fee["gross_fee_to_equity_ratio"], 0.1)
        self.assertEqual(fee["net_fee_to_equity_ratio"], 0.04)
        self.assertEqual(fee["conservative_net_fee_to_equity_ratio"], 0.05)

    def test_blocked_account_can_reduce_or_close_existing_position(self):
        decision = self._decision(
            self._account(
                110,
                positions=[{"side": "short", "size": -0.01, "leverage": 3}],
            )
        )
        self.assertEqual(decision["position_alignment"], "conflicted")
        self.assertIn("reduce_position", decision["allowed_actions"])
        self.assertIn("close_position", decision["allowed_actions"])
        self.assertTrue(decision["final_action"].startswith("reduce_or_close_conflicting_position"))

    def test_fee_pressure_raises_account_adjusted_rr(self):
        decision = self._decision(self._account(30))
        apply_trade_quality(
            decision,
            100,
            {"entry": 100, "stop": 95, "target": 106.5},
            "지금 롱 진입",
        )
        self.assertEqual(decision["setup_action_verdict"], "enter_long_now")
        self.assertEqual(decision["setup_quality"], "good")
        self.assertFalse(decision["immediate_entry_allowed"])
        self.assertEqual(decision["account_adjusted_required_rr"], 1.5)
        self.assertEqual(decision["final_action"], "wait_for_better_rr_with_size_limit")

    def test_blocked_overlay_does_not_erase_valid_market_setup(self):
        decision = self._decision(self._account(110))
        apply_trade_quality(
            decision,
            100,
            {"entry": 100, "stop": 95, "target": 110},
            "지금 롱 진입",
        )
        self.assertEqual(decision["market_direction"], "상방 우위")
        self.assertEqual(decision["setup_action_verdict"], "enter_long_now")
        self.assertEqual(decision["account_execution_permission"], "blocked")
        self.assertFalse(decision["immediate_entry_allowed"])
        self.assertIn("no_new_entry", decision["allowed_actions"])
        self.assertIn("no_new_entry", decision["final_action"])


if __name__ == "__main__":
    unittest.main()
