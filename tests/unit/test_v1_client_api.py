"""Unit tests for FlowKit Client API v1 (/v1/...)."""
import asyncio
import json
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport

from agent.main import app
from agent.db.schema import init_db, close_db, get_db, _db_lock
from agent.db import crud
from agent.worker.processor import _dispatch_client_v1


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_flowkit_v1.db")
    monkeypatch.setattr("agent.config.DB_PATH", test_db)
    monkeypatch.setattr("agent.db.schema.DB_PATH", test_db)
    await init_db()
    yield
    await close_db()


class FakeOps:
    def __init__(self):
        self.uploaded = []
        self.generated_images = []
        self.generated_videos = []
        self._client = self

    async def upload_image(self, image_base64, mime_type="image/jpeg", project_id="0", file_name="image.jpg", preferred_installation=None):
        self.uploaded.append({"base64": image_base64, "mime": mime_type})
        return {"status": 200, "_mediaId": "media-uuid-1234"}

    async def generate_images(self, prompt, project_id="0", aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                              character_media_ids=None, image_model=None, preferred_installation=None, count=1):
        self.generated_images.append({
            "prompt": prompt, "aspect": aspect_ratio, "refs": character_media_ids, "model": image_model, "count": count
        })
        return {
            "status": 200,
            "data": {
                "media": [
                    {
                        "name": "media-uuid-gen-5678",
                        "image": {
                            "generatedImage": {
                                "mediaId": "media-uuid-gen-5678",
                                "fifeUrl": "https://storage.googleapis.com/test/media-uuid-gen-5678.jpg",
                            }
                        }
                    }
                ]
            }
        }

    async def generate_video(self, start_image_media_id, prompt, project_id="0", scene_id="",
                             aspect_ratio="VIDEO_ASPECT_RATIO_PORTRAIT", end_image_media_id=None):
        self.generated_videos.append({
            "start": start_image_media_id, "prompt": prompt, "aspect": aspect_ratio
        })
        return {
            "status": 200,
            "data": {
                "operations": [
                    {
                        "operation": {
                            "name": "op_video_1234",
                            "metadata": {
                                "video": {
                                    "mediaId": "video-uuid-9999",
                                    "fifeUrl": "https://storage.googleapis.com/test/video-uuid-9999.mp4",
                                }
                            }
                        },
                        "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL"
                    }
                ]
            }
        }

    async def generate_video_from_references(self, reference_media_ids, prompt, project_id="0",
                                             scene_id="", aspect_ratio="VIDEO_ASPECT_RATIO_PORTRAIT"):
        return await self.generate_video(reference_media_ids[0], prompt, project_id, scene_id, aspect_ratio)


@pytest.mark.asyncio
async def test_image_generation_endpoint_and_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Submit Image Generation
        resp = await ac.post("/v1/images/generations", json={
            "prompt": "A cybernetic tiger walking in neon jungle",
            "aspect_ratio": "16:9",
            "input_images": [
                {"image_base64": "aGVsbG8=", "mime_type": "image/png"}
            ]
        })
        assert resp.status_code == 202
        body = resp.json()
        job_id = body["job_id"]
        assert body["status"] == "queued"
        assert body["type"] == "image"

        # 2. Check Job Status via POST
        status_resp = await ac.post("/v1/jobs/status", json={"job_id": job_id})
        assert status_resp.status_code == 200
        status_body = status_resp.json()
        assert status_body["job_id"] == job_id
        assert status_body["status"] == "queued"

        # 3. Check Job Status via GET
        get_resp = await ac.get(f"/v1/jobs/{job_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "queued"

        # 4. Simulate Processor completing the job
        await crud.update_request(
            job_id,
            status="COMPLETED",
            media_id="media-uuid-gen-5678",
            output_url="https://storage.googleapis.com/test/media-uuid-gen-5678.jpg"
        )

        completed_resp = await ac.get(f"/v1/jobs/{job_id}")
        assert completed_resp.status_code == 200
        comp_body = completed_resp.json()
        assert comp_body["status"] == "complete"
        assert len(comp_body["media"]) == 1
        assert comp_body["media"][0]["url"] == "https://storage.googleapis.com/test/media-uuid-gen-5678.jpg"
        assert comp_body["media"][0]["media_id"] == "media-uuid-gen-5678"


@pytest.mark.asyncio
async def test_video_generation_endpoint_and_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Submit Video Generation
        resp = await ac.post("/v1/videos/generations", json={
            "prompt": "Cybernetic tiger leaps forward",
            "type": "image_to_video",
            "aspect_ratio": "9:16",
            "input_images": [
                {"image_base64": "aGVsbG8=", "mime_type": "image/jpeg"}
            ]
        })
        assert resp.status_code == 202
        body = resp.json()
        job_id = body["job_id"]
        assert body["status"] == "queued"
        assert body["type"] == "video"

        # Verify query returns queued
        stat_resp = await ac.get(f"/v1/jobs/{job_id}")
        assert stat_resp.status_code == 200
        assert stat_resp.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_video_job_exposes_flow_operation_id_after_submission():
    """The client polls the local job ID, while Flow's handle is observable."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        submitted = await ac.post("/v1/videos/generations", json={
            "prompt": "A fox runs through a forest",
            "input_images": [{"image_base64": "aGVsbG8=", "mime_type": "image/jpeg"}],
        })
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        assert submitted.json()["operation_id"] is None

        await crud.update_request(
            job_id,
            status="PROCESSING",
            request_id="operations/flow-video-1234",
        )

        polled = await ac.get(f"/v1/jobs/{job_id}")
        assert polled.status_code == 200
        body = polled.json()
        assert body["status"] == "running"
        assert body["operation_id"] == "operations/flow-video-1234"
        assert body["jobs"][0]["operation_id"] == "operations/flow-video-1234"


@pytest.mark.asyncio
async def test_v1_omni_r2v_base64_uses_selected_profile_and_persists_workflow(monkeypatch):
    """Regression: an installation-scoped R2V job must not use `client` before assignment."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        submitted = await ac.post("/v1/videos/generations", json={
            "prompt": "A paper boat crosses a calm puddle",
            "generation_type": "reference_to_video",
            "model": "omni_flash",
            "installation_id": "install-a",
            "input_images": [{"role": "reference", "image_base64": "aGVsbG8=", "mime_type": "image/png"}],
        })
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]

        class SelectedProfileClient:
            _extensions = {"profile-a": {"installation_id": "install-a", "flow_project_id": "11111111-1111-4111-8111-111111111111"}}

            def is_installation_exhausted(self, installation_id):
                return False

            def _select_extension(self, require_token, preferred_installation=None, preferred_project_id=None):
                assert preferred_installation == "install-a"
                return "profile-a"

            def _batch_project_id(self, project_id, preferred_installation=None):
                assert preferred_installation == "install-a"
                return "11111111-1111-4111-8111-111111111111"

        client = SelectedProfileClient()
        client.upload_image = AsyncMock(return_value={
            "_mediaId": "22222222-2222-4222-8222-222222222222",
            "_installation_id": "install-a",
            "_projectId": "11111111-1111-4111-8111-111111111111",
        })
        client.generate_video_from_references = AsyncMock(return_value={
            "status": 200,
            "data": {"operations": [{"operation": {"name": "workflows/omni-r2v-1"}, "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL"}]},
            "_installation_id": "install-a",
        })
        client.generate_video = AsyncMock()

        req = await crud.get_request(job_id)
        result = await _dispatch_client_v1(req, "HORIZONTAL", type("Ops", (), {"_client": client})())

        assert result["data"]["operations"][0]["operation"]["name"] == "workflows/omni-r2v-1"
        assert client.upload_image.await_args.kwargs["preferred_installation"] == "install-a"
        assert client.generate_video_from_references.await_args.kwargs == {
            "reference_media_ids": ["22222222-2222-4222-8222-222222222222"],
            "prompt": "A paper boat crosses a calm puddle",
            "project_id": "11111111-1111-4111-8111-111111111111",
            "scene_id": "",
            "aspect_ratio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
            "video_model": "abra_r2v_8s",
            "preferred_installation": "install-a",
        }
        stored = await crud.get_request(job_id)
        assert stored["request_id"] == "workflows/omni-r2v-1"
        assert json.loads(stored["payload_json"])["input_images"][0]["media_id"] == "22222222-2222-4222-8222-222222222222"

        # Client polling reads the same job record and exposes completed media.
        await crud.update_request(job_id, status="COMPLETED", media_id="33333333-3333-4333-8333-333333333333", output_url="https://example.test/video.mp4")
        polled = await ac.post("/v1/jobs/status", json={"job_id": job_id})
        assert polled.status_code == 200
        assert polled.json()["status"] == "complete"
        assert polled.json()["media"][0]["type"] == "video"
        assert polled.json()["media"][0]["url"] == "https://example.test/video.mp4"


@pytest.mark.asyncio
async def test_v1_rejects_direct_media_ids():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Video endpoint rejects start_media_id
        resp1 = await ac.post("/v1/videos/generations", json={
            "prompt": "Test video",
            "start_media_id": "c1611a51-bb44-42b7-84bc-2e997f7bb194",
        })
        assert resp1.status_code == 422
        assert "Direct media IDs" in resp1.text

        # Video endpoint rejects reference_media_ids
        resp2 = await ac.post("/v1/videos/generations", json={
            "prompt": "Test video",
            "reference_media_ids": ["c1611a51-bb44-42b7-84bc-2e997f7bb194"],
        })
        assert resp2.status_code == 422
        assert "Direct media IDs" in resp2.text

        # Video endpoint rejects input_images missing image_base64
        resp3 = await ac.post("/v1/videos/generations", json={
            "prompt": "Test video",
            "input_images": [{"media_id": "c1611a51-bb44-42b7-84bc-2e997f7bb194"}],
        })
        assert resp3.status_code == 422

        # Image endpoint rejects reference_media_ids
        resp4 = await ac.post("/v1/images/generations", json={
            "prompt": "Test image",
            "reference_media_ids": ["c1611a51-bb44-42b7-84bc-2e997f7bb194"],
        })
        assert resp4.status_code == 422
        assert "Direct reference_media_ids are not allowed" in resp4.text



@pytest.mark.asyncio
async def test_character_crud_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create Character
        create_resp = await ac.post("/v1/characters", json={
            "name": "General Victor",
            "description": "A seasoned commander with scarred silver armor",
            "image_prompt": "Portrait of veteran commander, silver armor",
            "entity_type": "character"
        })
        assert create_resp.status_code == 201
        char = create_resp.json()
        char_id = char["id"]
        assert char["name"] == "General Victor"

        # 2. Get Character
        get_resp = await ac.get(f"/v1/characters/{char_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == char_id

        # 3. List Characters
        list_resp = await ac.get("/v1/characters")
        assert list_resp.status_code == 200
        assert any(c["id"] == char_id for c in list_resp.json())

        # 4. Update Character
        update_resp = await ac.patch(f"/v1/characters/{char_id}", json={
            "description": "Updated veteran commander"
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["description"] == "Updated veteran commander"

        # 5. Delete Character
        del_resp = await ac.delete(f"/v1/characters/{char_id}")
        assert del_resp.status_code == 204

        # 6. Verify 404 after delete
        not_found = await ac.get(f"/v1/characters/{char_id}")
        assert not_found.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_client_v1_auto_uploads_base64():
    ops = FakeOps()
    req = {
        "id": "job_test_123",
        "type": "GENERATE_IMAGE",
        "project_id": "0",
        "payload_json": json.dumps({
            "prompt": "Warrior in golden armor",
            "aspect_ratio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            "input_images": [
                {"image_base64": "dGVzdF9pbWFnZQ==", "mime_type": "image/png"}
            ]
        })
    }

    result = await _dispatch_client_v1(req, "HORIZONTAL", ops)
    assert result.get("status") == 200
    # Verified: uploaded base64 to Flow session
    assert len(ops.uploaded) == 1
    assert ops.uploaded[0]["base64"] == "dGVzdF9pbWFnZQ=="
    # Verified: generated images using uploaded media ID as reference
    assert len(ops.generated_images) == 1
    assert "media-uuid-1234" in ops.generated_images[0]["refs"]


@pytest.mark.asyncio
async def test_health_endpoints_parity():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # GET /health/live
        live_resp = await ac.get("/health/live")
        assert live_resp.status_code == 200
        assert live_resp.json() == {"status": "ok"}

        # GET /health/ready (waiting_for_provider when no ext connected)
        ready_resp = await ac.get("/health/ready")
        assert ready_resp.status_code in (200, 503)
        ready_data = ready_resp.json()
        assert "status" in ready_data
        assert "job_queue_capacity" in ready_data

        # GET /api/health
        api_h_resp = await ac.get("/api/health")
        assert api_h_resp.status_code == 200
        assert "ok" in api_h_resp.json()


@pytest.mark.asyncio
async def test_multi_job_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Queue image generation
        gen_resp = await ac.post("/v1/images/generations", json={
            "prompt": "Casting lightning spell",
            "aspect_ratio": "16:9"
        })
        assert gen_resp.status_code == 202
        body = gen_resp.json()
        assert "jobs" in body
        assert len(body["jobs"]) == 1
        job_id = body["jobs"][0]["id"]
        assert body["metadata"]["counts"]["queued"] >= 1

        # Query batch job status
        batch_resp = await ac.post("/v1/jobs/status", json={"job_ids": [job_id, "job_nonexistent"]})
        assert batch_resp.status_code == 200
        batch_data = batch_resp.json()
        assert len(batch_data["jobs"]) == 2
        assert batch_data["jobs"][0]["id"] == job_id
        assert batch_data["jobs"][0]["status"] == "queued"
        assert batch_data["jobs"][1]["status"] == "failed"
        assert batch_data["jobs"][1]["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_v1_images_generations_batch():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Submit batch with {"requests": [...]}
        resp = await ac.post("/v1/images/generations/batch", json={
            "requests": [
                {"prompt": "Batch image prompt 1", "aspect_ratio": "16:9"},
                {"prompt": "Batch image prompt 2", "aspect_ratio": "9:16"},
            ]
        })
        assert resp.status_code == 202
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) == 2
        j1 = data["jobs"][0]
        j2 = data["jobs"][1]
        assert j1["type"] == "image"
        assert j1["status"] == "queued"
        assert j2["type"] == "image"
        assert j2["status"] == "queued"

        # Verify polling with /v1/jobs/status works
        poll_resp = await ac.post("/v1/jobs/status", json={"job_ids": [j1["id"], j2["id"]]})
        assert poll_resp.status_code == 200
        poll_data = poll_resp.json()
        assert len(poll_data["jobs"]) == 2
        assert poll_data["jobs"][0]["id"] == j1["id"]
        assert poll_data["jobs"][1]["id"] == j2["id"]


@pytest.mark.asyncio
async def test_v1_videos_generations_batch():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Submit batch directly as list [...]
        resp = await ac.post("/v1/videos/generations/batch", json=[
            {"prompt": "Batch video prompt 1", "duration_seconds": 8, "aspect_ratio": "16:9"},
            {"prompt": "Batch video prompt 2", "duration_seconds": 4, "aspect_ratio": "9:16"},
        ])
        assert resp.status_code == 202
        data = resp.json()
        assert "jobs" in data
        assert len(data["jobs"]) == 2
        j1 = data["jobs"][0]
        j2 = data["jobs"][1]
        assert j1["type"] == "video"
        assert j1["status"] == "queued"
        assert j2["type"] == "video"
        assert j2["status"] == "queued"

        # Each batch job gets its own Flow operation ID after submission.
        await crud.update_request(j1["id"], status="PROCESSING", request_id="operations/flow-video-1")
        await crud.update_request(j2["id"], status="PROCESSING", request_id="operations/flow-video-2")

        poll_resp = await ac.post("/v1/jobs/status", json={"job_ids": [j1["id"], j2["id"]]})
        assert poll_resp.status_code == 200
        poll_data = poll_resp.json()
        assert [job["operation_id"] for job in poll_data["jobs"]] == [
            "operations/flow-video-1",
            "operations/flow-video-2",
        ]
        # Top-level convenience fields deliberately mirror the first job.
        assert poll_data["operation_id"] == "operations/flow-video-1"


@pytest.mark.asyncio
async def test_v1_batch_empty_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Empty object requests
        resp_obj = await ac.post("/v1/images/generations/batch", json={"requests": []})
        assert resp_obj.status_code == 422

        # Empty list
        resp_list = await ac.post("/v1/videos/generations/batch", json=[])
        assert resp_list.status_code == 422


def test_flowkit_client_batch_methods(monkeypatch):
    from flowkit_client import FlowKitClient
    client = FlowKitClient(base_url="http://test")
    recorded = []

    def fake_request(method, endpoint, **kwargs):
        recorded.append((method, endpoint, kwargs))
        if endpoint == "/v1/jobs/status":
            return {"jobs": [{"id": jid, "status": "complete"} for jid in kwargs["json_data"]["job_ids"]], "metadata": {"done": True}}
        return {"jobs": [{"id": "job_1", "status": "queued"}], "metadata": {"done": False}}

    monkeypatch.setattr(client, "_request", fake_request)

    # Test batch image generation
    res_img = client.v1_generate_images_batch([{"prompt": "img 1"}, {"prompt": "img 2"}])
    assert recorded[-1][0] == "POST"
    assert recorded[-1][1] == "/v1/images/generations/batch"
    assert "requests" in recorded[-1][2]["json_data"]

    # Test batch video generation
    res_vid = client.v1_generate_videos_batch([{"prompt": "vid 1"}])
    assert recorded[-1][0] == "POST"
    assert recorded[-1][1] == "/v1/videos/generations/batch"

    # Test get_jobs and poll_jobs
    jobs_res = client.v1_get_jobs(["job_1", "job_2"])
    assert recorded[-1][1] == "/v1/jobs/status"
    assert recorded[-1][2]["json_data"] == {"job_ids": ["job_1", "job_2"]}

    poll_res = client.v1_poll_jobs(["job_1", "job_2"], interval=0.01)
    assert poll_res["metadata"]["done"] is True




@pytest.mark.asyncio
async def test_image_generation_with_count_multiple_media():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Submit with count=4
        resp = await ac.post("/v1/images/generations", json={
            "prompt": "4 cats in different costumes",
            "aspect_ratio": "16:9",
            "count": 4,
        })
        assert resp.status_code == 202
        body = resp.json()
        job_id = body["job_id"]
        assert body["status"] == "queued"

        # Verify payload_json stored count=4
        req = await crud.get_request(job_id)
        assert req is not None
        pj = json.loads(req["payload_json"])
        assert pj["count"] == 4

        # 2. Simulate worker completing request with 4 generated images
        from agent.worker.processor import _complete_request
        mock_result = {
            "status": 200,
            "_installation_id": "inst_123",
            "data": {
                "media": [
                    {
                        "name": f"uuid-cat-{i}",
                        "image": {
                            "generatedImage": {
                                "mediaId": f"uuid-cat-{i}",
                                "fifeUrl": f"https://storage.googleapis.com/test/cat_{i}.jpg",
                            }
                        }
                    }
                    for i in range(1, 5)
                ]
            }
        }
        await _complete_request(req, "HORIZONTAL", mock_result)

        # 3. Query GET /v1/jobs/{job_id}
        get_resp = await ac.get(f"/v1/jobs/{job_id}")
        assert get_resp.status_code == 200
        comp_body = get_resp.json()
        assert comp_body["status"] == "complete"
        assert len(comp_body["media"]) == 4
        for i, m in enumerate(comp_body["media"], start=1):
            assert m["media_id"] == f"uuid-cat-{i}"
            assert m["url"] == f"https://storage.googleapis.com/test/cat_{i}.jpg"
            assert m["type"] == "image"


@pytest.mark.asyncio
async def test_image_generation_count_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # count > 4 rejected
        r1 = await ac.post("/v1/images/generations", json={"prompt": "test", "count": 5})
        assert r1.status_code == 422

        # count < 1 rejected
        r2 = await ac.post("/v1/images/generations", json={"prompt": "test", "count": 0})
        assert r2.status_code == 422

        # conflicting count and variant_count rejected
        r3 = await ac.post("/v1/images/generations", json={"prompt": "test", "count": 2, "variant_count": 3})
        assert r3.status_code == 422

        # variant_count alias synced to count
        r4 = await ac.post("/v1/images/generations", json={"prompt": "test", "variant_count": 3})
        assert r4.status_code == 202
        req = await crud.get_request(r4.json()["job_id"])
        assert json.loads(req["payload_json"])["count"] == 3
