from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

import aiohttp
from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from agent.api.v1.schemas import (
    BatchImageGenerationRequest,
    BatchVideoGenerationRequest,
    GeneratedMedia,
    ImageEditRequest,
    ImageGenerationRequest,
    ImageUpscaleRequest,
    Job,
    JobError,
    JobMetadata,
    JobsResponse,
    JobPollResponse,
    JobStatusRequest,
    VideoGenerationRequest,
    normalize_image_model,
)
from agent.api.v1.errors import (
    V1ErrorCode,
    build_v1_job_error,
    parse_v1_error,
    raise_v1_http_error,
)
from agent.db import crud
from agent.db.schema import get_db, _db_lock
from agent.services.flow_client import get_flow_client
from agent.services.v1_multi_extension import (
    V1RoutingError,
    get_v1_multi_extension_router,
)
from agent.worker._parsing import _extract_media_items

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Client API v1"])


def _ensure_v1_routing_ready(client):
    """Return the V1 routing adapter or expose a stable V1 availability error."""
    profile_router = get_v1_multi_extension_router(client)
    try:
        profile_router.ensure_connected()
    except V1RoutingError as exc:
        raise_v1_http_error(
            str(exc),
            status_code=503,
            default_message="Chrome extension FlowKit chưa kết nối với backend server.",
            default_action="Vui lòng mở trình duyệt Chrome có cài extension FlowKit và kiểm tra trạng thái kết nối.",
        )
    return profile_router


async def _resolve_v1_route(client, payload: dict):
    """Resolve Apikit V1 profile/project affinity before calling FlowKit core."""
    profile_router = _ensure_v1_routing_ready(client)
    try:
        route = await profile_router.resolve_context(payload)
    except V1RoutingError as exc:
        raise_v1_http_error(
            str(exc),
            status_code=503 if exc.code == "PROFILE_UNAVAILABLE" else 409,
            default_message="Không thể chọn tài khoản Flow phù hợp cho yêu cầu này.",
            default_action="Kiểm tra extension/profile, quota hoặc upload lại ảnh trên cùng tài khoản.",
        )
    return profile_router, route


def _wake_worker() -> None:
    """Start queued v1 work immediately when the worker has capacity."""
    try:
        from agent.worker.processor import get_worker_controller
        get_worker_controller().notify_work_available()
    except Exception:
        pass


def _resolve_idempotency_key(header_key: str | None, body_key: str | None) -> str | None:
    """Accept the standard HTTP header while retaining body compatibility."""
    if header_key and body_key and header_key != body_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key header conflicts with idempotency_key body field.")
    return header_key or body_key


async def _find_idempotent_job(db, key: str | None, request_type: str) -> str | None:
    """Return the newest existing job for a client retry key."""
    if not key:
        return None
    cur = await db.execute(
        "SELECT id, payload_json FROM request WHERE type=? AND payload_json IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1000",
        (request_type,),
    )
    for row in await cur.fetchall():
        try:
            payload = json.loads(row[1] or "{}")
        except Exception:
            continue
        if payload.get("idempotency_key") == key:
            return row[0]
    return None


def _normalize_image_aspect(aspect: str | None) -> str:
    raw = str(aspect or "").strip().upper()
    mapping = {
        "IMAGE_ASPECT_RATIO_LANDSCAPE": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "IMAGE_ASPECT_RATIO_PORTRAIT": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "IMAGE_ASPECT_RATIO_SQUARE": "IMAGE_ASPECT_RATIO_SQUARE",
        "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE": "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE",
        "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
        "LANDSCAPE": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "PORTRAIT": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "SQUARE": "IMAGE_ASPECT_RATIO_SQUARE",
        "HORIZONTAL": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "VERTICAL": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
        "3:4": "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE",
        "4:3": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    }
    return mapping.get(raw, "IMAGE_ASPECT_RATIO_LANDSCAPE")


def _normalize_video_aspect(aspect: str | None) -> str:
    raw = str(aspect or "").strip().upper()
    mapping = {
        "VIDEO_ASPECT_RATIO_LANDSCAPE": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "VIDEO_ASPECT_RATIO_PORTRAIT": "VIDEO_ASPECT_RATIO_PORTRAIT",
        "LANDSCAPE": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "PORTRAIT": "VIDEO_ASPECT_RATIO_PORTRAIT",
        "HORIZONTAL": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "VERTICAL": "VIDEO_ASPECT_RATIO_PORTRAIT",
        "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
    }
    return mapping.get(raw, "VIDEO_ASPECT_RATIO_LANDSCAPE")


def _build_job_item(req: dict) -> Job:
    jid = req["id"]
    raw_status = req.get("status", "PENDING")
    status_map = {
        "PENDING": "queued",
        "PROCESSING": "running",
        "COMPLETED": "complete",
        "FAILED": "failed",
    }
    job_status = status_map.get(raw_status, "queued")
    is_video = "VIDEO" in req.get("type", "")
    job_type = "video" if is_video else "image"
    generation_type = job_type
    project_id = None
    if req.get("payload_json"):
        try:
            pj = json.loads(req["payload_json"])
            if pj.get("type"):
                generation_type = pj["type"]
            if pj.get("project_id"):
                project_id = pj["project_id"]
        except Exception:
            pass

    media_items: list[GeneratedMedia] = []
    if req.get("payload_json"):
        try:
            pj_media = json.loads(req["payload_json"]).get("generated_media", [])
            for m in pj_media:
                if isinstance(m, dict) and (m.get("url") or m.get("media_id")):
                    media_items.append(
                        GeneratedMedia(
                            id=m.get("media_id") or jid,
                            type=job_type,
                            url=m.get("url"),
                            media_id=m.get("media_id"),
                        )
                    )
        except Exception:
            pass

    if not media_items and req.get("output_url"):
        media_items.append(
            GeneratedMedia(
                id=req.get("media_id") or jid,
                type=job_type,
                url=req["output_url"],
                media_id=req.get("media_id"),
            )
        )

    job_error: JobError | None = None
    err_msg = req.get("error_message")
    op_id = req.get("media_id") or req.get("request_id")
    if not op_id or op_id == jid:
        try:
            if req.get("payload_json"):
                ops = json.loads(req["payload_json"]).get("operations", [])
                if ops and isinstance(ops[0], dict):
                    extracted_op = ops[0].get("name") or ops[0].get("operation", {}).get("name")
                    if extracted_op:
                        op_id = extracted_op
        except Exception:
            pass
    if raw_status == "FAILED":
        job_error = build_v1_job_error(err_msg or "Generation failed", op_id=op_id)
    elif raw_status in ("PROCESSING", "PENDING") and not req.get("output_url"):
        created_at_str = req.get("created_at") or req.get("updated_at")
        if created_at_str:
            try:
                from datetime import datetime, timezone
                ts_clean = created_at_str.replace("Z", "+00:00")
                cat = datetime.fromisoformat(ts_clean)
                if cat.tzinfo is None:
                    cat = cat.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - cat).total_seconds()
                from agent.config import VIDEO_POLL_TIMEOUT
                if elapsed > VIDEO_POLL_TIMEOUT:
                    job_status = "failed"
                    job_error = build_v1_job_error(
                        f"Generation timed out after {int(elapsed)}s (Exceeded VIDEO_POLL_TIMEOUT {VIDEO_POLL_TIMEOUT}s)",
                        op_id=op_id,
                    )
            except Exception:
                pass

    phase = "queued" if job_status == "queued" else ("polling" if op_id else "submitting")
    if job_status == "complete":
        phase = "complete"
    elif job_status == "failed":
        phase = "failed"

    return Job(
        id=jid,
        operation_id=op_id,
        project_id=project_id,
        routing_scope=None,
        provider="google_flow",
        type=job_type,
        generation_type=generation_type,
        status=job_status,
        phase=phase,
        media=media_items,
        error=job_error,
        installation_id=req.get("installation_id"),
    )


async def _resolve_jobs_response(job_ids: list[str]) -> JobsResponse:
    jobs: list[Job] = []
    counts: dict[str, int] = {"queued": 0, "running": 0, "complete": 0, "failed": 0}

    for jid in job_ids:
        req = await crud.get_request(jid)
        if req:
            item = _build_job_item(req)
            jobs.append(item)
            if item.status in counts:
                counts[item.status] += 1
        else:
            item = Job(
                id=jid,
                provider="google_flow",
                type="image",
                generation_type="image",
                status="failed",
                error=JobError(
                    code="JOB_NOT_FOUND",
                    message=f"Job {jid} not found.",
                ),
            )
            jobs.append(item)
            counts["failed"] += 1

    done = all(j.status in ("complete", "failed") for j in jobs)
    metadata = JobMetadata(
        counts=counts,
        done=done,
        poll_after_seconds=None if done else 10,
    )

    first_job = jobs[0] if jobs else None
    return JobsResponse(
        jobs=jobs,
        metadata=metadata,
        job_id=first_job.id if first_job else None,
        operation_id=first_job.operation_id if first_job else None,
        type=first_job.type if first_job else None,
        generation_type=first_job.generation_type if first_job else None,
        status=first_job.status if first_job else None,
        media=first_job.media if first_job else [],
        error=first_job.error if first_job else None,
        installation_id=first_job.installation_id if first_job else None,
    )


async def _resolve_single_job_response(job_id: str) -> JobPollResponse:
    req = await crud.get_request(job_id)
    if not req:
        return JobPollResponse(
            job_id=job_id,
            id=job_id,
            status="failed",
            type="video",
            url=None,
            media=[],
            error=JobError(
                code="JOB_NOT_FOUND",
                message=f"Job {job_id} not found.",
            ),
        )

    item = _build_job_item(req)
    primary_url = item.media[0].url if item.media else req.get("output_url")
    return JobPollResponse(
        job_id=item.id,
        id=item.id,
        status=item.status,
        type=item.type,
        url=primary_url,
        media=item.media,
        error=item.error,
        installation_id=item.installation_id,
    )


async def _fetch_url_as_base64(url: str) -> tuple[str, str]:
    """Fetch an image from URL and return (base64_str, mime_type)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=400, detail=f"Failed to fetch image from URL: {url} (HTTP {resp.status})")
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                data = await resp.read()
                return base64.b64encode(data).decode(), content_type
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Error downloading image from {url}: {e}")


async def _resolve_media_id_from_input(
    client,
    *,
    media_id: str | None = None,
    image_base64: str | None = None,
    image_url: str | None = None,
    mime_type: str = "image/jpeg",
    project_id: str = "",
    preferred_installation: str | None = None,
) -> tuple[str, str | None]:
    """Resolve an input image into a Google Flow media_id, uploading if necessary."""
    if media_id:
        return media_id, preferred_installation
    if image_url and not image_base64:
        image_base64, mime_type = await _fetch_url_as_base64(image_url)
    if image_base64:
        profile_router = _ensure_v1_routing_ready(client)
        res = await profile_router.upload_image(
            image_base64=image_base64,
            mime_type=mime_type or "image/jpeg",
            project_id=project_id,
            preferred_installation=preferred_installation,
        )
        if res.get("error"):
            raise_v1_http_error(
                res.get("error"),
                default_message="Tải ảnh lên Google Flow thất bại",
                default_action="Kiểm tra tab Google Flow hoặc thử lại với ảnh dung lượng nhỏ hơn.",
            )
        mid = res.get("_mediaId") or res.get("name") or res.get("media_id") or res.get("data", {}).get("media", {}).get("name")
        inst_id = res.get("_installation_id") or preferred_installation
        if not mid:
            raise_v1_http_error(
                "Failed to obtain media_id from Google Flow upload",
                status_code=502,
                default_message="Không nhận được media_id từ Google Flow sau khi upload ảnh.",
                default_action="Kiểm tra tab Google Flow hoặc thử lại với ảnh khác.",
            )
        return mid, inst_id
    raise HTTPException(
        status_code=400,
        detail={
            "code": "MISSING_IMAGE_INPUT",
            "message": "Thiếu ảnh đầu vào (phải cung cấp image_base64, image_url, hoặc media_id).",
            "details": "No image provided (must provide image_base64, image_url, or media_id)",
            "action": "Vui lòng cung cấp chuỗi image_base64 hoặc đường dẫn image_url hợp lệ.",
        },
    )


async def _poll_video_result(client, submit_result: dict, timeout: int = 120) -> dict:
    """Poll a video generation result synchronously until completed or timeout."""
    data = submit_result.get("data", submit_result)
    polling_info = data.get("flowkitPolling", {}) if isinstance(data, dict) else {}
    mode = polling_info.get("mode")

    if mode == "batch_media":
        from agent.services.omni_flash import _check_omni_batch_media
        workflows = polling_info.get("workflows") or data.get("workflows") or []
        start_time = asyncio.get_event_loop().time()
        poll_interval = 4
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(poll_interval)
            check = await _check_omni_batch_media(workflows, project_id=polling_info.get("project_id", ""))
            if check.get("done"):
                for wf in check.get("workflows", []):
                    m = wf.get("media", {})
                    if m.get("url"):
                        return {"status": 200, "media_id": m.get("media_id"), "url": m.get("url")}
                raise_v1_http_error(
                    "Google Flow đã hoàn thành xử lý nhưng không trả về URL video (có thể do kiểm duyệt nội dung hậu kỳ hoặc tác vụ bị huỷ).",
                    status_code=502,
                )
        raise_v1_http_error(
            f"Omni Flash video generation timed out after {timeout}s",
            status_code=504,
            default_message=f"Thời gian chờ tạo video từ Google Flow vượt quá giới hạn ({timeout}s).",
        )

    # Mode: batch_operation or standard operations
    operations = submit_result.get("operations") or (data.get("operations", []) if isinstance(data, dict) else [])
    if not operations and "operation" in submit_result:
        operations = [submit_result]
    if not operations and isinstance(data, dict) and "operation" in data:
        operations = [data]
    if not operations:
        raise_v1_http_error(
            "No operations returned from video generation",
            status_code=502,
            default_message="Google Flow không trả về operation hợp lệ cho tác vụ video.",
        )

    # If already marked successful (e.g. mock or immediate completion)
    first_op = operations[0]
    if first_op.get("status") == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
        op_meta = first_op.get("operation", {}).get("metadata", {}).get("video", {})
        url = op_meta.get("fifeUrl") or op_meta.get("url") or first_op.get("url") or ""
        mid = op_meta.get("mediaId") or first_op.get("media_id") or ""
        if url:
            return {"status": 200, "media_id": mid, "url": url}

    from agent.sdk.services.operations import _poll_operations
    from agent.sdk.services.result_handler import parse_result

    poll_res = await _poll_operations(client, operations, timeout=timeout)
    if poll_res.get("error"):
        raise_v1_http_error(poll_res.get("error"), default_message="Lỗi trong quá trình render video")

    gen_res = parse_result(poll_res, "GENERATE_VIDEO")
    if not gen_res.success:
        raise_v1_http_error(gen_res.error or "Video generation failed", default_message="Tạo video thất bại")

    return {"status": 200, "media_id": gen_res.media_id, "url": gen_res.url}


async def _background_monitor_video(
    job_id: str,
    client,
    submit_result: dict,
    orientation: str,
    req_type: str,
    payload_dict: dict,
    target_inst: str | None,
) -> None:
    try:
        poll_res = await _poll_video_result(client, submit_result, timeout=180)
        video_url = poll_res.get("url")
        video_mid = poll_res.get("media_id") or job_id
        if video_url:
            media_items = [{"media_id": video_mid, "url": video_url}]
            payload_dict["generated_media"] = media_items
            db = await get_db()
            async with _db_lock:
                await db.execute(
                    """
                    UPDATE request
                    SET status = 'COMPLETED', media_id = ?, output_url = ?, payload_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                    WHERE id = ?
                    """,
                    (video_mid, video_url, json.dumps(payload_dict), job_id),
                )
                await db.commit()
            logger.info("Background monitor completed video Job %s (url=%s)", job_id, video_url[:60])
        else:
            await crud.update_request(job_id, status="FAILED", error_message="Video rendering returned no URL")
    except Exception as exc:
        logger.exception("Background monitor failed for video Job %s: %s", job_id, exc)
        await crud.update_request(job_id, status="FAILED", error_message=str(exc))


@router.post("/v1/images/generations", response_model=JobsResponse, status_code=status.HTTP_200_OK)
async def generate_image(
    payload: ImageGenerationRequest,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=200),
):
    """Generate images directly with Banana Pro / Banana 2 (synchronous 200 OK)."""
    client = get_flow_client()

    header_val = idempotency_header if isinstance(idempotency_header, str) else None
    idempotency_key = _resolve_idempotency_key(header_val, payload.idempotency_key)
    db = await get_db()
    if idempotency_key:
        async with _db_lock:
            existing_id = await _find_idempotent_job(db, idempotency_key, "GENERATE_IMAGE")
            if existing_id:
                return await _resolve_jobs_response([existing_id])

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_image_aspect(payload.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"
    model = normalize_image_model(payload.model or "NANO_BANANA_PRO")
    _, route = await _resolve_v1_route(client, {
        "installation_id": payload.installation_id,
        "project_id": payload.project_id or "",
    })
    inst_id = route.installation_id
    pid = route.project_id

    ref_media_ids = []
    if payload.input_images:
        for img in payload.input_images:
            mid, inst_id = await _resolve_media_id_from_input(
                client,
                media_id=img.media_id,
                image_base64=img.image_base64,
                image_url=img.image_url,
                mime_type=img.mime_type,
                project_id=pid,
                preferred_installation=inst_id,
            )
            if mid:
                ref_media_ids.append(mid)

    payload_dict = {
        "prompt": payload.prompt,
        "idempotency_key": idempotency_key,
        "installation_id": inst_id,
        "aspect_ratio": aspect,
        "model": model,
        "count": payload.count,
        "project_id": pid,
        "generated_media": [],
    }

    res = await client.generate_images(
        prompt=payload.prompt,
        project_id=pid,
        aspect_ratio=aspect,
        character_media_ids=ref_media_ids if ref_media_ids else None,
        image_model=model,
        preferred_installation=inst_id,
        count=payload.count,
    )

    if res.get("error") or (isinstance(res.get("status"), int) and res["status"] >= 400):
        code = res.get("status") if isinstance(res.get("status"), int) and res.get("status") >= 400 else None
        raise_v1_http_error(res.get("error") or "Image generation failed", status_code=code, default_message="Sinh ảnh thất bại")

    media_items = _extract_media_items(res, "GENERATE_IMAGE")
    if not media_items:
        data = res.get("data", res)
        if isinstance(data, dict) and data.get("url"):
            media_items = [{"media_id": data.get("media_id") or job_id, "url": data["url"]}]
        else:
            raise_v1_http_error(
                "Image generation succeeded but returned no media items",
                status_code=502,
                default_message="Google Flow đã xử lý xong nhưng không trả về ảnh nào.",
                default_action="Thử lại với prompt khác hoặc kiểm tra quota trên tab Google Flow.",
            )

    primary_media = media_items[0]
    target_inst = res.get("_installation_id") or inst_id
    payload_dict["installation_id"] = target_inst
    payload_dict["generated_media"] = media_items

    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, type, orientation, status, media_id, output_url, installation_id, payload_json, created_at, updated_at)
            VALUES (?, 'GENERATE_IMAGE', ?, 'COMPLETED', ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, orientation, primary_media.get("media_id"), primary_media.get("url"), target_inst, json.dumps(payload_dict)),
        )
        await db.commit()

    logger.info("v1 Client API direct Image Job %s completed", job_id)
    return await _resolve_jobs_response([job_id])


@router.post("/v1/images/generations/batch", response_model=JobsResponse, status_code=status.HTTP_200_OK)
async def generate_images_batch(
    payload: BatchImageGenerationRequest | list[ImageGenerationRequest],
):
    """Submit batch image generation tasks directly (synchronous 200 OK)."""
    items = payload.requests if isinstance(payload, BatchImageGenerationRequest) else payload
    if not items:
        raise HTTPException(status_code=422, detail="Requests list cannot be empty")

    job_ids: list[str] = []
    for item in items:
        resp = await generate_image(item)
        if resp.jobs:
            job_ids.append(resp.jobs[0].id)

    return await _resolve_jobs_response(job_ids)


@router.post("/v1/videos/generations", response_model=JobsResponse, status_code=status.HTTP_200_OK)
async def generate_video(
    payload: VideoGenerationRequest,
    response: Response = Response(),
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=200),
):
    """Generate videos with Omni Flash (asynchronous 200 OK, returns job_id for polling)."""
    client = get_flow_client()
    header_val = idempotency_header if isinstance(idempotency_header, str) else None
    idempotency_key = _resolve_idempotency_key(header_val, payload.idempotency_key)
    is_ref_based = payload.type in ("reference_to_video", "ingredients", "references", "omni", "r2v")
    req_type = "GENERATE_VIDEO_REFS" if is_ref_based else "GENERATE_VIDEO"

    db = await get_db()
    if idempotency_key:
        async with _db_lock:
            existing_id = await _find_idempotent_job(db, idempotency_key, req_type)
            if existing_id:
                return await _resolve_jobs_response([existing_id])

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_video_aspect(payload.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"
    duration = payload.duration_seconds
    resolution = getattr(payload, "resolution", "720p") or "720p"
    seed = getattr(payload, "seed", None)
    _, route = await _resolve_v1_route(client, {
        "installation_id": payload.installation_id,
        "project_id": payload.project_id or "",
    })
    inst_id = route.installation_id
    pid = route.project_id

    is_t2v = payload.type in ("text_to_video", "t2v", "text")
    start_mid = payload.start_media_id
    end_mid = payload.end_media_id
    ref_mids = list(payload.reference_media_ids or [])

    if not is_t2v and payload.input_images:
        for idx, img in enumerate(payload.input_images):
            mid, inst_id = await _resolve_media_id_from_input(
                client,
                media_id=img.media_id,
                image_base64=img.image_base64,
                image_url=img.image_url,
                mime_type=img.mime_type,
                project_id=pid,
                preferred_installation=inst_id,
            )
            role = img.role
            if role == "start_frame" or (idx == 0 and not start_mid and role != "reference"):
                start_mid = mid
            elif role == "end_frame" or (idx == 1 and start_mid and not end_mid and len(payload.input_images) == 2 and role != "reference"):
                end_mid = mid
            else:
                ref_mids.append(mid)

    # Check if client has mock/override method, else call omni_flash service functions
    from agent.services import omni_flash

    if is_t2v:
        if hasattr(client, "generate_text_video"):
            submit_result = await client.generate_text_video(
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                aspect_ratio=aspect,
                seed=seed,
                preferred_installation=inst_id,
            )
        else:
            submit_result = await omni_flash.generate_omni_flash_text_video(
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                aspect_ratio=aspect,
                seed=seed,
                preferred_installation=inst_id,
            )
    elif start_mid and end_mid:
        if hasattr(client, "generate_video"):
            submit_result = await client.generate_video(
                start_image_media_id=start_mid,
                end_image_media_id=end_mid,
                prompt=payload.prompt,
                project_id=pid,
                scene_id="",
                aspect_ratio=aspect,
            )
        else:
            submit_result = await omni_flash.generate_omni_flash_first_last_video(
                start_image_media_id=start_mid,
                end_image_media_id=end_mid,
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                resolution=resolution,
                aspect_ratio=aspect,
                seed=seed,
                preferred_installation=inst_id,
            )
    elif start_mid and not ref_mids:
        if hasattr(client, "generate_video"):
            submit_result = await client.generate_video(
                start_image_media_id=start_mid,
                prompt=payload.prompt,
                project_id=pid,
                scene_id="",
                aspect_ratio=aspect,
            )
        else:
            submit_result = await omni_flash.generate_omni_flash_first_frame_video(
                start_image_media_id=start_mid,
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                resolution=resolution,
                aspect_ratio=aspect,
                seed=seed,
                preferred_installation=inst_id,
            )
    elif ref_mids:
        all_refs = ([start_mid] if start_mid else []) + ref_mids
        if hasattr(client, "generate_video_from_references"):
            submit_result = await client.generate_video_from_references(
                reference_media_ids=all_refs,
                prompt=payload.prompt,
                project_id=pid,
                scene_id="",
                aspect_ratio=aspect,
            )
        else:
            submit_result = await omni_flash.generate_omni_flash_video(
                reference_media_ids=all_refs,
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                resolution=resolution,
                aspect_ratio=aspect,
                preferred_installation=inst_id,
            )
    else:
        if hasattr(client, "generate_text_video"):
            submit_result = await client.generate_text_video(
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                aspect_ratio=aspect,
                seed=seed,
                preferred_installation=inst_id,
            )
        else:
            submit_result = await omni_flash.generate_omni_flash_text_video(
                prompt=payload.prompt,
                project_id=pid,
                duration_s=duration,
                aspect_ratio=aspect,
                seed=payload.seed,
                preferred_installation=inst_id,
            )

    if submit_result.get("error") or (isinstance(submit_result.get("status"), int) and submit_result["status"] >= 400):
        code = submit_result.get("status") if isinstance(submit_result.get("status"), int) and submit_result.get("status") >= 400 else None
        raise_v1_http_error(submit_result.get("error") or "Video submission failed", status_code=code, default_message="Gửi yêu cầu tạo video thất bại")

    target_inst = submit_result.get("_installation_id") or inst_id
    operations = submit_result.get("operations") or (submit_result.get("data", {}).get("operations", []) if isinstance(submit_result.get("data"), dict) else [])
    if not operations and "operation" in submit_result:
        operations = [submit_result]
    if not operations and isinstance(submit_result.get("data"), dict) and "operation" in submit_result["data"]:
        operations = [submit_result["data"]]

    op_id = None
    if operations and isinstance(operations[0], dict):
        op_id = operations[0].get("name") or operations[0].get("operation", {}).get("name")

    payload_dict = {
        "prompt": payload.prompt,
        "idempotency_key": idempotency_key,
        "installation_id": target_inst,
        "type": payload.type,
        "aspect_ratio": aspect,
        "duration_seconds": duration,
        "resolution": resolution,
        "project_id": pid,
        "operations": operations,
        "generated_media": [],
    }

    # Polling mode: record as PROCESSING and monitor in background
    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, type, orientation, status, media_id, output_url, installation_id, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, 'PROCESSING', ?, NULL, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, req_type, orientation, op_id or job_id, target_inst, json.dumps(payload_dict)),
        )
        await db.commit()

    asyncio.create_task(
        _background_monitor_video(
            job_id=job_id,
            client=client,
            submit_result=submit_result,
            orientation=orientation,
            req_type=req_type,
            payload_dict=payload_dict,
            target_inst=target_inst,
        )
    )
    logger.info("v1 Client API Video Job %s submitted for polling (op=%s)", job_id, op_id)
    if response is not None:
        response.headers["Location"] = f"/v1/jobs/{job_id}"
        response.headers["Retry-After"] = "10"
    return await _resolve_jobs_response([job_id])


@router.post("/v1/videos/generations/batch", response_model=JobsResponse, status_code=status.HTTP_200_OK)
async def generate_videos_batch(
    payload: BatchVideoGenerationRequest | list[VideoGenerationRequest],
):
    """Submit batch video generation tasks (returns job_ids for polling)."""
    items = payload.requests if isinstance(payload, BatchVideoGenerationRequest) else payload
    if not items:
        raise HTTPException(status_code=422, detail="Requests list cannot be empty")

    job_ids: list[str] = []
    for item in items:
        resp = await generate_video(item)
        if resp.jobs:
            job_ids.append(resp.jobs[0].id)

    return await _resolve_jobs_response(job_ids)


@router.post("/v1/images/upscale")
@router.post("/v1/images/export")
async def upscale_image(
    payload: ImageUpscaleRequest,
    accept: str | None = Header(default=None, alias="Accept"),
):
    """Synchronously upscale an image to 2K/4K resolution via Flow's native RPC SPrCad.

    Accepts an existing media_id, image_base64, or image_url (auto-uploaded to Flow).
    Returns JSON with encodedImage base64 data, or raw binary JPEG if download=True or Accept: image/jpeg.
    """
    client = get_flow_client()
    _, route = await _resolve_v1_route(client, {
        "installation_id": payload.installation_id,
        "project_id": payload.project_id or "",
        "base_media_id": payload.media_id,
    })
    media_id = payload.media_id
    route_inst_id = route.installation_id
    route_project_id = route.project_id

    if not media_id and (payload.image_base64 or payload.image_url):
        media_id, _ = await _resolve_media_id_from_input(
            client,
            image_base64=payload.image_base64,
            image_url=payload.image_url,
            project_id=route_project_id,
            preferred_installation=route_inst_id,
        )

    result = await client.upscale_image(
        media_id=media_id,
        project_id=route_project_id,
        resolution=payload.quality,
        preferred_installation=route_inst_id,
    )

    if result.get("error"):
        code = result.get("status") if isinstance(result.get("status"), int) and result.get("status") >= 400 else None
        raise_v1_http_error(result.get("error"), status_code=code, default_message="Phóng to ảnh (upscale) thất bại")

    data = result.get("data", {})
    encoded = data.get("encodedImage", "")

    wants_binary = payload.download or (accept and "image/" in accept)
    if wants_binary and encoded:
        try:
            image_bytes = base64.b64decode(encoded)
            return Response(
                content=image_bytes,
                media_type="image/jpeg",
                headers={
                    "Content-Disposition": f'attachment; filename="flow_{payload.quality.lower()}_{media_id[:8]}.jpg"',
                    "X-Flow-Image-Quality": payload.quality,
                },
            )
        except Exception:
            pass

    return JSONResponse(status_code=200, content=result)


@router.post("/v1/images/edits", response_model=JobsResponse, status_code=status.HTTP_200_OK)
async def edit_image(
    payload: ImageEditRequest,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=200),
):
    """Edit/modify images directly with Banana (synchronous 200 OK)."""
    client = get_flow_client()
    _ensure_v1_routing_ready(client)

    header_val = idempotency_header if isinstance(idempotency_header, str) else None
    idempotency_key = _resolve_idempotency_key(header_val, payload.idempotency_key)
    db = await get_db()
    if idempotency_key:
        async with _db_lock:
            existing_id = await _find_idempotent_job(db, idempotency_key, "EDIT_IMAGE")
            if existing_id:
                return await _resolve_jobs_response([existing_id])

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_image_aspect(payload.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"
    model = normalize_image_model(payload.model or "NANO_BANANA_PRO")
    _, route = await _resolve_v1_route(client, {
        "installation_id": payload.installation_id,
        "project_id": payload.project_id or "",
        "base_media_id": payload.base_media_id,
    })
    inst_id = route.installation_id
    pid = route.project_id

    base_mid, inst_id = await _resolve_media_id_from_input(
        client,
        media_id=payload.base_media_id,
        image_base64=payload.base_image_base64,
        image_url=payload.base_image_url,
        project_id=pid,
        preferred_installation=inst_id,
    )

    ref_mids = []
    if payload.input_images:
        for img in payload.input_images:
            mid, inst_id = await _resolve_media_id_from_input(
                client,
                media_id=img.media_id,
                image_base64=img.image_base64,
                image_url=img.image_url,
                mime_type=img.mime_type,
                project_id=pid,
                preferred_installation=inst_id,
            )
            if mid:
                ref_mids.append(mid)

    payload_dict = {
        "prompt": payload.prompt,
        "idempotency_key": idempotency_key,
        "installation_id": inst_id,
        "aspect_ratio": aspect,
        "base_media_id": base_mid,
        "model": model,
        "count": payload.count,
        "seed": payload.seed,
        "project_id": pid,
        "generated_media": [],
    }

    res = await client.edit_image(
        prompt=payload.prompt,
        source_media_id=base_mid,
        project_id=pid,
        aspect_ratio=aspect,
        character_media_ids=ref_mids if ref_mids else None,
        image_model=model,
        preferred_installation=inst_id,
        count=payload.count,
        seed=payload.seed,
    )

    if res.get("error") or (isinstance(res.get("status"), int) and res["status"] >= 400):
        code = res.get("status") if isinstance(res.get("status"), int) and res.get("status") >= 400 else None
        raise_v1_http_error(res.get("error") or "Image edit failed", status_code=code, default_message="Chỉnh sửa ảnh thất bại")

    media_items = _extract_media_items(res, "EDIT_IMAGE")
    if not media_items:
        data = res.get("data", res)
        if isinstance(data, dict) and data.get("url"):
            media_items = [{"media_id": data.get("media_id") or base_mid, "url": data["url"]}]
        else:
            media_items = [{"media_id": base_mid, "url": None}]

    primary_media = media_items[0]
    target_inst = res.get("_installation_id") or inst_id
    payload_dict["installation_id"] = target_inst
    payload_dict["generated_media"] = media_items

    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, type, orientation, status, media_id, output_url, installation_id, payload_json, created_at, updated_at)
            VALUES (?, 'EDIT_IMAGE', ?, 'COMPLETED', ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, orientation, primary_media.get("media_id"), primary_media.get("url"), target_inst, json.dumps(payload_dict)),
        )
        await db.commit()

    logger.info("v1 Client API direct Image Edit Job %s completed", job_id)
    return await _resolve_jobs_response([job_id])


@router.post("/v1/jobs/status", response_model=JobsResponse)
async def get_job_status(payload: JobStatusRequest):
    """Query job status by job_ids (POST matching FlowProviderAPI contract)."""
    ids = payload.job_ids or ([payload.job_id] if payload.job_id else [])
    if not ids:
        raise HTTPException(status_code=422, detail="Either job_ids or job_id must be provided")
    return await _resolve_jobs_response(ids)


@router.get("/v1/jobs/{job_id}", response_model=JobPollResponse)
async def get_job_by_id(job_id: str):
    """Query job status by job_id (GET) — minimal polling response."""
    return await _resolve_single_job_response(job_id)


@router.get("/v1/jobs/{job_id}/executions")
async def get_job_executions(job_id: str):
    from agent.services.execution_audit import list_calls
    if not await crud.get_request(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "executions": await list_calls(job_id)}


@router.get("/v1/jobs/status/{job_id}", response_model=JobPollResponse)
async def get_job_status_by_id(job_id: str):
    """Query job status by job_id (GET /v1/jobs/status/{job_id}) — minimal polling response."""
    return await _resolve_single_job_response(job_id)
