from fastapi.testclient import TestClient

from server import app


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

    monkeypatch.setattr(app.state.v2_engine, "_live_observations", no_live_observations)
    with TestClient(app) as client:
        response = client.get("/api/v2/snapshot")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["mode"] == "live"
        assert data["synthetic"] is False
        assert data["data_unavailable"] is True


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
