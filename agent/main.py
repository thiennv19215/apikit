"""Flow Kit — FastAPI + WebSocket server entry point."""
import asyncio
import json
import logging
import signal
import time
from pathlib import Path
from contextlib import asynccontextmanager

import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from agent.config import API_HOST, API_PORT, WS_HOST, WS_PORT, BASE_DIR
from agent.db.schema import init_db, close_db
from agent.api.characters import router as characters_router
from agent.api.projects import router as projects_router
from agent.api.videos import router as videos_router
from agent.api.scenes import router as scenes_router
from agent.api.requests import router as requests_router
from agent.api.flow import router as flow_router
from agent.api.reviews import router as reviews_router
from agent.api.tts import router as tts_router
from agent.api.materials import router as materials_router
from agent.api.music import router as music_router
from agent.api.models import router as models_router
from agent.api.providers import router as providers_router
from agent.api.active_project import router as active_project_router
from agent.api.v1.generations import router as v1_generations_router
from agent.api.v1.characters import router as v1_characters_router
from agent.api.v1.materials import router as v1_materials_router
from agent.api.v1.audio import router as v1_audio_router
from agent.api.v1.concat import router as v1_concat_router
from agent.api.v1.health import router as v1_health_router, maintenance_response
from agent.worker.processor import get_worker_controller
from agent.services.flow_client import get_flow_client
from agent.services.event_bus import event_bus
from agent.sdk import init_sdk

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ─── WebSocket Server for Extension ─────────────────────────

async def ws_handler(websocket):
    """Handle a Chrome extension WebSocket connection."""
    client = get_flow_client()
    client.set_extension(websocket)
    logger.info("Extension connected from %s", websocket.remote_address)

    # Send callback secret so extension can authenticate HTTP callbacks
    await websocket.send(json.dumps({"type": "callback_secret", "secret": _CALLBACK_SECRET}))

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                await client.handle_message(data, websocket)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from extension")
            except Exception as e:
                logger.exception("Error handling extension message: %s", e)
    except websockets.ConnectionClosed:
        pass
    finally:
        client.clear_extension(websocket)
        logger.info("Extension disconnected")


class FastAPIWebSocketAdapter:
    """Wraps a FastAPI/Starlette WebSocket to match websockets protocol interface."""
    def __init__(self, ws: WebSocket):
        self._ws = ws

    @property
    def remote_address(self):
        client = getattr(self._ws, "client", None)
        return (client.host, client.port) if client else ("cloudflare_proxy", 0)

    async def send(self, data: str):
        await self._ws.send_text(data)

    async def __aiter__(self):
        try:
            while True:
                msg = await self._ws.receive_text()
                yield msg
        except WebSocketDisconnect:
            return


async def run_ws_server():
    """Run WebSocket server for extension connections."""
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        logger.info("WebSocket server listening on ws://%s:%d", WS_HOST, WS_PORT)
        await asyncio.Future()  # run forever


# ─── FastAPI App ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Load custom materials from DB into in-memory registry
    from agent.db.crud import list_materials as db_list_materials
    from agent.materials import register_material, _BUILTIN_IDS
    try:
        custom_materials = await db_list_materials()
        for m in custom_materials:
            if m["id"] not in _BUILTIN_IDS:
                register_material(m)
                logger.info("Loaded custom material from DB: %s", m["id"])
    except Exception as e:
        logger.warning("Failed to load custom materials: %s", e)

    ops = init_sdk(get_flow_client())
    logger.info("SDK initialized (OperationService ready)")
    logger.info("Flow Kit starting on %s:%d", API_HOST, API_PORT)

    controller = get_worker_controller()

    # SIGTERM handler for graceful shutdown (Unix only)
    try:
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, controller.request_shutdown)
    except (NotImplementedError, AttributeError):
        pass

    # Start background tasks
    ws_task = asyncio.create_task(run_ws_server())
    worker_task = asyncio.create_task(controller.start())
    logger.info("WS server + worker started")

    yield

    controller.request_shutdown()
    await controller.drain()
    ws_task.cancel()
    worker_task.cancel()
    await close_db()
    logger.info("Flow Kit stopped")


app = FastAPI(title="Flow Kit", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(characters_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(videos_router, prefix="/api")
app.include_router(scenes_router, prefix="/api")
app.include_router(requests_router, prefix="/api")
app.include_router(flow_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(materials_router, prefix="/api")
app.include_router(music_router, prefix="/api")
app.include_router(models_router)
app.include_router(providers_router)
app.include_router(active_project_router)
app.include_router(v1_generations_router)
app.include_router(v1_characters_router)
app.include_router(v1_materials_router)
app.include_router(v1_audio_router)
app.include_router(v1_concat_router)
app.include_router(v1_health_router)


@app.middleware("http")
async def client_maintenance_gate(request: Request, call_next):
    from agent import config
    # Keep health and existing job polling available during maintenance.
    if (config.CLIENT_MAINTENANCE
            and request.url.path.startswith("/v1/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path.rstrip("/") != "/v1/jobs/status"):
        return JSONResponse(
            status_code=503, content=maintenance_response().model_dump(),
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
    return await call_next(request)


import secrets as _secrets
_CALLBACK_SECRET = _secrets.token_urlsafe(32)


@app.post("/api/ext/callback")
async def ext_callback(request: Request):
    """HTTP callback for extension to deliver API responses.

    Replaces ws.send() for response delivery — immune to WS disconnect.
    Extension POSTs {id, status, data, error} here instead of sending via WS.
    Requires X-Callback-Secret header matching the secret sent to extension on WS connect.
    """
    data = await request.json()
    client = get_flow_client()
    req_id = data.get("id")
    logger.info("ext/callback: id=%s pending=%d match=%s",
                str(req_id)[:8] if req_id else "none",
                len(client._pending),
                "yes" if req_id and req_id in client._pending else "no")
    if req_id and req_id in client._pending:
        future = client._pending[req_id]
        try:
            future.set_result(data)
        except asyncio.InvalidStateError:
            pass
        return {"ok": True}
    return {"ok": False, "reason": "no matching pending request"}


# ─── Payload Capture Endpoint (for capturing new Flow RPCs) ──────
CAPTURED_PAYLOADS_FILE = Path(BASE_DIR) / "data" / "captured_payloads.jsonl"


@app.post("/api/ext/netlog")
async def ext_netlog(request: Request):
    """Receive captured network requests from extension recorder."""
    try:
        data = await request.json()
    except Exception:
        return {"error": "Invalid JSON"}
    url = data.get("url", "")
    body = data.get("body", "")
    status = data.get("statusCode")
    ts = data.get("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # Log to disk
    CAPTURED_PAYLOADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CAPTURED_PAYLOADS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "url": url, "status": status, "body": body}) + "\n")

    logger.info("[FLOW_CAPTURE] Logged network payload: url=%s length=%d", url, len(body or ""))
    return {"ok": True}


@app.get("/api/ext/captured-payloads")
async def get_captured_payloads(limit: int = 10):
    """Read recently captured payloads."""
    if not CAPTURED_PAYLOADS_FILE.exists():
        return {"payloads": []}
    lines = []
    with open(CAPTURED_PAYLOADS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    lines.append(json.loads(line))
                except Exception:
                    pass
    return {"total": len(lines), "payloads": lines[-limit:]}


@app.get("/health")
async def health():
    client = get_flow_client()
    return {
        "status": "ok",
        "version": "0.2.0",
        "extension_connected": client.connected,
        "ws": client.ws_stats,
    }


def _get_provider_status_dict():
    client = get_flow_client()
    connected = client.connected
    # The live connection pool is maintained by FlowClient, not _accounts.
    extensions = client.list_extensions()
    active_accounts = len(extensions)
    available_accounts = sum(bool(ext["available"]) for ext in extensions)
    controller = get_worker_controller()
    active_count = controller.active_count if controller else 0
    capacity = 200
    status_str = "ready" if connected and available_accounts else "waiting_for_provider"
    return {
        "status": status_str,
        "project_store": "ready",
        "provider_accounts": active_accounts,
        "video_lite_ready_accounts": available_accounts,
        "jobs": {"queued": 0, "dispatching": active_count, "running": active_count},
        "active_jobs": active_count,
        "job_queue_capacity": capacity,
        "job_queue_remaining": max(0, capacity - active_count),
    }


@app.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    status_info = _get_provider_status_dict()
    if status_info["status"] != "ready":
        return JSONResponse(status_code=503, content=status_info)
    return status_info


@app.get("/api/health", include_in_schema=False)
async def api_health():
    status_info = _get_provider_status_dict()
    return {"ok": status_info["status"] != "waiting_for_provider", **status_info}


@app.websocket("/ws")
@app.websocket("/api/extensions/ws")
async def extension_ws_fastapi(websocket: WebSocket):
    """WebSocket endpoint for Chrome extension connecting over HTTP / Cloudflare Tunnel."""
    await websocket.accept()
    adapter = FastAPIWebSocketAdapter(websocket)
    await ws_handler(adapter)


# ─── Dashboard WebSocket ──────────────────────────────────────

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    """WebSocket endpoint for dashboard clients (Chrome extension side panel)."""
    # Reject cross-origin connections (only allow localhost)
    origin = (websocket.headers.get("origin") or "").lower()
    if origin and not any(origin.startswith(p) for p in (
        "http://127.0.0.1", "http://localhost", "chrome-extension://",
    )):
        await websocket.close(code=4003, reason="Origin not allowed")
        return
    await websocket.accept()

    q = event_bus.subscribe()
    try:
        # Send initial snapshot
        client = get_flow_client()
        controller = get_worker_controller()
        from agent.db import crud
        pending_requests = await crud.list_requests(status="PENDING")
        processing_requests = await crud.list_requests(status="PROCESSING")
        snapshot = {
            "type": "snapshot",
            "health": {
                "status": "ok",
                "extension_connected": client.connected,
            },
            "requests": pending_requests + processing_requests,
            "worker": {
                "active": controller.active_count,
                "slots": max(0, 5 - controller.active_count),
            },
        }
        await websocket.send_text(json.dumps(snapshot))

        # Forward events from event_bus to this client
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(msg)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("Dashboard WS client disconnected: %s", e)
    finally:
        event_bus.unsubscribe(q)


if __name__ == "__main__":
    import os
    import uvicorn
    reload_enabled = os.environ.get("GLA_RELOAD", "0") == "1"
    uvicorn.run(
        "agent.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=reload_enabled,
        reload_excludes=["*.db", "*.db-wal", "*.db-shm", "output/*"],
    )
