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
@pytest.mark.parametrize("request_type,payload,error", [
    ("GENERATE_VIDEO", {"end_media_id": "end", "start_media_id": "start"}, "OMNI_START_END_NOT_CAPTURED"),
    ("GENERATE_VIDEO_REFS", {"reference_media_ids": ["reference"]}, "OMNI_R2V_NOT_CAPTURED"),
])
async def test_v1_never_degrades_omni_modes(request_type, payload, error, monkeypatch):
    async def owner(_):
        return {"installation_id": "install", "project_id": "project"}
    monkeypatch.setattr("agent.services.execution_audit.media_owner", owner)
    client = SimpleNamespace(generate_video=AsyncMock(), generate_video_from_references=AsyncMock())
    result = await _dispatch_client_v1({"id": "job", "type": request_type, "payload_json": json.dumps({
        "prompt": "x", "model": "omni_flash", **payload,
    })}, "HORIZONTAL", SimpleNamespace(_client=client))
    assert error in result["error"]
    client.generate_video.assert_not_awaited()
    client.generate_video_from_references.assert_not_awaited()
