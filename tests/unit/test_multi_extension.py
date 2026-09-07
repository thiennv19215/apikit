"""Unit tests for FlowKit multi-extension connection pool, load balancing, and concurrency."""
import asyncio
import json
import time
import pytest

from agent.services.flow_client import FlowClient
from agent.worker.processor import APIRateLimiter
from agent.db.schema import close_db


class FakeWebSocket:
    """Mock WebSocket for testing multi-extension dispatch."""

    def __init__(self, name="ws"):
        self.name = name
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(json.loads(payload))


@pytest.fixture
async def multi_client(monkeypatch):
    client = FlowClient()
    # Mock _sync_tier to avoid touching sqlite DB during unit tests
    async def noop_sync(*_args, **_kwargs):
        pass
    monkeypatch.setattr(client, "_sync_tier", noop_sync)
    yield client
    await close_db()


class TestMultiExtensionPool:
    @pytest.mark.asyncio
    async def test_multiple_extensions_connect_and_register(self, multi_client):
        ws_a = FakeWebSocket("ws_a")
        ws_b = FakeWebSocket("ws_b")

        multi_client.set_extension(ws_a)
        multi_client.set_extension(ws_b)

        assert multi_client.connected
        assert len(multi_client._extensions) == 2

        # Send handshake from profile A
        await multi_client.handle_message({
            "type": "extension_ready",
            "installationId": "install_profile_a",
            "profileName": "alice@gmail.com",
            "protocolVersion": 2,
        }, ws_a)

        # Send handshake from profile B
        await multi_client.handle_message({
            "type": "extension_ready",
            "installationId": "install_profile_b",
            "profileName": "bob@gmail.com",
            "protocolVersion": 2,
        }, ws_b)

        exts = multi_client.list_extensions()
        assert len(exts) == 2
        inst_ids = {e["installation_id"] for e in exts}
        assert inst_ids == {"install_profile_a", "install_profile_b"}

        stats = multi_client.ws_stats
        assert stats["active_connections"] == 2
        assert stats["authenticated_connections"] == 2

        # Lookup by installation
        assert multi_client.get_extension_by_installation("install_profile_a") is ws_a
        assert multi_client.get_extension_by_installation("install_profile_b") is ws_b
        assert multi_client.get_extension_by_installation("unknown") is None

    @pytest.mark.asyncio
    async def test_load_balancing_least_busy_routing(self, multi_client):
        ws_a = FakeWebSocket("ws_a")
        ws_b = FakeWebSocket("ws_b")

        multi_client.set_extension(ws_a)
        multi_client.set_extension(ws_b)

        await multi_client.handle_message({
            "type": "extension_ready",
            "installationId": "install_a",
            "profileName": "alice@gmail.com",
        }, ws_a)
        await multi_client.handle_message({
            "type": "extension_ready",
            "installationId": "install_b",
            "profileName": "bob@gmail.com",
        }, ws_b)

        candidates = multi_client._extension_candidates(require_token=False)
        assert len(candidates) == 2

        # Simulate that ws_a is currently processing 2 requests
        multi_client._extensions[ws_a]["in_flight"] = 2
        multi_client._extensions[ws_b]["in_flight"] = 0

        # Least busy routing must pick ws_b first!
        candidates = multi_client._extension_candidates(require_token=False)
        assert candidates[0] is ws_b

        # Affinity routing: if preferred_installation is explicitly requested,
        # it should take precedence
        candidates_pref = multi_client._extension_candidates(
            require_token=False, preferred_installation="install_a"
        )
        assert candidates_pref[0] is ws_a

    @pytest.mark.asyncio
    async def test_concurrent_send_dispatches_to_available_extension(self, multi_client):
        ws_a = FakeWebSocket("ws_a")
        multi_client.set_extension(ws_a)
        await multi_client.handle_message({
            "type": "extension_ready",
            "installationId": "install_a",
        }, ws_a)

        async def respond_soon():
            await asyncio.sleep(0.01)
            assert len(ws_a.sent) == 1
            msg = ws_a.sent[0]
            await multi_client.handle_message({
                "id": msg["id"],
                "data": "ok_response",
            }, ws_a)

        asyncio.create_task(respond_soon())
        res = await multi_client._send("test_method", {"param": 1}, timeout=2)
        assert res.get("data") == "ok_response"
        assert res.get("_installation_id") == "install_a"
        assert multi_client._extensions[ws_a]["in_flight"] == 0

    @pytest.mark.asyncio
    async def test_failover_when_one_extension_fails_with_quota(self, multi_client):
        ws_a = FakeWebSocket("ws_a")
        ws_b = FakeWebSocket("ws_b")
        multi_client.set_extension(ws_a)
        multi_client.set_extension(ws_b)

        await multi_client.handle_message({"type": "extension_ready", "installationId": "a"}, ws_a)
        await multi_client.handle_message({"type": "extension_ready", "installationId": "b"}, ws_b)

        async def mock_handler():
            while not ws_a.sent:
                await asyncio.sleep(0.005)
            # Fail ws_a with quota error
            msg_a = ws_a.sent[0]
            await multi_client.handle_message({
                "id": msg_a["id"],
                "error": "public_error_user_quota_reached",
            }, ws_a)

            # Wait for failover to send to ws_b
            while not ws_b.sent:
                await asyncio.sleep(0.005)
            msg_b = ws_b.sent[0]
            await multi_client.handle_message({
                "id": msg_b["id"],
                "data": "succeeded_on_failover",
            }, ws_b)

        asyncio.create_task(mock_handler())
        res = await multi_client._send("gen_img", {}, timeout=2)
        assert res.get("data") == "succeeded_on_failover"
        assert res.get("_installation_id") == "b"


class TestConcurrentRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limiter_does_not_block_different_installations(self):
        limiter = APIRateLimiter(max_concurrent=5, cooldown_seconds=2.0)

        t0 = time.monotonic()
        # Acquire for installation A
        await limiter.acquire("inst_a")
        limiter.release()

        # Immediate acquire for installation B should NOT wait for installation A's cooldown!
        await limiter.acquire("inst_b")
        limiter.release()
        elapsed = time.monotonic() - t0

        # Should complete almost instantly (< 0.2s), definitely not waiting 2 full seconds!
        assert elapsed < 0.5
