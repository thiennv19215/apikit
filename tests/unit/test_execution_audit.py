import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from agent.db import crud, schema
from agent.services import execution_audit as audit
from agent.services.flow_client import FlowClient
from agent.worker.processor import _dispatch_client_v1


@pytest.fixture
async def database(monkeypatch):
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(schema.SCHEMA)
    monkeypatch.setattr(schema, "_db_connection", db)
    monkeypatch.setattr(schema, "_db_lock", asyncio.Lock())
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_concurrent_logs_are_correlated_and_exposed(database):
    from agent.main import app

    await database.executemany("INSERT INTO request (id,type) VALUES (?, 'GENERATE_IMAGE')", [("j1",), ("j2",)])
    await database.commit()

    async def execute(job_id, installation):
        token = audit.current_job.set(job_id)
        try:
            call = await audit.start_call("image_rpc", installation, "project")
            await asyncio.sleep(0)
            await audit.finish_call(call, {"status": 200})
        finally:
            audit.current_job.reset(token)

    await asyncio.gather(execute("j1", "A"), execute("j2", "B"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        logs = (await client.get("/v1/jobs/j1/executions")).json()["executions"]
        assert len(logs) == 1
        assert logs[0]["installation_id"] == "A"
        assert logs[0]["finished_at"]
        assert logs[0]["status"] == "returned"
        assert (await client.get("/v1/jobs/j1")).json()["installation_id"] == "A"
        assert (await client.get("/v1/jobs/missing/executions")).status_code == 404
    assert (await audit.list_calls("j2"))[0]["installation_id"] == "B"


@pytest.mark.asyncio
async def test_bound_payload_does_not_failover_to_another_account(database):
    client = FlowClient()

    class Socket:
        def __init__(self):
            self.calls = 0

        async def send(self, payload):
            self.calls += 1
            await client.handle_message({"id": json.loads(payload)["id"],
                                         "error": "PUBLIC_ERROR_USER_QUOTA_REACHED"}, self)

    a, b = Socket(), Socket()
    client.set_extension(a)
    client.set_extension(b)
    client._extensions[a]["installation_id"] = "A"
    client._extensions[b]["installation_id"] = "B"
    await database.execute("INSERT INTO request (id,type) VALUES ('j', 'GENERATE_IMAGE')")
    await database.commit()
    token = audit.current_job.set("j")
    try:
        result = await client._send("batch", {}, preferred_installation="A", preferred_project_id="P")
    finally:
        audit.current_job.reset(token)
    assert result["error"] == "PUBLIC_ERROR_USER_QUOTA_REACHED"
    assert a.calls == 1 and b.calls == 0
    logs = await audit.list_calls("j")
    assert logs[0]["error_code"] == "PUBLIC_ERROR_USER_QUOTA_REACHED"
    assert logs[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_unknown_and_mixed_owner_media_are_rejected(database):
    async def dispatch(payload):
        return await _dispatch_client_v1({"id": "j", "type": "GENERATE_IMAGE", "payload_json": json.dumps(payload)},
                                         "HORIZONTAL", SimpleNamespace(_client=object()))

    assert "MEDIA_OWNER_UNKNOWN" in (await dispatch({"reference_media_ids": ["unknown"]}))["error"]
    await audit.remember_media("m1", "A", "p1")
    await audit.remember_media("m2", "B", "p2")
    result = await dispatch({"reference_media_ids": ["m1", "m2"]})
    assert "MEDIA_ACCOUNT_MISMATCH" in result["error"]


@pytest.mark.asyncio
async def test_base64_retry_reuploads_and_uses_same_account_for_generation(database):
    class Client:
        def __init__(self):
            self.profile = "A"
            self.calls = []

        async def upload_image(self, **kwargs):
            self.calls.append(("upload", kwargs["image_base64"]))
            return {"_mediaId": "media-" + self.profile, "_installation_id": self.profile, "_projectId": "p-" + self.profile}

        async def generate_images(self, **kwargs):
            self.calls.append(("generate", kwargs))
            return {"status": 200}

    payload = {"input_images": [{"image_base64": "original", "mime_type": "image/png"}]}
    await database.execute("INSERT INTO request (id,type,payload_json) VALUES ('j','GENERATE_IMAGE',?)", (json.dumps(payload),))
    await database.commit()
    client = Client()
    for profile in ("A", "B"):
        client.profile = profile
        req = await crud.get_request("j")
        await _dispatch_client_v1(req, "HORIZONTAL", SimpleNamespace(_client=client))
        generated = client.calls[-1][1]
        assert generated["preferred_installation"] == profile
        assert generated["character_media_ids"] == ["media-" + profile]
    assert [c for c in client.calls if c[0] == "upload"] == [("upload", "original"), ("upload", "original")]
    assert "image_base64" in json.loads((await crud.get_request("j"))["payload_json"])["input_images"][0]


@pytest.mark.asyncio
async def test_cache_does_not_reuse_unscoped_media(database):
    await crud.set_cached_media_id("hash", "0", "old-media")
    assert await crud.get_cached_media_id("hash", "another-project") is None


@pytest.mark.asyncio
async def test_quota_retry_selects_b_and_reuploads_source(database, monkeypatch):
    from agent.services import flow_batch as fb
    from agent.worker.processor import _handle_failure
    from tests.unit.test_flow_client_batch import envelope

    monkeypatch.setattr("agent.services.flow_client.USE_BATCH_RPC", True)
    client = FlowClient()
    calls = []
    projects = {"A": "11111111-1111-4111-8111-111111111111",
                "B": "22222222-2222-4222-8222-222222222222"}
    media = {"A": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
             "B": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}

    class Socket:
        def __init__(self, profile):
            self.profile = profile

        async def send(self, raw):
            message = json.loads(raw)
            rpc = message["params"]["rpcid"]
            calls.append((self.profile, rpc, message["params"]["freq"]))
            result = {"id": message["id"]}
            if rpc == fb.RPC_UPLOAD_IMAGE:
                result["data"] = envelope(rpc, [[media[self.profile], projects[self.profile], "operation", "CAE"]])
            elif self.profile == "A":
                result["error"] = "PUBLIC_ERROR_USER_QUOTA_REACHED"
            else:
                result["data"] = envelope(rpc, [[f"https://{fb.MEDIA_HOST}/image/{media['B']}?sig=test"]])
            await client.handle_message(result, self)

    for name in ("A", "B"):
        socket = Socket(name)
        client.set_extension(socket)
        client._extensions[socket].update(installation_id=name, flow_project_id=projects[name],
                                          connected_at=2 if name == "A" else 1)
    payload = {"prompt": "test", "input_images": [{"image_base64": "c291cmNl"}]}
    await database.execute("INSERT INTO request(id,type,payload_json) VALUES ('quota-test','GENERATE_IMAGE',?)", (json.dumps(payload),))
    await database.commit()
    token = audit.current_job.set("quota-test")
    try:
        req = await crud.get_request("quota-test")
        failed = await _dispatch_client_v1(req, "HORIZONTAL", SimpleNamespace(_client=client))
        assert "QUOTA" in failed["error"]
        await _handle_failure("quota-test", req, failed, {})
        retry = await crud.get_request("quota-test")
        assert retry["status"] == "PENDING" and retry["retry_count"] == 1
        succeeded = await _dispatch_client_v1(retry, "HORIZONTAL", SimpleNamespace(_client=client))
    finally:
        audit.current_job.reset(token)
    assert succeeded["status"] == 200 and succeeded["_installation_id"] == "B"
    assert [(profile, rpc) for profile, rpc, _ in calls] == [
        ("A", fb.RPC_UPLOAD_IMAGE), ("A", fb.RPC_GEN_IMAGE),
        ("B", fb.RPC_UPLOAD_IMAGE), ("B", fb.RPC_GEN_IMAGE)]
    assert media["B"] in calls[-1][2] and media["A"] not in calls[-1][2]
    assert projects["B"] in calls[-1][2] and projects["A"] not in calls[-1][2]
    logs = await audit.list_calls("quota-test")
    assert [row["installation_id"] for row in logs] == ["A", "A", "B", "B"]
    assert logs[1]["error_code"] == "PUBLIC_ERROR_USER_QUOTA_REACHED"
