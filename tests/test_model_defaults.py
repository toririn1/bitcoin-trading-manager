import unittest
from pathlib import Path

import config
from agents.judge import JUDGE_SYSTEM
from agents.prompts import BEAR_SYSTEM, BULL_SYSTEM
from agents.risk_prompts import AGGRESSIVE_SYSTEM, CONSERVATIVE_SYSTEM, NEUTRAL_SYSTEM


ROOT = Path(__file__).resolve().parents[1]


class ModelDefaultTests(unittest.TestCase):
    def test_runtime_default_is_56_sol(self):
        self.assertEqual(config.LLM_MODEL, "gpt-5.6-sol")

    def test_intended_runtime_and_ui_files_do_not_pin_old_model(self):
        old_model = "gpt-" + "5.5"
        files = [
            ROOT / "config.py",
            ROOT / ".env.example",
            ROOT / "README.md",
            ROOT / "run.sh",
            ROOT / "static" / "index.html",
        ]
        for path in files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("gpt-5.6-sol", text)
                self.assertNotIn(old_model, text)

    def test_prompts_preserve_roles_without_absolute_account_override(self):
        prompts = "\n".join(
            [
                BULL_SYSTEM,
                BEAR_SYSTEM,
                AGGRESSIVE_SYSTEM,
                CONSERVATIVE_SYSTEM,
                NEUTRAL_SYSTEM,
                JUDGE_SYSTEM,
            ]
        )
        self.assertNotIn("execution_permission=blocked면", prompts)
        self.assertIn("시장 방향", prompts)
        self.assertIn("account_execution_permission", prompts)


if __name__ == "__main__":
    unittest.main()
