import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server
from engine_v2.api.routes import register_v2_routes
from engine_v2.engine import V2Engine

app = server.app


@pytest.fixture(autouse=True)
def isolated_v2_storage(tmp_path, monkeypatch):
    data_dir = tmp_path / "v2"
    monkeypatch.setenv("V2_DATA_DIR", str(data_dir))
    monkeypatch.setenv("V2_DUCKDB_PATH", str(data_dir / "engine.duckdb"))
    monkeypatch.setenv("V2_PARQUET_ROOT", str(data_dir / "raw"))
    monkeypatch.setenv("V2_STORAGE_BACKEND", "duckdb")
    monkeypatch.setenv("V2_MODE", "live")
    monkeypatch.setenv("V2_LIVE_ENABLED", "false")
    monkeypatch.setenv("V2_OPTION_SAMPLE", "0")
    monkeypatch.setenv("V2_TIMEFRAMES", "15m")


def test_v2_status_and_snapshot_envelopes():
    with TestClient(app) as client:
        status = client.get("/api/v2/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == "2.0"
        snapshot = client.get("/api/v2/demo/snapshot")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["schema_version"] == "2.0"
        assert body["data"]["snapshot_id"].startswith("v2-")
        assert body["data"]["mode"] == "fixture"
        assert body["data"]["synthetic"] is True


def test_v2_manual_intake_is_the_only_v2_write_surface():
    with TestClient(app) as client:
        response = client.post("/api/v2/events/manual-intake", json={"headline": "Fixture headline", "url": "https://example.com/event"})
        assert response.status_code == 200
        event = response.json()["data"]
        assert event["status"] == "reported"
        assert event["discovered_via"] == "manual_intake"


def test_legacy_health_route_remains_available():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200


def test_production_snapshot_does_not_fallback_to_fixture(monkeypatch):
    async def no_live_observations():
        return []

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.v2_engine, "_live_observations", no_live_observations)
        response = client.get("/api/v2/snapshot")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["mode"] == "live"
        assert data["synthetic"] is False
        assert data["data_unavailable"] is True


def test_unavailable_snapshot_has_zero_current_candidates_and_separate_stale_cache(monkeypatch):
    async def no_live_observations():
        return []

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.v2_engine, "_live_observations", no_live_observations)
        warm = client.get("/api/v2/demo/snapshot")
        assert warm.status_code == 200
        live_snapshot = client.get("/api/v2/snapshot?mode=live")
        assert live_snapshot.status_code == 200
        snapshot = live_snapshot.json()["data"]
        assert snapshot["data_unavailable"] is True
        assert snapshot["ranked_candidates"] == []
        assert snapshot["current_candidate_count"] == 0
        assert snapshot["stale_candidate_count"] > 0
        assert snapshot["stale_candidate_generated_at"]

        decision = client.get("/api/v2/decision?mode=live")
        assert decision.status_code == 200
        decision_data = decision.json()["data"]
        assert decision_data["final_action"] == "data_unavailable"
        assert decision_data["candidate_rank"] == []
        assert decision_data["current_candidate_count"] == 0
        assert decision_data["stale_candidate_count"] > 0

        opportunities = client.get("/api/v2/opportunities?mode=live")
        assert opportunities.status_code == 200
        opportunities_body = opportunities.json()
        assert opportunities_body["data"] == []
        assert opportunities_body["meta"]["current_candidate_count"] == 0
        assert opportunities_body["meta"]["stale_candidate_count"] > 0


def test_v2_ui_separates_legacy_llm_and_canonical_current_candidates():
    html = Path(__file__).resolve().parents[1].joinpath("static", "index.html").read_text()
    assert "LEGACY LLM / ANALYSIS CACHE" in html
    assert "V2 ENGINE / DETERMINISTIC / READ-ONLY SHADOW" in html
    assert 'String(item.product_id || "unknown")' in html
    assert "const currentCandidates = unavailable ? [] : candidates;" in html
    assert "countEl.textContent = String(currentCandidates.length);" in html
    assert 'const decisionRes = await fetch("/api/v2/decision");' in html
    assert "Promise.all([" not in html[html.index("async function fetchV2Dashboard()"):html.index("fetchV2Dashboard();")]


def test_manual_intake_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("SAVE_TICKER_INTAKE_TOKEN", "test-intake-token")
    with TestClient(app) as client:
        denied = client.post("/api/v2/events/manual-intake", json={"headline": "Denied"})
        assert denied.status_code == 401
        allowed = client.post(
            "/api/v2/events/manual-intake",
            headers={"X-Intake-Token": "test-intake-token"},
            json={"headline": "Allowed"},
        )
        assert allowed.status_code == 200


def test_import_server_does_not_open_duckdb(tmp_path):
    data_dir = tmp_path / "import-only"
    env = os.environ.copy()
    env.update({
        "V2_DATA_DIR": str(data_dir),
        "V2_DUCKDB_PATH": str(data_dir / "engine.duckdb"),
        "V2_PARQUET_ROOT": str(data_dir / "raw"),
        "V2_STORAGE_BACKEND": "duckdb",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import server; assert not hasattr(server.app.state, 'v2_engine')"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (data_dir / "engine.duckdb").exists()


def test_route_registration_does_not_create_engine(monkeypatch):
    import engine_v2.api.routes as routes

    def fail_engine(*args, **kwargs):
        raise AssertionError("V2Engine must not be constructed during route registration")

    monkeypatch.setattr(routes, "V2Engine", fail_engine)
    test_app = FastAPI()
    router = register_v2_routes(test_app)
    assert router is not None
    included_router = test_app.routes[-1].original_router
    assert any(route.path == "/api/v2/status" for route in included_router.routes)
    assert not hasattr(test_app.state, "v2_engine")


def test_v2_endpoint_returns_503_before_startup():
    test_app = FastAPI()
    register_v2_routes(test_app)
    with TestClient(test_app) as client:
        response = client.get("/api/v2/status")
    assert response.status_code == 503
    assert response.json()["detail"] == "V2 engine is not initialized"


def test_testclient_lifecycle_creates_and_closes_v2_engine():
    close_calls = []
    with TestClient(app) as client:
        engine = app.state.v2_engine
        assert isinstance(engine, V2Engine)
        original_close = engine.close

        def close_spy():
            close_calls.append(True)
            original_close()

        engine.close = close_spy
        assert client.get("/api/v2/status").status_code == 200
    assert close_calls == [True]
    assert app.state.v2_engine is None


def test_two_testclients_sequentially_release_duckdb_lock():
    with TestClient(app) as first:
        assert first.get("/api/v2/status").status_code == 200
    assert app.state.v2_engine is None
    with TestClient(app) as second:
        assert second.get("/api/v2/status").status_code == 200
    assert app.state.v2_engine is None


def test_run_sh_reload_is_opt_in():
    text = Path(__file__).resolve().parents[1].joinpath("run.sh").read_text()
    assert "UVICORN_ARGS=(" in text
    assert "UVICORN_ARGS+=(--reload)" in text
    assert "exec .venv/bin/python -m uvicorn " + '"' + "$" + "{UVICORN_ARGS[@]}" + '"' in text
    default_section = text.split("if [[", 1)[0]
    assert "--reload" not in default_section


def test_uvicorn_reload_subprocess_starts_without_duckdb_lock(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    data_dir = tmp_path / "reload"
    env = os.environ.copy()
    env.update({
        "V2_DATA_DIR": str(data_dir),
        "V2_DUCKDB_PATH": str(data_dir / "engine.duckdb"),
        "V2_PARQUET_ROOT": str(data_dir / "raw"),
        "V2_STORAGE_BACKEND": "duckdb",
        "V2_MODE": "fixture",
        "V2_LIVE_ENABLED": "false",
        "V2_OPTION_SAMPLE": "0",
        "V2_TIMEFRAMES": "15m",
    })
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--reload",
            "--log-level",
            "warning",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        deadline = time.monotonic() + 20
        responses = {}
        while time.monotonic() < deadline and len(responses) < 2:
            for path in ("/health", "/api/v2/status"):
                if path in responses:
                    continue
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
                        responses[path] = response.status
                except (urllib.error.URLError, TimeoutError):
                    pass
            time.sleep(0.2)
        assert responses == {"/health": 200, "/api/v2/status": 200}
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate(timeout=5)
    assert "Conflicting lock" not in output
    assert "_duckdb.IOException" not in output
