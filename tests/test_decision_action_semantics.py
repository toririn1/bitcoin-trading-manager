import unittest

from analyzer import append_decision_report_sections
from decision_bridge import (
    apply_trade_quality,
    build_decision_support,
    ensure_api_compatibility,
)


class DecisionActionSemanticsTests(unittest.TestCase):
    def test_authoritative_report_has_exact_seven_decision_sections(self):
        decision = build_decision_support(
            {"market_direction": "상방 우위"},
            {"account_equity": 1000, "today_fee_paid": 110},
        )
        apply_trade_quality(
            decision,
            100,
            {"entry": 100, "stop": 95, "target": 110},
            "105 돌파 후 리테스트 확인 시 롱",
        )
        report = append_decision_report_sections(
            "📊 관점: 상방 우위\n💬 한줄 요약: 조건부 상방",
            decision,
            {"counter_scenario": ["95 이탈 시 하방 전환"]},
        )
        self.assertEqual(report.count("🧩 최종 Decision Support"), 1)
        for index, label in enumerate(
            [
                "시장 방향",
                "방향 근거",
                "반대 시나리오",
                "무효화 조건",
                "진입 기대값",
                "계좌 실행 허가",
                "금지 행동",
            ],
            start=1,
        ):
            self.assertIn(f"{index}. {label}:", report)
        self.assertIn("wait_for_trigger", report)
        self.assertIn("blocked", report)
        self.assertIn("상방 우위", report)

    def test_existing_decision_block_is_replaced_not_duplicated(self):
        decision = build_decision_support({"market_direction": "중립"}, {})
        report = append_decision_report_sections(
            "본문\n\n🧩 최종 Decision Support\n1. 시장 방향: 오래된 값",
            decision,
            {},
        )
        self.assertEqual(report.count("🧩 최종 Decision Support"), 1)
        self.assertNotIn("오래된 값", report)

    def test_legacy_payload_aliases_remain_synchronized(self):
        payload = ensure_api_compatibility(
            {
                "signal": "매수",
                "decision_support": {
                    "action_verdict": "wait_for_trigger",
                    "entry_expectancy": "good",
                    "execution_permission": "reduce_size_only",
                    "source_labels": None,
                },
            }
        )
        decision = payload["decision_support"]
        self.assertEqual(decision["setup_action_verdict"], "wait_for_trigger")
        self.assertEqual(decision["entry_expectancy"], "conditional_good")
        self.assertEqual(decision["account_execution_permission"], "reduce_size_only")
        self.assertEqual(decision["account_overlay"]["execution_permission"], "reduce_size_only")
        self.assertEqual(decision["source_labels"], {})


if __name__ == "__main__":
    unittest.main()
