from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.db.schema import init_db, close_db


@pytest.fixture(autouse=True)
async def isolated_health(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.db.schema.DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.setattr("agent.config.CLIENT_MAINTENANCE", False)
    await init_db()
    yield
    await close_db()


@pytest.mark.parametrize("connected,available,code,status", [
    (True, True, 200, "degraded"),
    (False, True, 503, "unavailable"),
    (True, False, 503, "unavailable"),
])
async def test_health_capabilities(monkeypatch, connected, available, code, status):
    provider = SimpleNamespace(connected=connected, list_extensions=lambda: [
        {"available": available, "account_email": "private@example.com"}])
    monkeypatch.setattr("agent.api.v1.health.get_flow_client", lambda: provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/health")
    assert response.status_code == code
    body = response.json()
    assert body["status"] == status
    assert body["maintenance"] is False
    assert body["accepting_requests"] is (code == 200)
    assert body["capabilities"]["video_generation"] == {
        "available": False, "reason": "OMNI_TRANSPORT_UNSUPPORTED"}
    assert response.headers["cache-control"] == "no-store"
    assert "private@example.com" not in response.text
    if code == 503:
        assert response.headers["retry-after"] == "10"


async def test_database_unavailable(monkeypatch):
    monkeypatch.setattr("agent.api.v1.health.get_db", AsyncMock(side_effect=RuntimeError("secret")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/health")
    assert response.status_code == 503
    assert response.json()["capabilities"]["image_generation"]["reason"] == "BACKEND_UNAVAILABLE"
    assert "secret" not in response.text


async def test_maintenance_blocks_writes_but_keeps_polling(monkeypatch):
    monkeypatch.setattr("agent.config.CLIENT_MAINTENANCE", True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/v1/health")
        assert health.status_code == 503
        assert health.json()["status"] == "maintenance"
        for method, path in [("POST", "/v1/images/generations"),
                             ("POST", "/v1/characters"),
                             ("PATCH", "/v1/characters/test"),
                             ("DELETE", "/v1/characters/test")]:
            response = await client.request(method, path, json={"prompt": "test"})
            assert response.status_code == 503
            assert response.headers["retry-after"] == "60"
        response = await client.post("/v1/jobs/status", json={"job_id": "missing"})
        assert response.status_code == 200
        assert (await client.get("/v1/jobs/missing")).status_code == 200
        assert (await client.get("/health/live")).status_code == 200
        monkeypatch.setattr("agent.config.CLIENT_MAINTENANCE", False)
        response = await client.post("/v1/images/generations", json={"prompt": "test"})
        assert response.status_code == 202


def test_health_openapi():
    schema = app.openapi()
    route = schema["paths"]["/v1/health"]["get"]
    assert "503" in route["responses"]
    assert "ClientHealth" in schema["components"]["schemas"]
