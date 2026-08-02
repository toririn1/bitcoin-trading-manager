import importlib
import unittest
from unittest.mock import patch

import config
import llm_client


class LLMClientPayloadTests(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "openai_compatible",
                "LLM_BASE_URL": "http://mock.local/v1",
                "LLM_API_KEY": "test-key",
                "LLM_MODEL": "gpt-4.1-mini",
                "LLM_MAX_TOKENS": "8000",
                "LLM_TEMPERATURE": "0.2",
            },
            clear=False,
        )
        self._env_patch.start()
        importlib.reload(config)
        importlib.reload(llm_client)

    def tearDown(self):
        self._env_patch.stop()
        importlib.reload(config)
        importlib.reload(llm_client)

    def test_openai_compatible_payload_shape(self):
        payload = llm_client._build_openai_payload(
            system_prompt="sys",
            user_prompt="user",
            schema={"input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            use_response_format=False,
            force_json_prompt=True,
        )

        self.assertEqual(payload["model"], "gpt-4.1-mini")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertIn("analysis_json", payload["messages"][1]["content"])
        self.assertEqual(payload["max_tokens"], 8000)
        self.assertEqual(payload["temperature"], 0.2)
        self.assertNotIn("response_format", payload)

    def test_official_openai_uses_json_schema_first(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai", "LLM_BASE_URL": "https://api.openai.com/v1"}, clear=False):
            importlib.reload(config)
            importlib.reload(llm_client)
            payload = llm_client._build_openai_payload(
                system_prompt="sys",
                user_prompt="user",
                schema={"input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
                use_response_format=True,
            )

        self.assertEqual(payload["messages"][0]["role"], "developer")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["response_format"]["json_schema"]["name"], "record_analysis")

    def test_openai_oauth_needs_no_api_key_and_omits_authorization(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_PROVIDER": "openai_oauth",
                "LLM_BASE_URL": "http://127.0.0.1:10532/v1",
                "LLM_API_KEY": "",
                "LLM_MODEL": "gpt-5.6-sol",
            },
            clear=False,
        ):
            importlib.reload(config)
            importlib.reload(llm_client)

            self.assertTrue(config.llm_api_key_configured())
            self.assertEqual(llm_client._provider(), "openai_oauth")

            payload = llm_client._build_openai_payload(
                system_prompt="sys",
                user_prompt="user",
                schema={"input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
                use_response_format=False,
                force_json_prompt=True,
            )
            self.assertEqual(payload["model"], "gpt-5.6-sol")
            self.assertNotIn("temperature", payload)

            class FakeResponse:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"choices": [{"message": {"content": "<analysis_json>{}</analysis_json>"}}]}

            with patch("llm_client.requests.post", return_value=FakeResponse()) as post:
                llm_client._post_chat_completions(payload)

            _, kwargs = post.call_args
            self.assertEqual(post.call_args.args[0], "http://127.0.0.1:10532/v1/chat/completions")
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    def test_frontend_openai_oauth_does_not_require_key(self):
        with open("static/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('value="openai_oauth"', html)
        self.assertIn('body.llm_provider !== "openai_oauth"', html)
        self.assertIn('keyInput.disabled = true', html)
        self.assertIn('http://127.0.0.1:10532/v1', html)
        self.assertIn('gpt-5.6-sol', html)


if __name__ == "__main__":
    unittest.main()
