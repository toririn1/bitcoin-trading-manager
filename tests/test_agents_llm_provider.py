import types
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import llm_client
from agents.debate import run_bull_bear_debate
from agents.judge import run_judge
from agents.risk_triad import run_risk_triad


class AgentLLMProviderTests(unittest.TestCase):
    def test_agents_do_not_import_anthropic_directly(self):
        import_stmt = "import " + "anthropic"
        client_ctor = "anthropic" + ".Anthropic"
        for path in Path("agents").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(import_stmt, source, str(path))
            self.assertNotIn(client_ctor, source, str(path))

    def test_openai_oauth_debate_judge_risk_precheck_passes_without_key(self):
        with (
            patch.object(config, "LLM_PROVIDER", "openai_oauth"),
            patch.object(config, "LLM_BASE_URL", "http://127.0.0.1:10532/v1"),
            patch.object(config, "LLM_MODEL", "gpt-5.6-sol"),
            patch.object(config, "LLM_API_KEY", ""),
            patch("agents.debate.call_text_llm", return_value="agent text"),
            patch("agents.judge.call_text_llm", return_value=(
                "판정: 중립\n"
                "점수: price_structure=0, momentum=0, derivatives=0, macro=0, account_risk_fit=0, counter_scenario=0\n"
                "이유: 테스트\nBull 핵심: 테스트\nBear 핵심: 테스트"
            )),
            patch("agents.risk_triad.call_text_llm", return_value="risk text"),
        ):
            debate = run_bull_bear_debate("context", "BTC/USDT", max_rounds=1)
            judge = run_judge("context", "BTC/USDT", debate.final_bull, debate.final_bear)
            risk = run_risk_triad("context", "BTC/USDT", debate.final_bull, debate.final_bear, max_rounds=1)

        self.assertTrue(debate.enabled)
        self.assertIsNone(debate.error)
        legacy_key_name = "CLAUDE" + "_API_KEY"
        self.assertNotIn(legacy_key_name, str(debate.to_payload()))
        self.assertTrue(judge.enabled)
        self.assertNotIn(legacy_key_name, str(judge.to_payload()))
        self.assertTrue(risk.enabled)
        self.assertIsNone(risk.error)
        self.assertNotIn(legacy_key_name, str(risk.to_payload()))

    def test_openai_oauth_call_text_llm_uses_chat_completions_without_auth(self):
        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        with (
            patch.object(config, "LLM_PROVIDER", "openai_oauth"),
            patch.object(config, "LLM_BASE_URL", "http://127.0.0.1:10532/v1"),
            patch.object(config, "LLM_MODEL", "gpt-5.6-sol"),
            patch.object(config, "LLM_API_KEY", ""),
            patch("llm_client.requests.post", return_value=FakeResponse()) as post,
        ):
            text = llm_client.call_text_llm("system", "user", max_tokens=123)

        self.assertEqual(text, "ok")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:10532/v1/chat/completions")
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gpt-5.6-sol")

    def test_anthropic_call_text_llm_path_uses_anthropic_key(self):
        class FakeMessages:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text="anthropic ok")]
                )

        fake_messages = FakeMessages()

        class FakeAnthropic:
            def __init__(self, api_key):
                self.api_key = api_key
                self.messages = fake_messages

        with (
            patch.object(config, "LLM_PROVIDER", "anthropic"),
            patch.object(config, "ANTHROPIC_API_KEY", "test-anthropic-key"),
            patch.object(config, "ANTHROPIC_MODEL", "claude-test"),
            patch("anthropic" + ".Anthropic", FakeAnthropic),
        ):
            text = llm_client.call_text_llm("system", "user", max_tokens=321)

        self.assertEqual(text, "anthropic ok")
        self.assertEqual(fake_messages.kwargs["model"], "claude-test")
        self.assertEqual(fake_messages.kwargs["max_tokens"], 321)


if __name__ == "__main__":
    unittest.main()
