_retry_state: dict = {}
"""Background worker — processes pending requests via Chrome extension.

Thin dispatcher: picks up PENDING requests, delegates to OperationService
for actual API work, handles status transitions + retry + scene updates.
"""
import asyncio
import base64
import json
import logging
import time

import aiohttp

from agent.db import crud
from agent.services.flow_client import get_flow_client
from datetime import datetime, timezone

from agent.services.event_bus import event_bus
from agent.config import (
    POLL_INTERVAL,
    MAX_RETRIES,
    API_COOLDOWN,
    MAX_CONCURRENT_REQUESTS,
    CLIENT_V1_QUEUE_TIMEOUT,
    USE_BATCH_RPC,
)
from agent.worker._parsing import _is_error
from agent.sdk.services.result_handler import parse_result, apply_scene_result, apply_character_result

logger = logging.getLogger(__name__)

_API_CALL_TYPES = {"GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE",
                   "GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO",
                   "GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE",
                   "EDIT_CHARACTER_IMAGE"}

_TYPE_PRIORITY = {
    "GENERATE_CHARACTER_IMAGE": 0, "REGENERATE_CHARACTER_IMAGE": 0, "EDIT_CHARACTER_IMAGE": 0,
    "GENERATE_IMAGE": 1, "REGENERATE_IMAGE": 1, "EDIT_IMAGE": 1,
    "GENERATE_VIDEO": 2, "REGENERATE_VIDEO": 2, "GENERATE_VIDEO_REFS": 2,
    "UPSCALE_VIDEO": 3,
}


class APIRateLimiter:
    """Enforces max concurrent requests AND minimum gap between API calls per account/extension."""
    def __init__(self, max_concurrent: int, cooldown_seconds: float):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cooldown = cooldown_seconds
        self._last_call = 0.0
        self._per_inst_last_call: dict[str, float] = {}
        self._gate = asyncio.Lock()

    async def acquire(self, installation_id: str | None = None):
        await self._semaphore.acquire()
        async with self._gate:
            now = time.monotonic()
            key = installation_id or "__global__"
            last = self._per_inst_last_call.get(key, 0.0)
            elapsed = now - last
            # If multi-extension routing is active, cooldown applies per-extension
            # so multiple accounts can dispatch without blocking each other.
            target_gap = min(self._cooldown, 2.0) if installation_id else min(self._cooldown, 1.0)
            if elapsed < target_gap:
                await asyncio.sleep(target_gap - elapsed)
            self._per_inst_last_call[key] = time.monotonic()
            self._last_call = time.monotonic()

    def release(self):
        self._semaphore.release()


class WorkerController:
    """Controls the background worker loop with rate limiting and graceful shutdown."""

    def __init__(self):
        self._shutdown = asyncio.Event()
        # New work and released slots wake the scheduler immediately. The
        # polling interval remains a fallback for external state changes.
        self._work_available = asyncio.Event()
        self._active_ids: set[str] = set()
        self._monitor_tasks: set[asyncio.Task] = set()
        self._rate_limiter = APIRateLimiter(MAX_CONCURRENT_REQUESTS, API_COOLDOWN)
        self._deferred: dict[str, float] = {}  # rid -> defer_until timestamp
        self._retry_after: dict[str, float] = {}  # rid -> retry_after timestamp

    @property
    def active_count(self) -> int:
        """Number of currently active requests."""
        return len(self._active_ids)

    async def start(self):
        """Start the worker loop."""
        await self._cleanup_stale_processing()
        await self._run_loop()

    def request_shutdown(self):
        """Signal the worker to stop after current tasks drain."""
        self._shutdown.set()
        self._work_available.set()

    def notify_work_available(self):
        """Wake the scheduler after a request is queued or a slot is freed."""
        self._work_available.set()

    def start_monitor(self, coro) -> None:
        """Run provider polling outside the submission-worker capacity."""
        task = asyncio.create_task(coro)
        self._monitor_tasks.add(task)
        task.add_done_callback(self._monitor_tasks.discard)

    async def _wait_for_work(self):
        """Wait for work without delaying an available worker slot."""
        try:
            await asyncio.wait_for(self._work_available.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass

    async def drain(self, timeout: float = 30.0):
        """Wait until all active tasks complete, with timeout."""
        deadline = time.monotonic() + timeout
        while (self._active_ids or self._monitor_tasks) and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        if self._active_ids or self._monitor_tasks:
            logger.warning("Drain timeout: %d submissions, %d monitors still active after %.0fs",
                           len(self._active_ids), len(self._monitor_tasks), timeout)
            for task in list(self._monitor_tasks):
                task.cancel()
            if self._monitor_tasks:
                await asyncio.gather(*self._monitor_tasks, return_exceptions=True)

    async def _cleanup_stale_processing(self):
        """Reset any requests stuck in PROCESSING state from a previous run."""
        try:
            stale = await crud.list_requests(status="PROCESSING")
            for req in stale:
                await crud.update_request(req["id"], status="PENDING",
                                          error_message="reset: stale PROCESSING on startup")
                logger.warning("Stale request reset: %s type=%s", req["id"][:8], req.get("type"))
            if stale:
                logger.info("Cleaned up %d stale PROCESSING requests", len(stale))
        except Exception as e:
            logger.warning("Could not clean up stale requests: %s", e)

    async def _expire_unserviceable_pending_requests(self):
        """Fail requests stuck in PENDING for too long when no extension is connected."""
        try:
            pending = await crud.list_requests(status="PENDING")
            now = datetime.now(timezone.utc)
            for req in pending:
                c_at = req.get("created_at")
                if not c_at:
                    continue
                try:
                    t_c = datetime.fromisoformat(c_at.replace("Z", "+00:00"))
                    age = (now - t_c).total_seconds()
                except Exception:
                    continue
                if age > CLIENT_V1_QUEUE_TIMEOUT:
                    rid = req["id"]
                    msg = f"EXTENSION_UNAVAILABLE_TIMEOUT: Request expired after waiting {int(age)}s with no active Chrome extension connected."
                    await crud.update_request(rid, status="FAILED", error_message=msg)
                    await _mark_scene_failed(req)
                    logger.error("Request %s timed out waiting for extension (%ds): failed permanently", rid[:8], int(age))
                    await event_bus.emit("request_update", {"id": rid, "status": "FAILED", "error": msg})
        except Exception as e:
            logger.warning("Error checking unserviceable pending requests: %s", e)

    async def _run_loop(self):
        client = get_flow_client()

        while not self._shutdown.is_set():
            try:
                # Clear before inspecting the queue: a notification received
                # during this scheduling pass stays set for the next pass.
                self._work_available.clear()
                if not client.connected:
                    await self._expire_unserviceable_pending_requests()
                    await self._wait_for_work()
                    continue

                if USE_BATCH_RPC and client._extensions and all(sess.get("has_flow_tab") is False for sess in client._extensions.values()):
                    await self._wait_for_work()
                    continue

                now = time.time()
                num_extensions = max(1, len(client._extensions))
                effective_concurrency = MAX_CONCURRENT_REQUESTS * num_extensions
                slots_available = effective_concurrency - len(self._active_ids)
                if slots_available <= 0:
                    await self._wait_for_work()
                    continue

                pending = await crud.list_actionable_requests(
                    exclude_ids=self._active_ids, limit=slots_available
                )

                pending_count = len(pending)
                await event_bus.emit("worker_tick", {
                    "active": len(self._active_ids),
                    "slots": slots_available,
                    "pending": pending_count,
                })

                if pending:
                    logger.info("Worker: %d actionable, %d active, %d slots",
                                len(pending), len(self._active_ids), slots_available)

                for req in pending:
                    if slots_available <= 0:
                        break
                    rid = req["id"]

                    # Skip in-flight
                    if rid in self._active_ids:
                        continue

                    # Skip recently deferred (prereq or retry cooldown)
                    if rid in self._deferred and self._deferred[rid] > now:
                        continue
                    self._deferred.pop(rid, None)

                    # Skip if retry backoff not elapsed
                    if rid in self._retry_after and self._retry_after[rid] > now:
                        continue

                    self._active_ids.add(rid)
                    slots_available -= 1
                    asyncio.create_task(self._run_one(req))

                # Prune stale deferred/retry entries for requests no longer pending
                pending_ids = {r["id"] for r in pending}
                self._deferred = {k: v for k, v in self._deferred.items() if k in pending_ids}
                self._retry_after = {k: v for k, v in self._retry_after.items() if k in pending_ids}

            except Exception as e:
                logger.exception("Worker loop error: %s", e)

            await self._wait_for_work()

    async def _run_one(self, req: dict):
        rid = req["id"]
        inst_id = req.get("installation_id")
        try:
            await self._rate_limiter.acquire(inst_id)
            try:
                await _process_one(req, self._deferred, self._retry_after)
            finally:
                self._rate_limiter.release()
        finally:
            self._active_ids.discard(rid)
            self.notify_work_available()


async def _prerequisites_met(req: dict, orientation: str) -> bool:
    """Check if prerequisites are ready. Returns False to defer (stay PENDING)."""
    req_type = req.get("type", "")
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"

    # Video gen needs scene image to be ready; upscale needs video to be ready
    if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
        scene = await crud.get_scene(req.get("scene_id"))
        if not scene:
            return True  # let _dispatch handle "scene not found"
        if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
            if not scene.get(f"{prefix}_image_media_id"):
                logger.info("VIDEO prereq deferred: scene=%s no %s_image_media_id", req.get("scene_id","")[:12], prefix)
                return False
        elif req_type == "UPSCALE_VIDEO":
            if not scene.get(f"{prefix}_video_media_id"):
                logger.info("UPSCALE prereq deferred: scene=%s no %s_video_media_id", req.get("scene_id","")[:12], prefix)
                return False

    # Edit requests need source media (own image or parent's for INSERT scenes)
    if req_type in ("EDIT_IMAGE", "EDIT_CHARACTER_IMAGE"):
        if not req.get("source_media_id"):
            if req_type == "EDIT_CHARACTER_IMAGE":
                char = await crud.get_character(req.get("character_id"))
                if not char or not char.get("media_id"):
                    return False
            elif req_type == "EDIT_IMAGE":
                scene = await crud.get_scene(req.get("scene_id"))
                if not scene:
                    return True  # let _dispatch handle
                # CONTINUATION scenes always use parent's image as source
                src = None
                if scene.get("parent_scene_id"):
                    parent = await crud.get_scene(scene["parent_scene_id"])
                    src = parent.get(f"{prefix}_image_media_id") if parent else None
                if not src:
                    src = scene.get(f"{prefix}_image_media_id")
                logger.info("EDIT_IMAGE prereq: scene=%s src=%s parent=%s", req.get("scene_id","")[:12], src, scene.get("parent_scene_id","")[:12] if scene.get("parent_scene_id") else "none")
                if not src:
                    return False

    return True


async def _resolve_orientation(req: dict) -> str:
    """Resolve orientation from request, falling back to video table, then VERTICAL."""
    orient = req.get("orientation")
    if orient:
        return orient
    vid = req.get("video_id")
    if vid:
        video = await crud.get_video(vid)
        if video and video.get("orientation"):
            return video["orientation"]
    return "VERTICAL"


async def _process_one(req: dict, deferred: dict = None, retry_after: dict = None):
    rid, req_type = req["id"], req["type"]
    orientation = await _resolve_orientation(req)

    if await _is_already_completed(req, orientation):
        logger.info("Request %s skipped — already COMPLETED", rid[:8])
        # Copy existing result data from scene/character onto the request record
        skip_kwargs = {"status": "COMPLETED", "error_message": "skipped: already completed"}
        prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
            char = await crud.get_character(req.get("character_id"))
            if char:
                skip_kwargs["media_id"] = char.get("media_id")
                skip_kwargs["output_url"] = char.get("image_url")
        else:
            scene = await crud.get_scene(req.get("scene_id"))
            if scene:
                if req_type == "GENERATE_IMAGE":
                    skip_kwargs["media_id"] = scene.get(f"{prefix}_image_media_id")
                    skip_kwargs["output_url"] = scene.get(f"{prefix}_image_url")
                elif req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
                    skip_kwargs["media_id"] = scene.get(f"{prefix}_video_media_id")
                    skip_kwargs["output_url"] = scene.get(f"{prefix}_video_url")
                elif req_type == "UPSCALE_VIDEO":
                    skip_kwargs["media_id"] = scene.get(f"{prefix}_upscale_media_id")
                    skip_kwargs["output_url"] = scene.get(f"{prefix}_upscale_url")
        await crud.update_request(rid, **skip_kwargs)
        return

    # Check prerequisites before dispatching — don't burn retries on missing deps
    if not await _prerequisites_met(req, orientation):
        if deferred is not None:
            deferred[rid] = time.time() + 30  # defer 30s before rechecking
        return

    logger.info("Processing request %s type=%s", rid[:8], req_type)
    await crud.update_request(rid, status="PROCESSING")
    await event_bus.emit("request_update", {"id": rid, "status": "PROCESSING", "type": req_type})

    try:
        result = await _dispatch(req, orientation)
        if _is_error(result):
            await _handle_failure(rid, req, result, retry_after)
        elif result.get("_async_operation"):
            # Flow accepted the job. Release this worker slot now; a separate
            # monitor owns completion while clients can already see operation_id.
            get_worker_controller().start_monitor(
                _monitor_operation(req, orientation, result["operations"], result.get("_poll_client"),
                                   result.get("_poll_timeout"))
            )
        else:
            await _complete_request(req, orientation, result)
    except Exception as e:
        logger.exception("Request %s exception: %s", rid[:8], e)
        await event_bus.emit("request_update", {"id": rid, "status": "FAILED", "error": str(e)})
        await _handle_failure(rid, req, {"error": str(e)}, retry_after)


async def _complete_request(req: dict, orientation: str, result: dict) -> None:
    """Persist a successful generation, shared by submit workers and pollers."""
    rid, req_type = req["id"], req["type"]
    gen_result = parse_result(result, req_type)
    update_kw = {"status": "COMPLETED", "media_id": gen_result.media_id, "output_url": gen_result.url}
    if result.get("_installation_id"):
        update_kw["installation_id"] = result["_installation_id"]
    await crud.update_request(rid, **update_kw)
    if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
        if req.get("character_id"):
            await apply_character_result(req["character_id"], gen_result)
    else:
        await apply_scene_result(req.get("scene_id"), req_type, orientation, gen_result)
    await event_bus.emit("request_update", {"id": rid, "status": "COMPLETED"})
    logger.info("Request %s COMPLETED: media=%s", rid[:8], gen_result.media_id[:20] if gen_result.media_id else "?")


async def _monitor_operation(req: dict, orientation: str, operations: list[dict], client,
                             timeout: int | None = None) -> None:
    """Poll a Flow-accepted operation without consuming a submission slot."""
    from agent.sdk.services.operations import _poll_operations
    try:
        kwargs = {"timeout": timeout} if timeout else {}
        result = await _poll_operations(client or get_flow_client(), operations, **kwargs)
        if _is_error(result):
            await _handle_failure(req["id"], req, result)
        else:
            await _complete_request(req, orientation, result)
    except Exception as e:
        logger.exception("Operation monitor failed for %s: %s", req["id"][:8], e)
        await _handle_failure(req["id"], req, {"error": str(e)})
    finally:
        get_worker_controller().notify_work_available()


async def _dispatch(req: dict, orientation: str) -> dict:
    """Route request to the appropriate OperationService method."""
    from agent.sdk.services.operations import get_operations
    ops = get_operations()
    req_type, rid = req["type"], req["id"]
    pid = req.get("project_id", "0")

    # Client v1 standalone request (direct Base64 / decoupled from scenes)
    if req.get("payload_json"):
        return await _dispatch_client_v1(req, orientation, ops)

    # Scene-based operations
    if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE",
                    "GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
        scene = await crud.get_scene(req.get("scene_id"))
        if not scene:
            return {"error": "Scene not found"}
        scene["_project_id"] = pid

        if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE"):
            return await ops.generate_scene_image(scene, orientation)
        if req_type == "EDIT_IMAGE":
            return await ops.edit_scene_image(scene, orientation, source_media_id=req.get("source_media_id"))
        if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO"):
            return await ops.generate_scene_video(scene, orientation, request_id=rid, poll=False)
        if req_type == "GENERATE_VIDEO_REFS":
            return await ops.generate_scene_video_refs(scene, orientation, request_id=rid, poll=False)
        if req_type == "UPSCALE_VIDEO":
            return await ops.upscale_scene_video(scene, orientation, request_id=rid, poll=False)

    # Character operations
    if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
        char = await crud.get_character(req.get("character_id"))
        if not char:
            return {"error": "Character not found"}
        if req_type == "REGENERATE_CHARACTER_IMAGE":
            # Clear existing media so generate_reference_image takes the normal (not fast) path
            await crud.update_character(char["id"], media_id=None, reference_image_url=None)
            char["media_id"] = None
            char["reference_image_url"] = None
            return await ops.generate_reference_image(char, pid)
        if req_type == "EDIT_CHARACTER_IMAGE":
            src = req.get("source_media_id") or char.get("media_id")
            if not src:
                return {"error": "No source image to edit — generate a reference image first"}
            edit_prompt = char.get("image_prompt") or char.get("description", "")
            project = await crud.get_project(pid) if pid != "0" else None
            tier = project.get("user_paygate_tier", "PAYGATE_TIER_ONE") if project else "PAYGATE_TIER_ONE"
            aspect = "IMAGE_ASPECT_RATIO_LANDSCAPE" if char.get("entity_type") in ("location",) else "IMAGE_ASPECT_RATIO_PORTRAIT"
            return await ops._client.edit_image(
                prompt=edit_prompt, source_media_id=src,
                project_id=pid, aspect_ratio=aspect,
                user_paygate_tier=tier,
            )
        return await ops.generate_reference_image(char, pid)

    return {"error": f"Unknown request type: {req_type}"}


async def _dispatch_client_v1(req: dict, orientation: str, ops) -> dict:
    """Execute stateless/decoupled generation request from v1 Client API."""
    from agent.sdk.services.operations import _extract_operations, _poll_operations

    req_type = req["type"]
    rid = req["id"]
    pid = req.get("project_id") or ""
    # V1 jobs are decoupled from a scene, so there is no earlier dispatch path
    # that necessarily touched the Flow client.  Resolve it before examining
    # the requested installation: otherwise a request carrying an
    # installation_id raises UnboundLocalError before it can upload Base64
    # inputs or submit the workflow.
    client = getattr(ops, "_client", None)
    if client is None:
        return {"error": "PROFILE_UNAVAILABLE: Flow client is not configured"}

    try:
        payload = json.loads(req.get("payload_json") or "{}")
    except Exception as e:
        return {"error": f"Invalid payload_json: {e}"}

    if payload.get("project_id"):
        pid = payload["project_id"]
    # request.installation_id is the last actual executor, not a routing lock.
    inst_id = payload.get("installation_id")
    if inst_id and hasattr(client, "is_installation_exhausted") and client.is_installation_exhausted(inst_id):
        inst_id = None
        pid = None

    # UUID-only inputs must remain on their known owner. Base64 inputs can be
    # uploaded again on another profile; keep the source bytes across retries.
    from agent.services.execution_audit import media_owner
    raw_ids = list(payload.get("reference_media_ids") or []) + list(payload.get("character_media_ids") or [])
    raw_ids += [payload[key] for key in ("start_media_id", "end_media_id") if payload.get(key)]
    raw_ids += [img["media_id"] for img in payload.get("input_images", [])
                if img.get("media_id") and not img.get("image_base64")]
    for media_id in dict.fromkeys(raw_ids):
        owner = await media_owner(media_id)
        if not owner:
            return {"error": "MEDIA_OWNER_UNKNOWN: upload the original image again before using this UUID"}
        if (inst_id and inst_id != owner["installation_id"]) or (pid and pid != owner["project_id"]):
            return {"error": "MEDIA_ACCOUNT_MISMATCH: inputs must share an owner/project; re-upload the original images"}
        inst_id, pid = owner["installation_id"], owner["project_id"]

    if hasattr(client, "_select_extension"):
        from agent.config import USE_BATCH_RPC
        selected = client._select_extension(not USE_BATCH_RPC, preferred_installation=inst_id, preferred_project_id=pid or None)
        if selected is None:
            return {"error": "PROFILE_UNAVAILABLE: no eligible profile"}
        selected_profile = client._extensions.get(selected, {})
        selected_inst_id = selected_profile.get("installation_id")
        if inst_id and selected_inst_id != inst_id:
            return {"error": "PROFILE_UNAVAILABLE: requested installation is not an eligible authenticated profile"}
        if pid and not inst_id and selected_profile.get("flow_project_id") != pid:
            return {"error": "PROFILE_UNAVAILABLE: requested project has no available profile"}
        if not inst_id:
            inst_id = selected_inst_id
        if not inst_id:
            return {"error": "PROFILE_UNAVAILABLE: selected profile has no installation_id; reconnect the Flow extension"}
        if not pid:
            try:
                pid = client._batch_project_id("", preferred_installation=inst_id)
            except Exception as e:
                return {"error": f"PROFILE_UNAVAILABLE: selected profile has no usable Flow project: {e}"}

    # 1. Image Generation
    if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE", "GENERATE_CHARACTER_IMAGE"):
        prompt = payload.get("prompt") or ""
        aspect_ratio = payload.get("aspect_ratio") or (
            "IMAGE_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "IMAGE_ASPECT_RATIO_LANDSCAPE"
        )
        model = payload.get("model")
        input_images = payload.get("input_images") or []
        ref_media_ids = list(payload.get("character_media_ids") or []) + list(payload.get("reference_media_ids") or [])

        # Auto-upload Base64 or collect media_ids
        payload_modified = False
        for img in input_images:
            if not isinstance(img, dict):
                continue
            if img.get("media_id") and not img.get("image_base64"):
                ref_media_ids.append(img["media_id"])
            elif img.get("image_base64"):
                upload_res = await client.upload_image(
                    image_base64=img["image_base64"],
                    mime_type=img.get("mime_type") or "image/jpeg",
                    project_id=pid,
                    preferred_installation=inst_id,
                )
                if upload_res.get("error"):
                    return upload_res
                mid = upload_res.get("_mediaId") or upload_res.get("data", {}).get("media", {}).get("name")
                if mid:
                    img["media_id"] = mid
                    inst_id = upload_res.get("_installation_id") or inst_id
                    pid = upload_res.get("_projectId") or pid
                    payload_modified = True
                    ref_media_ids.append(mid)

        if payload_modified:
            try:
                await crud.update_request(rid, payload_json=json.dumps(payload))
            except Exception as e:
                logger.warning("Failed to cache uploaded media_id in request %s: %s", rid[:8], e)

        return await client.generate_images(
            prompt=prompt,
            project_id=pid,
            aspect_ratio=aspect_ratio,
            character_media_ids=ref_media_ids if ref_media_ids else None,
            image_model=model,
            preferred_installation=inst_id,
        )

    # 2. Video Generation
    if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
        prompt = payload.get("prompt") or ""
        aspect_ratio = payload.get("aspect_ratio") or (
            "VIDEO_ASPECT_RATIO_PORTRAIT" if orientation == "VERTICAL" else "VIDEO_ASPECT_RATIO_LANDSCAPE"
        )
        start_media_id = payload.get("start_media_id")
        end_media_id = payload.get("end_media_id")
        ref_media_ids = list(payload.get("reference_media_ids") or [])
        input_images = payload.get("input_images") or []
        video_model = payload.get("model") or "omni_flash"
        if video_model != "omni_flash":
            return {"error": "INVALID_ARGUMENT: client v1 video supports only omni_flash"}

        # Auto-upload Base64 or collect media_ids
        uploaded_mids = []
        payload_modified = False
        for img in input_images:
            if not isinstance(img, dict):
                continue
            if img.get("media_id") and not img.get("image_base64"):
                uploaded_mids.append(img["media_id"])
            elif img.get("image_base64"):
                upload_res = await client.upload_image(
                    image_base64=img["image_base64"],
                    mime_type=img.get("mime_type") or "image/jpeg",
                    project_id=pid,
                    preferred_installation=inst_id,
                )
                if upload_res.get("error"):
                    return upload_res
                mid = upload_res.get("_mediaId") or upload_res.get("data", {}).get("media", {}).get("name")
                if mid:
                    img["media_id"] = mid
                    inst_id = upload_res.get("_installation_id") or inst_id
                    pid = upload_res.get("_projectId") or pid
                    payload_modified = True
                    uploaded_mids.append(mid)

        if payload_modified:
            try:
                await crud.update_request(rid, payload_json=json.dumps(payload))
            except Exception as e:
                logger.warning("Failed to cache uploaded video media_id in request %s: %s", rid[:8], e)

        # Preserve explicit roles and include every input in an R2V request.
        # Keep the historical positional mapping for unlabelled I2V inputs.
        unlabelled_mids = []
        for img in input_images:
            mid = img.get("media_id")
            if not mid:
                continue
            role = img.get("role")
            if role == "start_frame":
                start_media_id = start_media_id or mid
            elif role == "end_frame":
                end_media_id = end_media_id or mid
            elif role == "reference" or req_type == "GENERATE_VIDEO_REFS":
                ref_media_ids.append(mid)
            else:
                unlabelled_mids.append(mid)

        uploaded_mids = unlabelled_mids

        if uploaded_mids:
            if not start_media_id:
                if len(uploaded_mids) == 2 and not is_ref_based and (
                    payload.get("generation_type") in ("start_end", "first_last")
                    or payload.get("type") in ("start_end", "first_last")
                ):
                    start_media_id = uploaded_mids[0]
                    end_media_id = uploaded_mids[1]
                else:
                    start_media_id = uploaded_mids[0]
                    if len(uploaded_mids) > 1:
                        ref_media_ids.extend(uploaded_mids[1:])
            else:
                if not end_media_id and len(uploaded_mids) == 1 and (
                    payload.get("generation_type") in ("start_end", "first_last")
                    or payload.get("type") in ("start_end", "first_last")
                ):
                    end_media_id = uploaded_mids[0]
                else:
                    ref_media_ids.extend(uploaded_mids)

        is_ref_based = req_type == "GENERATE_VIDEO_REFS" or bool(ref_media_ids)

        if video_model == "omni_flash" or payload.get("mode") == "omni":
            try:
                import importlib
                omni_mod = importlib.import_module("agent.services.client_omni")
                execute_omni = getattr(omni_mod, "execute_omni")
                payload["aspect_ratio"] = aspect_ratio
                return await execute_omni(
                    req, payload, client, inst_id, pid,
                    start_media_id=start_media_id, end_media_id=end_media_id,
                    reference_media_ids=ref_media_ids,
                )
            except (ImportError, ModuleNotFoundError, AttributeError):
                pass

        duration = payload.get("duration_seconds", 8)
        if is_ref_based and ref_media_ids:
            submit_result = await client.generate_video_from_references(
                reference_media_ids=ref_media_ids,
                prompt=prompt,
                project_id=pid,
                scene_id="",
                aspect_ratio=aspect_ratio,
                video_model=f"abra_r2v_{duration}s",
                preferred_installation=inst_id,
            )
        elif start_media_id and end_media_id:
            submit_result = await client.generate_video(
                start_image_media_id=start_media_id,
                end_image_media_id=end_media_id,
                prompt=prompt,
                project_id=pid,
                scene_id="",
                aspect_ratio=aspect_ratio,
                video_model=f"omni_flash_i2v_{duration}s_first_last",
                preferred_installation=inst_id,
            )
        else:
            if not start_media_id:
                return {"error": "Video generation requires start_media_id or input_images"}
            submit_result = await client.generate_video(
                start_image_media_id=start_media_id,
                prompt=prompt,
                project_id=pid,
                scene_id="",
                aspect_ratio=aspect_ratio,
                video_model=f"abra_i2v_{duration}s",
                preferred_installation=inst_id,
            )

        if _is_error(submit_result):
            return submit_result

        operations = _extract_operations(submit_result)
        if not operations:
            return {"error": "Video gen returned no operations"}

        op_name = operations[0].get("operation", {}).get("name", "")
        if rid:
            await crud.update_request(rid, request_id=op_name)

        status = operations[0].get("status", "")
        if status == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
            return submit_result
        if status == "MEDIA_GENERATION_STATUS_FAILED":
            return {"error": f"Operation failed immediately: {op_name}"}

        return {"_async_operation": True, "operations": operations, "_poll_client": client,
                "_installation_id": submit_result.get("_installation_id")}

    return {"error": f"Unsupported client v1 request type: {req_type}"}


async def _reupload_media(url: str, project_id: str, preferred_installation: str | None = None) -> str | None:
    """Download image from URL and re-upload to get a fresh media_id."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("Re-upload: failed to download %s (status %d)", url[:60], resp.status)
                    return None
                image_bytes = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")

        if not content_type.startswith("image/"):
            logger.warning("Re-upload: unexpected content-type %s from %s", content_type, url[:60])
            return None
        image_b64 = base64.b64encode(image_bytes).decode()
        mime = content_type.split(";")[0].strip()

        client = get_flow_client()
        result = await client.upload_image(
            image_b64, mime_type=mime, project_id=project_id,
            preferred_installation=preferred_installation,
        )
        new_mid = result.get("_mediaId")
        if new_mid:
            logger.info("Re-upload OK: fresh media_id=%s (inst=%s)",
                        new_mid[:20], result.get("_installation_id") or "?")
            return new_mid
        logger.warning("Re-upload: no media_id in response: %s", str(result)[:200])
    except Exception as e:
        logger.warning("Re-upload failed: %s", e)
    return None


async def _recover_entity_not_found(req: dict, preferred_installation: str | None = None) -> bool:
    """When Google returns 'entity not found' or quota fails over, re-upload to get fresh media_id."""
    req_type = req.get("type", "")
    pid = req.get("project_id", "")
    orientation = await _resolve_orientation(req)
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"

    # Scene-based requests: re-upload scene image
    if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
        scene = await crud.get_scene(req.get("scene_id"))
        if not scene:
            return False
        url = scene.get(f"{prefix}_image_url")
        if not url:
            return False
        new_mid = await _reupload_media(url, pid, preferred_installation=preferred_installation)
        if new_mid:
            await crud.update_scene(scene["id"], **{f"{prefix}_image_media_id": new_mid})
            logger.info("Recovered scene %s: new %s_image_media_id=%s", scene["id"][:12], prefix, new_mid[:12])
            return True

    # Scene image generation with character refs: re-upload character refs if needed
    if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE"):
        scene = await crud.get_scene(req.get("scene_id"))
        if scene and scene.get("character_names") and pid:
            char_names_raw = scene.get("character_names")
            if isinstance(char_names_raw, str):
                try:
                    char_names_raw = json.loads(char_names_raw)
                except Exception:
                    char_names_raw = []
            if isinstance(char_names_raw, list):
                project_chars = await crud.get_project_characters(pid)
                char_names_set = set(char_names_raw)
                recovered_any = False
                for c in project_chars:
                    if (c.get("slug") in char_names_set or c.get("name") in char_names_set) and c.get("reference_image_url"):
                        new_mid = await _reupload_media(c["reference_image_url"], pid, preferred_installation=preferred_installation)
                        if new_mid:
                            await crud.update_character(c["id"], media_id=new_mid)
                            logger.info("Recovered character ref %s: new media_id=%s", c["name"], new_mid[:12])
                            recovered_any = True
                if recovered_any:
                    return True

    # Character-based requests: re-upload ref image
    if req_type in ("EDIT_CHARACTER_IMAGE",):
        char = await crud.get_character(req.get("character_id"))
        if not char:
            return False
        url = char.get("reference_image_url")
        if not url:
            return False
        new_mid = await _reupload_media(url, pid, preferred_installation=preferred_installation)
        if new_mid:
            await crud.update_character(char["id"], media_id=new_mid)
            logger.info("Recovered character %s: new media_id=%s", char["id"][:12], new_mid[:12])
            return True

    return False


async def _handle_failure(rid: str, req: dict, result: dict, retry_after: dict = None):
    error_msg = result.get("error")
    if not error_msg:
        data = result.get("data", {})
        if isinstance(data, dict):
            ef = data.get("error", "Unknown error")
            if isinstance(ef, dict):
                error_msg = ef.get("message", json.dumps(ef)[:200])
                # Extract detailed reason from error details (e.g. PUBLIC_ERROR_UNSAFE_GENERATION)
                details = ef.get("details", [])
                if details and isinstance(details, list):
                    for d in details:
                        reason = d.get("reason") if isinstance(d, dict) else None
                        if reason:
                            error_msg = f"{error_msg} [{reason}]"
                            break
            else:
                error_msg = str(ef)
        else:
            error_msg = "Unknown error"
    if isinstance(error_msg, dict):
        error_msg = json.dumps(error_msg)[:200]

    # Auto-recover expired media by re-uploading
    if "not found" in str(error_msg).lower():
        recovered = await _recover_entity_not_found(req)
        if recovered:
            logger.info("Request %s: recovered expired media, retrying", rid[:8])
            await crud.update_request(rid, status="PENDING", error_message=f"recovered: {error_msg}")
            return

    error_lower = str(error_msg).lower()

    from agent.services.flow_client import is_quota_error
    if is_quota_error(error_msg):
        client = get_flow_client()
        failed_inst = (
            result.get("_installation_id")
            or req.get("installation_id")
            or (client._extensions.get(client._extension_ws, {}).get("installation_id") if client._extension_ws else None)
        )
        if failed_inst:
            client.mark_quota_exhausted(failed_inst)

        retry = req.get("retry_count", 0) + 1
        if retry < MAX_RETRIES:
            logger.warning(
                "Request %s: Account %s quota exhausted. Retrying with alternative profile (retry %d/%d).",
                rid[:8], failed_inst or "unknown", retry, MAX_RETRIES,
            )
            # Pre-recover / re-upload media if this request is scene or character based
            if req.get("scene_id") or req.get("character_id"):
                try:
                    await _recover_entity_not_found(req)
                except Exception as e:
                    logger.warning("Could not pre-recover media during quota failover: %s", e)

            # Reset request to PENDING and clear installation_id so candidate profile picks it up
            await crud.update_request(
                rid, status="PENDING", retry_count=retry, installation_id=None,
                error_message=f"failover: account {failed_inst} quota exhausted; switching to another account",
            )
            if retry_after is not None:
                retry_after[rid] = time.time() + 1.0  # short cooldown before picking up with alternative
            return
        else:
            # Max retries reached or all accounts out of quota
            error_text = f"QUOTA_EXHAUSTED: Account {failed_inst or 'active'} has exhausted quota (max retries reached): {error_msg}"
            await crud.update_request(rid, status="FAILED", error_message=error_text)
            await _mark_scene_failed(req)
            logger.error("Request %s FAILED permanently: %s", rid[:8], error_text)
            return

    if "media_owner_unknown" in error_lower or "media_account_mismatch" in error_lower:
        await crud.update_request(rid, status="FAILED", error_message=str(error_msg))
        await _mark_scene_failed(req)
        return

    if "profile_unavailable" in error_lower:
        retry = req.get("retry_count", 0) + 1
        if retry <= 3:
            await crud.update_request(rid, status="PENDING", retry_count=retry, error_message=str(error_msg))
            if retry_after is not None:
                retry_after[rid] = time.time() + 60
            logger.warning("Request %s profile unavailable (retry %d/3 in 60s): %s", rid[:8], retry, error_msg)
            return
        await crud.update_request(rid, status="FAILED", error_message=f"PROFILE_UNAVAILABLE_MAX_RETRIES: {error_msg}")
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED permanently: profile unavailable after 3 retries", rid[:8])
        return

    if "unsupported_on_batch_api" in error_lower or "failed: [3]" in error_lower or "invalid_argument" in error_lower or "model_access_denied" in error_lower:
        await crud.update_request(rid, status="FAILED", error_message=str(error_msg))
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED (not retryable): %s", rid[:8], error_msg)
        return

    if "no_flow_project" in error_lower:
        retry = req.get("retry_count", 0) + 1
        if retry < 4:
            await crud.update_request(rid, status="PENDING", retry_count=retry, error_message=str(error_msg))
            if retry_after is not None:
                retry_after[rid] = time.time() + 3.0
            logger.info("Request %s waiting for Flow project sync (retry %d/3 in 3s)", rid[:8], retry)
            return
        await crud.update_request(rid, status="FAILED", error_message=str(error_msg))
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED (max project retries reached): %s", rid[:8], error_msg)
        return

    # WS transient errors (extension disconnect/reconnect): retry up to 3 times
    if "extension reconnected" in error_lower or "extension disconnected" in error_lower or "extension not connected" in error_lower:
        retry = req.get("retry_count", 0) + 1
        if retry <= 3:
            await crud.update_request(rid, status="PENDING", retry_count=retry, error_message=str(error_msg))
            if retry_after is not None:
                retry_after[rid] = time.time() + 15
            logger.info("Request %s transient WS error (retry %d/3 in 15s): %s", rid[:8], retry, error_msg)
            return
        await crud.update_request(rid, status="FAILED", error_message=f"EXTENSION_DISCONNECTED_MAX_RETRIES: {error_msg}")
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED permanently: extension disconnected after 3 retries", rid[:8])
        return

    # reCAPTCHA errors: retry up to 10 times — deferred dict in main loop handles delay
    if "captcha" in error_lower or "recaptcha" in error_lower:
        retry = req.get("retry_count", 0) + 1
        if retry < 10:
            await crud.update_request(rid, status="PENDING", retry_count=retry, error_message=str(error_msg))
            logger.warning("Request %s reCAPTCHA failed (retry %d/10), will retry", rid[:8], retry)
            return
        else:
            await crud.update_request(rid, status="FAILED", error_message=str(error_msg))
            await _mark_scene_failed(req)
            logger.error("Request %s FAILED after 10 reCAPTCHA retries: %s", rid[:8], error_msg)
            return

    retry = req.get("retry_count", 0) + 1
    if retry < MAX_RETRIES:
        now = time.time()
        if retry_after is not None:
            ra = retry_after.get(rid, 0.0)
            if ra > now:
                # Still in backoff — reset to PENDING so it's not stuck in PROCESSING
                await crud.update_request(rid, status="PENDING", error_message=str(error_msg))
                return
            retry_after[rid] = now + min(2 ** retry * 10, 300)
        await crud.update_request(rid, status="PENDING", retry_count=retry, error_message=str(error_msg))
        logger.warning("Request %s failed (retry %d/%d): %s", rid[:8], retry, MAX_RETRIES, error_msg)
    else:
        await crud.update_request(rid, status="FAILED", error_message=str(error_msg))
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED permanently: %s", rid[:8], error_msg)


async def _mark_scene_failed(req: dict):
    scene_id = req.get("scene_id")
    if not scene_id:
        return
    orientation = await _resolve_orientation(req)
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
    req_type = req["type"]
    updates = {}
    if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE"):
        updates[f"{prefix}_image_status"] = "FAILED"
    elif req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
        updates[f"{prefix}_video_status"] = "FAILED"
    elif req_type == "UPSCALE_VIDEO":
        updates[f"{prefix}_upscale_status"] = "FAILED"
    if updates:
        await crud.update_scene(scene_id, **updates)


async def _is_already_completed(req: dict, orientation: str) -> bool:
    scene_id = req.get("scene_id")
    req_type = req.get("type", "")
    if not scene_id or req_type == "GENERATE_CHARACTER_IMAGE":
        return False
    scene = await crud.get_scene(scene_id)
    if not scene:
        return False
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
    if req_type in ("EDIT_IMAGE", "REGENERATE_IMAGE", "REGENERATE_VIDEO", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
        return False  # Always run — explicitly requesting new generation
    if req_type == "GENERATE_IMAGE":
        return scene.get(f"{prefix}_image_status") == "COMPLETED"
    if req_type in ("GENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
        return scene.get(f"{prefix}_video_status") == "COMPLETED"
    if req_type == "UPSCALE_VIDEO":
        return scene.get(f"{prefix}_upscale_status") == "COMPLETED"
    return False


# ─── Module-level controller ──────────────────────────────────

_controller: WorkerController | None = None


def get_worker_controller() -> WorkerController:
    global _controller
    if _controller is None:
        _controller = WorkerController()
    return _controller
