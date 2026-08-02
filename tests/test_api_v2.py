from fastapi.testclient import TestClient

from server import app


def test_v2_status_and_snapshot_envelopes():
    with TestClient(app) as client:
        status = client.get("/api/v2/status")
        assert status.status_code == 200
        assert status.json()["schema_version"] == "2.0"
        snapshot = client.get("/api/v2/snapshot")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["schema_version"] == "2.0"
        assert body["data"]["snapshot_id"].startswith("v2-")


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
