"""Tests for the Apikit V1 multi-extension routing boundary."""
from unittest.mock import AsyncMock

import pytest

import agent.services.v1_multi_extension as routing
from agent.services.v1_multi_extension import (
    V1MultiExtensionRouter,
    V1RoutingError,
)


class FakeClient:
    def __init__(self):
        self.connected = True
        self.ws_a = object()
        self.ws_b = object()
        self._extensions = {
            self.ws_a: {
                "installation_id": "inst_a",
                "flow_project_id": "11111111-1111-4111-8111-111111111111",
                "quota_exhausted": False,
                "unavailable_until": 0,
                "available": True,
            },
            self.ws_b: {
                "installation_id": "inst_b",
                "flow_project_id": "22222222-2222-4222-8222-222222222222",
                "quota_exhausted": False,
                "unavailable_until": 0,
                "available": True,
            },
        }
        self.exhausted = set()
        self.uploads = []

    def list_extensions(self):
        result = []
        for sess in self._extensions.values():
            result.append({
                "installation_id": sess["installation_id"],
                "flow_project_id": sess["flow_project_id"],
                "available": sess["installation_id"] not in self.exhausted,
            })
        return result

    def is_installation_exhausted(self, installation_id):
        return installation_id in self.exhausted

    def _select_extension(
        self,
        require_token,
        preferred_installation=None,
        preferred_project_id=None,
    ):
        candidates = [
            (ws, sess)
            for ws, sess in self._extensions.items()
            if sess["installation_id"] not in self.exhausted
        ]
        if preferred_installation:
            for ws, sess in candidates:
                if sess["installation_id"] == preferred_installation:
                    return ws
        if preferred_project_id:
            for ws, sess in candidates:
                if sess["flow_project_id"] == preferred_project_id:
                    return ws
        return candidates[0][0] if candidates else None

    def _batch_project_id(self, project_id, preferred_installation=None):
        if preferred_installation:
            for sess in self._extensions.values():
                if sess["installation_id"] == preferred_installation:
                    return sess["flow_project_id"]
        return project_id

    async def upload_image(
        self,
        image_base64,
        mime_type="image/jpeg",
        project_id="",
        file_name="image.jpg",
        preferred_installation=None,
    ):
        self.uploads.append({
            "image_base64": image_base64,
            "mime_type": mime_type,
            "project_id": project_id,
            "file_name": file_name,
            "preferred_installation": preferred_installation,
        })
        return {
            "status": 200,
            "_mediaId": "media-1",
            "_installation_id": preferred_installation,
        }


@pytest.mark.asyncio
async def test_resolve_context_selects_profile_and_project():
    client = FakeClient()
    router = V1MultiExtensionRouter(client)

    ctx = await router.resolve_context({})

    assert ctx.installation_id == "inst_a"
    assert ctx.project_id == "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_exhausted_previous_executor_can_failover():
    client = FakeClient()
    client.exhausted.add("inst_a")
    router = V1MultiExtensionRouter(client)

    ctx = await router.resolve_context({
        "installation_id": "inst_a",
        "project_id": "11111111-1111-4111-8111-111111111111",
    })

    assert ctx.installation_id == "inst_b"
    assert ctx.project_id == "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_uuid_media_owner_pins_execution_profile(monkeypatch):
    client = FakeClient()
    router = V1MultiExtensionRouter(client)
    monkeypatch.setattr(
        routing,
        "media_owner",
        AsyncMock(return_value={
            "installation_id": "inst_b",
            "project_id": "22222222-2222-4222-8222-222222222222",
        }),
    )

    ctx = await router.resolve_context({
        "reference_media_ids": ["media-owned-by-b"],
    })

    assert ctx.installation_id == "inst_b"
    assert ctx.project_id == "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_conflicting_media_owner_is_rejected(monkeypatch):
    client = FakeClient()
    router = V1MultiExtensionRouter(client)
    monkeypatch.setattr(
        routing,
        "media_owner",
        AsyncMock(return_value={
            "installation_id": "inst_b",
            "project_id": "22222222-2222-4222-8222-222222222222",
        }),
    )

    with pytest.raises(V1RoutingError, match="MEDIA_ACCOUNT_MISMATCH"):
        await router.resolve_context({
            "installation_id": "inst_a",
            "reference_media_ids": ["media-owned-by-b"],
        })


@pytest.mark.asyncio
async def test_upload_delegates_without_changing_contract():
    client = FakeClient()
    router = V1MultiExtensionRouter(client)

    result = await router.upload_image(
        image_base64="aGVsbG8=",
        mime_type="image/png",
        project_id="11111111-1111-4111-8111-111111111111",
        file_name="reference.png",
        preferred_installation="inst_a",
    )

    assert result["_mediaId"] == "media-1"
    assert client.uploads[0]["preferred_installation"] == "inst_a"
    assert client.uploads[0]["file_name"] == "reference.png"


@pytest.mark.asyncio
async def test_single_client_compatibility_without_pool():
    class SingleClient:
        connected = True

        def list_extensions(self):
            return [{"available": True}]

    router = V1MultiExtensionRouter(SingleClient())
    ctx = await router.resolve_context({
        "installation_id": "legacy-inst",
        "project_id": "legacy-project",
    })

    assert ctx.installation_id == "legacy-inst"
    assert ctx.project_id == "legacy-project"
