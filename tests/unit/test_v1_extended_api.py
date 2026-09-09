"""Unit tests for extended FlowKit Client API v1 endpoints (Materials, Audio, Concat, Characters)."""
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport

from agent.main import app
from agent.db.schema import init_db, close_db


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_flowkit_v1_ext.db")
    monkeypatch.setattr("agent.config.DB_PATH", test_db)
    monkeypatch.setattr("agent.db.schema.DB_PATH", test_db)
    await init_db()
    yield
    await close_db()


@pytest.mark.asyncio
async def test_v1_materials_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. List materials
        resp = await ac.get("/v1/materials")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        ids = [m["id"] for m in data]
        assert "realistic" in ids
        assert "anime" in ids

        # 2. Get specific material
        resp_single = await ac.get("/v1/materials/realistic")
        assert resp_single.status_code == 200
        mat = resp_single.json()
        assert mat["id"] == "realistic"
        assert "style_instruction" in mat

        # 3. Not found
        resp_404 = await ac.get("/v1/materials/nonexistent_material_123")
        assert resp_404.status_code == 404


@pytest.mark.asyncio
async def test_v1_audio_voices_and_speech():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. List voices
        resp = await ac.get("/v1/audio/voices")
        assert resp.status_code == 200
        voices = resp.json()
        assert isinstance(voices, list)
        assert len(voices) > 0

        # 2. Generate speech
        speech_resp = await ac.post("/v1/audio/speech", json={
            "text": "Hello world from FlowKit Client API V1",
            "voice_id": "narrator_calm",
            "speed": 1.0,
        })
        assert speech_resp.status_code == 201
        speech_data = speech_resp.json()
        assert "id" in speech_data
        assert "audio_url" in speech_data
        assert speech_data["id"].startswith("tts_")


@pytest.mark.asyncio
async def test_v1_characters_crud():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. List characters initially empty
        resp = await ac.get("/v1/characters")
        assert resp.status_code == 200
        assert resp.json() == []

        # 2. Create character
        create_resp = await ac.post("/v1/characters", json={
            "name": "Commander Ray",
            "description": "A seasoned space commander with silver hair",
            "image_prompt": "Portrait of space commander, silver hair, futuristic navy uniform",
            "entity_type": "character"
        })
        assert create_resp.status_code == 201
        char = create_resp.json()
        char_id = char["id"]
        assert char["name"] == "Commander Ray"

        # 3. Get character
        get_resp = await ac.get(f"/v1/characters/{char_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == char_id

        # 4. Patch character
        patch_resp = await ac.patch(f"/v1/characters/{char_id}", json={
            "description": "Updated description"
        })
        assert patch_resp.status_code == 200
        assert patch_resp.json()["description"] == "Updated description"

        # 5. Delete character
        del_resp = await ac.delete(f"/v1/characters/{char_id}")
        assert del_resp.status_code == 204


@pytest.mark.asyncio
async def test_v1_concat_endpoint(tmp_path, monkeypatch):
    from unittest.mock import patch
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Test unreachable URL returns 400
        resp = await ac.post("/v1/videos/concat", json={
            "video_urls": ["http://invalid-nonexistent-domain-12345/video.mp4"]
        })
        assert resp.status_code == 400

        # Test successful concat with mock
        dummy_file = tmp_path / "dummy.mp4"
        dummy_file.write_bytes(b"dummy video content")

        async def fake_dl(url, dest):
            dest.write_bytes(b"dummy clip content")

        def fake_merge(inputs, output):
            Path(output).write_bytes(b"merged video content")
            return True

        with patch("agent.api.v1.concat._download_file", side_effect=fake_dl), \
             patch("agent.api.v1.concat.merge_videos", side_effect=fake_merge):
            resp_ok = await ac.post("/v1/videos/concat", json={
                "video_urls": ["http://test-server/clip1.mp4", "http://test-server/clip2.mp4"]
            })
            assert resp_ok.status_code == 201
            data = resp_ok.json()
            assert data["status"] == "complete"
            assert "video_url" in data
            assert "/v1/videos/download/" in data["video_url"]

