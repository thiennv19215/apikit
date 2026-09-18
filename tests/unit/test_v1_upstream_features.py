import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.api.v1.schemas import (
    ImageEditRequest,
    ImageUpscaleRequest,
    VideoGenerationRequest,
    normalize_image_model,
)
from agent.main import app
from agent.worker.processor import _dispatch_client_v1


def test_nano_banana_2_lite_normalization():
    assert normalize_image_model("banana2_lite") == "NANO_BANANA_2_LITE"
    assert normalize_image_model("nano_banana_2_lite") == "NANO_BANANA_2_LITE"
    assert normalize_image_model("harbor_seal") == "NANO_BANANA_2_LITE"
    assert normalize_image_model("harborseal") == "NANO_BANANA_2_LITE"
    assert normalize_image_model("lite") == "NANO_BANANA_2_LITE"


def test_video_generation_request_text_to_video_and_resolution():
    req = VideoGenerationRequest(
        prompt="A serene sunrise over misty mountains",
        type="text_to_video",
        resolution="360p",
    )
    assert req.type == "text_to_video"
    assert req.generation_type == "text_to_video"
    assert req.resolution == "360p"
    assert len(req.input_images) == 0

    # Default type when no images provided is text_to_video
    req_default = VideoGenerationRequest(prompt="Just a prompt")
    assert req_default.type == "text_to_video"
    assert req_default.resolution == "720p"


def test_image_upscale_request_validation():
    with pytest.raises(ValueError):
        ImageUpscaleRequest()

    req_media = ImageUpscaleRequest(media_id="media_123", quality="4K")
    assert req_media.media_id == "media_123"
    assert req_media.quality == "4K"

    req_base64 = ImageUpscaleRequest(image_base64="aGVsbG8=", resolution="2k")
    assert req_base64.image_base64 == "aGVsbG8="
    assert req_base64.quality == "2K"


def test_image_edit_request_validation():
    req = ImageEditRequest(
        prompt="Make it cartoon style",
        base_image_base64="aGVsbG8=",
        model="banana2_lite",
        count=2,
    )
    assert req.prompt == "Make it cartoon style"
    assert req.base_image_base64 == "aGVsbG8="
    assert req.model == "NANO_BANANA_2_LITE"
    assert req.count == 2
    assert req.variant_count == 2


@pytest.mark.asyncio
async def test_v1_health_includes_upstream_capabilities():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/health")
        assert resp.status_code in (200, 503)
        data = resp.json()
        caps = data.get("capabilities", {})
        assert "video_text" in caps
        assert "image_upscale" in caps
        assert "image_edit" in caps


@pytest.mark.asyncio
async def test_v1_image_upscale_json_and_download(monkeypatch):
    mock_client = SimpleNamespace(
        connected=True,
        list_extensions=lambda: [{"available": True}],
        upload_image=AsyncMock(return_value={"_mediaId": "auto_uploaded_mid"}),
        upscale_image=AsyncMock(return_value={
            "status": 200,
            "data": {
                "encodedImage": base64.b64encode(b"fake_jpeg_data").decode("utf-8"),
                "mediaId": "upscaled_mid_456",
            },
        }),
    )
    monkeypatch.setattr("agent.api.v1.generations.get_flow_client", lambda: mock_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Direct upscale with media_id -> returns JSON
        resp1 = await ac.post("/v1/images/upscale", json={
            "media_id": "test_mid_123",
            "quality": "4K",
            "installation_id": "inst_profile_1",
        })
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == 200
        assert data1["data"]["mediaId"] == "upscaled_mid_456"
        mock_client.upscale_image.assert_awaited_with(
            media_id="test_mid_123",
            project_id=None,
            resolution="4K",
            preferred_installation="inst_profile_1",
        )

        # 2. Upscale with image_base64 auto-upload and download=True -> returns binary
        resp2 = await ac.post("/v1/images/upscale", json={
            "image_base64": base64.b64encode(b"source_image").decode("utf-8"),
            "quality": "2K",
            "download": True,
        })
        assert resp2.status_code == 200
        assert resp2.headers["content-type"] == "image/jpeg"
        assert resp2.content == b"fake_jpeg_data"
        mock_client.upload_image.assert_awaited()


@pytest.mark.asyncio
async def test_v1_image_edits_endpoint_and_dispatch(monkeypatch):
    mock_client = SimpleNamespace(
        connected=True,
        list_extensions=lambda: [{"available": True, "installation_id": "inst_1"}],
        upload_image=AsyncMock(return_value={"_mediaId": "uploaded_base_mid", "_installation_id": "inst_1", "_projectId": "proj_1"}),
        edit_image=AsyncMock(return_value={
            "status": 200,
            "data": {"media": [{"name": "result_edited_mid", "image": {"generatedImage": {"fifeUrl": "https://example.test/edited.jpg"}}}]},
        }),
    )
    monkeypatch.setattr("agent.api.v1.generations.get_flow_client", lambda: mock_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/v1/images/edits", json={
            "prompt": "Change the background to a sunset beach",
            "base_image_base64": base64.b64encode(b"base_photo").decode("utf-8"),
            "model": "banana2",
            "count": 1,
        })
        assert resp.status_code == 200
        jobs = resp.json().get("jobs", [])
        assert len(jobs) == 1
        job_id = jobs[0]["id"]
        assert jobs[0]["type"] == "image"
        assert jobs[0]["status"] == "complete"

    # Test processor dispatch for EDIT_IMAGE
    res = await _dispatch_client_v1({
        "id": job_id,
        "type": "EDIT_IMAGE",
        "payload_json": json.dumps({
            "prompt": "Change the background to a sunset beach",
            "base_image_base64": base64.b64encode(b"base_photo").decode("utf-8"),
            "model": "NANO_BANANA_2",
            "count": 1,
        }),
    }, "HORIZONTAL", SimpleNamespace(_client=mock_client))
    assert res["status"] == 200
    mock_client.upload_image.assert_awaited()
    mock_client.edit_image.assert_awaited()


@pytest.mark.asyncio
async def test_v1_text_to_video_dispatch(monkeypatch):
    mock_client = SimpleNamespace(
        generate_text_video=AsyncMock(return_value={"error": "stopped_at_submit"}),
    )
    result = await _dispatch_client_v1({
        "id": "job_t2v",
        "type": "GENERATE_VIDEO",
        "payload_json": json.dumps({
            "prompt": "A futuristic city in the clouds",
            "type": "text_to_video",
            "duration_seconds": 8,
            "resolution": "720p",
            "installation_id": "inst_target",
        }),
    }, "VERTICAL", SimpleNamespace(_client=mock_client))

    assert result["error"] == "stopped_at_submit"
    mock_client.generate_text_video.assert_awaited_with(
        prompt="A futuristic city in the clouds",
        project_id="",
        duration_s=8,
        aspect_ratio="VIDEO_ASPECT_RATIO_PORTRAIT",
        preferred_installation="inst_target",
    )


@pytest.mark.asyncio
async def test_v1_video_resolution_passed_to_model(monkeypatch):
    async def owner(_):
        return {"installation_id": "inst", "project_id": "proj"}
    monkeypatch.setattr("agent.services.execution_audit.media_owner", owner)

    mock_client = SimpleNamespace(
        generate_video=AsyncMock(return_value={"error": "stopped"}),
        upload_image=AsyncMock(return_value={"_mediaId": "mid1", "_installation_id": "inst", "_projectId": "proj"}),
    )
    # Test 360p resolution
    result = await _dispatch_client_v1({
        "id": "job_res_test",
        "type": "GENERATE_VIDEO",
        "payload_json": json.dumps({
            "prompt": "Video prompt",
            "start_media_id": "mid1",
            "duration_seconds": 6,
            "resolution": "360p",
        }),
    }, "HORIZONTAL", SimpleNamespace(_client=mock_client))

    assert result["error"] == "stopped"
    assert mock_client.generate_video.await_args.kwargs["video_model"] == "abra_i2v_6s_360p"


@pytest.mark.asyncio
async def test_v1_image_generation_with_image_url(monkeypatch):
    mock_client = SimpleNamespace(
        connected=True,
        list_extensions=lambda: [{"available": True, "installation_id": "inst_1"}],
        upload_image=AsyncMock(return_value={"_mediaId": "uploaded_mid", "_installation_id": "inst_1", "_projectId": "proj_1"}),
        generate_images=AsyncMock(return_value={
            "status": 200,
            "data": {"media": [{"name": "gen_mid", "image": {"generatedImage": {"mediaId": "gen_mid", "fifeUrl": "https://example.test/gen.jpg"}}}]},
        }),
    )
    monkeypatch.setattr("agent.api.v1.generations.get_flow_client", lambda: mock_client)
    monkeypatch.setattr("agent.api.v1.generations._fetch_url_as_base64", AsyncMock(return_value=("aGVsbG8=", "image/jpeg")))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/v1/images/generations", json={
            "prompt": "Cybernetic cat with reference url",
            "input_images": [
                {"image_url": "https://cdn.example.com/source.jpg"}
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        assert len(data["media"]) == 1
        assert data["media"][0]["url"] == "https://example.test/gen.jpg"
        mock_client.upload_image.assert_awaited()


@pytest.mark.asyncio
async def test_v1_image_upscale_with_image_url(monkeypatch):
    mock_client = SimpleNamespace(
        connected=True,
        list_extensions=lambda: [{"available": True, "installation_id": "inst_1"}],
        upload_image=AsyncMock(return_value={"_mediaId": "uploaded_mid", "_installation_id": "inst_1", "_projectId": "proj_1"}),
        upscale_image=AsyncMock(return_value={
            "status": 200,
            "data": {"media": [{"name": "upscaled_mid", "image": {"generatedImage": {"fifeUrl": "https://example.test/upscaled.jpg"}}}]},
        }),
    )
    monkeypatch.setattr("agent.api.v1.generations.get_flow_client", lambda: mock_client)
    monkeypatch.setattr("agent.api.v1.generations._fetch_url_as_base64", AsyncMock(return_value=("aGVsbG8=", "image/jpeg")))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/v1/images/upscale", json={
            "image_url": "https://cdn.example.com/photo.png",
            "upscale_factor": "2x"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == 200
        assert data["data"]["media"][0]["name"] == "upscaled_mid"
        mock_client.upload_image.assert_awaited()

