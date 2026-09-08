import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agent.api.v1.schemas import VideoGenerationRequest
from agent.worker.processor import _dispatch_client_v1


def test_v1_rejects_non_omni_video_models():
    assert VideoGenerationRequest(prompt="x", model="omni").model == "omni_flash"
    with pytest.raises(ValidationError):
        VideoGenerationRequest(prompt="x", model="veo_3_1_i2v_lite")


@pytest.mark.asyncio
async def test_v1_first_frame_uses_abra_without_veo(monkeypatch):
    client = SimpleNamespace(
        upload_image=AsyncMock(return_value={"_mediaId": "start", "_installation_id": "install", "_projectId": "project"}),
        generate_video=AsyncMock(return_value={"error": "stop"}),
        generate_video_from_references=AsyncMock(),
    )
    result = await _dispatch_client_v1({"id": "job", "type": "GENERATE_VIDEO", "payload_json": json.dumps({
        "prompt": "x", "model": "omni_flash", "duration_seconds": 6,
        "input_images": [{"image_base64": "YQ==", "role": "start_frame"}],
    })}, "HORIZONTAL", SimpleNamespace(_client=client))
    assert result["error"] == "stop"
    assert client.generate_video.await_args.kwargs["video_model"] == "abra_i2v_6s"
    client.generate_video_from_references.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_r2v_uses_abra_r2v(monkeypatch):
    async def owner(_):
        return {"installation_id": "install", "project_id": "project"}
    monkeypatch.setattr("agent.services.execution_audit.media_owner", owner)
    client = SimpleNamespace(
        upload_image=AsyncMock(return_value={"_mediaId": "ref1", "_installation_id": "install", "_projectId": "project"}),
        generate_video=AsyncMock(),
        generate_video_from_references=AsyncMock(return_value={"error": "stop"}),
    )
    result = await _dispatch_client_v1({"id": "job", "type": "GENERATE_VIDEO_REFS", "payload_json": json.dumps({
        "prompt": "ok hehe", "model": "omni_flash", "duration_seconds": 4,
        "reference_media_ids": ["ref1", "ref2"],
    })}, "VERTICAL", SimpleNamespace(_client=client))
    assert result["error"] == "stop"
    assert client.generate_video_from_references.await_args.kwargs["video_model"] == "abra_r2v_4s"
    assert client.generate_video_from_references.await_args.kwargs["reference_media_ids"] == ["ref1", "ref2"]
    client.generate_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_v1_start_end_not_captured(monkeypatch):
    async def owner(_):
        return {"installation_id": "install", "project_id": "project"}
    monkeypatch.setattr("agent.services.execution_audit.media_owner", owner)
    client = SimpleNamespace(generate_video=AsyncMock(), generate_video_from_references=AsyncMock())
    result = await _dispatch_client_v1({"id": "job", "type": "GENERATE_VIDEO", "payload_json": json.dumps({
        "prompt": "x", "model": "omni_flash", "start_media_id": "start", "end_media_id": "end",
    })}, "HORIZONTAL", SimpleNamespace(_client=client))
    assert "OMNI_START_END_NOT_CAPTURED" in result["error"]
    client.generate_video.assert_not_awaited()
    client.generate_video_from_references.assert_not_awaited()
