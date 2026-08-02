import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import server


class AnalysisCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_cooldown_does_not_block_start(self):
        manager = server.AnalysisManager()

        async def fake_run_job(job_id):
            await server.asyncio.sleep(60)

        manager._run_job = fake_run_job
        with patch.object(server.runtime_config, "ANALYSIS_COOLDOWN_SECS", 0):
            job, started = await manager.start_job()

        self.assertTrue(started)
        self.assertEqual(job["status"], "running")
        await manager.stop()

    async def test_positive_cooldown_returns_remaining_time(self):
        manager = server.AnalysisManager()
        manager._last_manual_finished_at = time.time() - 60

        with patch.object(server.runtime_config, "ANALYSIS_COOLDOWN_SECS", 3600):
            with self.assertRaises(HTTPException) as cm:
                await manager.start_job()

        self.assertEqual(cm.exception.status_code, 429)
        remaining = cm.exception.detail["cooldown_remaining_secs"]
        self.assertGreaterEqual(remaining, 3500)
        self.assertLessEqual(remaining, 3600)

    async def test_prevent_concurrent_analysis_blocks_duplicate_request(self):
        manager = server.AnalysisManager()
        now = server._now_iso()
        manager._job = {
            "id": "running",
            "status": "running",
            "step": 1,
            "phase": None,
            "phase_detail": None,
            "error": None,
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
        }

        with patch.object(server.runtime_config, "PREVENT_CONCURRENT_ANALYSIS", True):
            with self.assertRaises(HTTPException) as cm:
                await manager.start_job()

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["reason"], "analysis_running")

    async def test_openai_oauth_without_key_passes_analyze_precheck(self):
        fake_job = {
            "id": "fake",
            "status": "running",
            "step": 0,
            "phase": None,
            "phase_detail": None,
            "error": None,
            "started_at": server._now_iso(),
            "updated_at": server._now_iso(),
            "completed_at": None,
        }
        with (
            patch.object(server.runtime_config, "LLM_PROVIDER", "openai_oauth"),
            patch.object(server.runtime_config, "LLM_BASE_URL", "http://127.0.0.1:10532/v1"),
            patch.object(server.runtime_config, "LLM_MODEL", "gpt-5.6-sol"),
            patch.object(server.runtime_config, "LLM_API_KEY", ""),
            patch.object(server._analysis_manager, "start_job", AsyncMock(return_value=(fake_job, True))),
        ):
            transport = httpx.ASGITransport(app=server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/api/analyze")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["started"])

    def test_frontend_openai_oauth_and_zero_cooldown_paths_exist(self):
        with open("static/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        with open(".env.example", "r", encoding="utf-8") as f:
            env_example = f.read()

        self.assertIn('body.llm_provider !== "openai_oauth"', html)
        self.assertIn("state.analysisCooldownSecs <= 0", html)
        self.assertIn("ANALYSIS_COOLDOWN_SECS=0", env_example)


if __name__ == "__main__":
    unittest.main()
