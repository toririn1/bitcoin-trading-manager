import unittest

from unittest.mock import patch

import agents.pipeline as pipeline_module
from agents.debate import DebateResult
from agents.judge import JudgeResult, _parse_judge_output
from agents.risk_triad import RiskTriadResult


class AgentDecisionLayerTests(unittest.TestCase):
    def test_judge_default_fields_are_structured(self):
        result = JudgeResult(
            enabled=False,
            verdict="",
            reasoning="",
            bull_key="",
            bear_key="",
            raw_text="",
        ).to_payload()
        self.assertEqual(result["market_view"]["direction"], "중립")
        self.assertEqual(result["setup_view"]["action_verdict"], "wait_for_trigger")
        self.assertEqual(
            result["account_permission"]["execution_permission"],
            "manual_confirm_required",
        )
        self.assertIn("final_action", result)

    def test_risk_default_fields_are_structured(self):
        result = RiskTriadResult(enabled=False, rounds=0).to_payload()
        self.assertEqual(result["market_view"]["direction"], "중립")
        self.assertEqual(result["setup_view"]["entry_expectancy"], "acceptable")
        self.assertEqual(
            result["account_permission"]["execution_permission"],
            "manual_confirm_required",
        )
        self.assertEqual(result["final_action"], "wait_for_trigger_with_manual_confirm")

    def test_judge_parser_keeps_market_and_account_separate(self):
        parsed = _parse_judge_output(
            "\n".join(
                [
                    "판정: 상방 우위",
                    "시장 확신: 72",
                    "시장 레짐: 상승 추세",
                    "무효화: 62,930 하회",
                    "Setup action: wait_for_trigger",
                    "진입 기대값: conditional_good",
                    "계좌 실행 허가: blocked",
                    "계좌 제한 이유: conservative net fee extreme",
                    "금지 행동: open_new_position, high_leverage",
                    "Final action: wait_for_trigger_but_no_new_entry_until_fee_cooldown",
                    "점수: price_structure=2, momentum=1, derivatives=1, macro=0, account_risk_fit=-2, counter_scenario=-1",
                    "이유: 상방 구조는 유지되지만 계좌 오버레이는 별도 차단이다.",
                    "Bull 핵심: 가격 구조는 상방이다.",
                    "Bear 핵심: 저항 확인이 필요하다.",
                ]
            )
        )
        self.assertEqual(parsed["verdict"], "상방 우위")
        self.assertEqual(parsed["setup_action"], "wait_for_trigger")
        self.assertEqual(parsed["entry_expectancy"], "conditional_good")
        self.assertEqual(parsed["execution_permission"], "blocked")
        self.assertEqual(
            parsed["final_action"],
            "wait_for_trigger_but_no_new_entry_until_fee_cooldown",
        )
        self.assertIn("open_new_position", parsed["forbidden_actions"])

    def test_pipeline_threads_same_decision_object(self):
        decision = {
            "market_direction": "상방 우위",
            "setup_action_verdict": "wait_for_trigger",
            "account_execution_permission": "blocked",
            "final_action": "wait_for_trigger_but_no_new_entry_until_fee_cooldown",
        }
        debate = DebateResult(enabled=False, rounds=0)
        judge_result = JudgeResult(False, "", "", "", "", "")
        risk_result = RiskTriadResult(False, 0)

        def fake_judge(**kwargs):
            self.assertIs(kwargs["decision_support"], decision)
            judge_result.decision_support = kwargs["decision_support"]
            return judge_result

        def fake_risk(**kwargs):
            self.assertIs(kwargs["decision_support"], decision)
            risk_result.decision_support = kwargs["decision_support"]
            return risk_result

        with (
            patch.object(pipeline_module, "run_bull_bear_debate", return_value=debate),
            patch.object(pipeline_module, "run_judge", side_effect=fake_judge),
            patch.object(pipeline_module, "run_risk_triad", side_effect=fake_risk),
            patch.object(pipeline_module, "_JUDGE_AVAILABLE", True),
            patch.object(pipeline_module, "RISK_TRIAD_IN_PIPELINE", True),
        ):
            result = pipeline_module.run_pipeline(
                context_blob="shared context",
                pair_label="BTC/USDT",
                decision_support=decision,
            )

        self.assertIs(result.judge.decision_support, decision)
        self.assertIs(result.risk.decision_support, decision)


if __name__ == "__main__":
    unittest.main()
