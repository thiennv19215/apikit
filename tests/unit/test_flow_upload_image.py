"""Unit tests for POST /api/flow/upload-image and FlowKitClient.upload_image."""
import base64
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport

from agent.main import app
from agent.db.schema import init_db, close_db
from flowkit_client import FlowKitClient


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_upload_image.db")
    monkeypatch.setattr("agent.config.DB_PATH", test_db)
    monkeypatch.setattr("agent.db.schema.DB_PATH", test_db)
    await init_db()
    yield
    await close_db()


@pytest.mark.asyncio
async def test_upload_image_with_raw_base64():
    mock_client = MagicMock()
    mock_client.connected = True
    mock_client.upload_image = AsyncMock(return_value={
        "status": 200,
        "data": {"media": {"name": "test-uuid-raw-b64"}},
        "_mediaId": "test-uuid-raw-b64",
    })

    sample_b64 = base64.b64encode(b"fake image data").decode("utf-8")

    with patch("agent.api.flow.get_flow_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/flow/upload-image", json={
                "image_base64": sample_b64,
                "project_id": "proj-123",
                "file_name": "sample.jpg",
                "mime_type": "image/jpeg",
            })

    assert res.status_code == 200
    data = res.json()
    assert data["media_id"] == "test-uuid-raw-b64"
    mock_client.upload_image.assert_awaited_once_with(
        sample_b64, mime_type="image/jpeg", project_id="proj-123", file_name="sample.jpg"
    )


@pytest.mark.asyncio
async def test_upload_image_with_data_uri():
    mock_client = MagicMock()
    mock_client.connected = True
    mock_client.upload_image = AsyncMock(return_value={
        "status": 200,
        "data": {"media": {"name": "test-uuid-data-uri"}},
        "_mediaId": "test-uuid-data-uri",
    })

    raw_payload = base64.b64encode(b"png image bytes").decode("utf-8")
    data_uri = f"data:image/webp;base64,{raw_payload}"

    with patch("agent.api.flow.get_flow_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/flow/upload-image", json={
                "image_base64": data_uri,
            })

    assert res.status_code == 200
    data = res.json()
    assert data["media_id"] == "test-uuid-data-uri"
    # Verify that data URI header was parsed to mime and stripped from b64
    mock_client.upload_image.assert_awaited_once_with(
        raw_payload, mime_type="image/webp", project_id="", file_name="image.png"
    )


@pytest.mark.asyncio
async def test_upload_image_with_file_path(tmp_path):
    mock_client = MagicMock()
    mock_client.connected = True
    mock_client.upload_image = AsyncMock(return_value={
        "status": 200,
        "data": {"media": {"name": "test-uuid-file"}},
        "_mediaId": "test-uuid-file",
    })

    test_file = tmp_path / "avatar.png"
    test_file.write_bytes(b"avatar image bytes")
    expected_b64 = base64.b64encode(b"avatar image bytes").decode("utf-8")

    with patch("agent.api.flow.get_flow_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/flow/upload-image", json={
                "file_path": str(test_file),
                "project_id": "proj-abc",
            })

    assert res.status_code == 200
    data = res.json()
    assert data["media_id"] == "test-uuid-file"
    mock_client.upload_image.assert_awaited_once_with(
        expected_b64, mime_type="image/png", project_id="proj-abc", file_name="avatar.png"
    )


@pytest.mark.asyncio
async def test_upload_image_missing_both_file_and_base64():
    mock_client = MagicMock()
    mock_client.connected = True

    with patch("agent.api.flow.get_flow_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/flow/upload-image", json={})

    assert res.status_code == 400
    assert "Either file_path or image_base64 must be provided" in res.text


@pytest.mark.asyncio
async def test_upload_image_file_not_found():
    mock_client = MagicMock()
    mock_client.connected = True

    with patch("agent.api.flow.get_flow_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/flow/upload-image", json={
                "file_path": "/non/existent/path/image.png"
            })

    assert res.status_code == 404
    assert "File not found" in res.text


@pytest.mark.asyncio
async def test_upload_image_extension_disconnected():
    mock_client = MagicMock()
    mock_client.connected = False

    with patch("agent.api.flow.get_flow_client", return_value=mock_client):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/flow/upload-image", json={
                "image_base64": "abc123"
            })

    assert res.status_code == 503
    assert "Extension not connected" in res.text


def test_flowkit_client_upload_image_base64():
    client = FlowKitClient(base_url="http://127.0.0.1:8100")
    client._request = MagicMock(return_value={"media_id": "uuid-client-123"})

    res = client.upload_image(
        image_base64="dGVzdA==",
        project_id="pid-1",
        file_name="test.png",
        mime_type="image/png"
    )

    assert res["media_id"] == "uuid-client-123"
    client._request.assert_called_once_with(
        "POST",
        "/api/flow/upload-image",
        json_data={
            "project_id": "pid-1",
            "image_base64": "dGVzdA==",
            "file_name": "test.png",
            "mime_type": "image/png",
        }
    )


def test_flowkit_client_upload_image_file(tmp_path):
    client = FlowKitClient(base_url="http://127.0.0.1:8100")
    client._request = MagicMock(return_value={"media_id": "uuid-client-456"})

    img_file = tmp_path / "photo.jpg"
    img_file.write_bytes(b"jpeg bytes")
    expected_b64 = base64.b64encode(b"jpeg bytes").decode("utf-8")

    res = client.upload_image(file_path=str(img_file), project_id="pid-2")

    assert res["media_id"] == "uuid-client-456"
    client._request.assert_called_once_with(
        "POST",
        "/api/flow/upload-image",
        json_data={
            "project_id": "pid-2",
            "image_base64": expected_b64,
            "file_name": "photo.jpg",
            "mime_type": "image/jpeg",
        }
    )


def test_flowkit_client_upload_image_empty():
    client = FlowKitClient()
    with pytest.raises(ValueError):
        client.upload_image()
