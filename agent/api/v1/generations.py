from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse

from agent.api.v1.schemas import (
    BatchImageGenerationRequest,
    BatchVideoGenerationRequest,
    GeneratedMedia,
    ImageGenerationRequest,
    Job,
    JobError,
    JobMetadata,
    JobsResponse,
    JobStatusRequest,
    VideoGenerationRequest,
)
from agent.db import crud
from agent.db.schema import get_db, _db_lock
from agent.services.flow_client import get_flow_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Client API v1"])


def _wake_worker() -> None:
    """Start queued v1 work immediately when the worker has capacity."""
    from agent.worker.processor import get_worker_controller
    get_worker_controller().notify_work_available()


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
    if req.get("output_url"):
        media_items.append(
            GeneratedMedia(
                id=req.get("media_id") or jid,
                type=job_type,
                url=req["output_url"],
                media_id=req.get("media_id"),
            )
        )

    job_error: JobError | None = None
    if raw_status == "FAILED" and req.get("error_message"):
        job_error = JobError(
            code="GENERATION_FAILED",
            message=req["error_message"],
            details=req.get("error_message"),
        )

    return Job(
        id=jid,
        operation_id=req.get("request_id"),
        project_id=project_id,
        routing_scope=None,
        provider="google_flow",
        type=job_type,
        generation_type=generation_type,
        status=job_status,
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



@router.post("/v1/images/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_image(payload: ImageGenerationRequest):
    """Submit image generation task (accepts text prompt and optional Base64 reference images)."""
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_image_aspect(payload.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    payload_dict = {
        "prompt": payload.prompt,
        "installation_id": payload.installation_id,
        "aspect_ratio": aspect,
        "input_images": [img.model_dump() for img in (payload.input_images or [])],
        "model": payload.model,
        "count": payload.count,
        "project_id": payload.project_id,
        "reference_media_ids": payload.reference_media_ids,
    }

    db = await get_db()
    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, type, orientation, status, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, "GENERATE_IMAGE", orientation, json.dumps(payload_dict)),
        )
        await db.commit()

    _wake_worker()
    logger.info("v1 Client API queued Image Job %s (orientation=%s)", job_id, orientation)
    return await _resolve_jobs_response([job_id])


@router.post("/v1/images/generations/batch", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_images_batch(payload: BatchImageGenerationRequest | list[ImageGenerationRequest]):
    """Submit batch image generation tasks atomically. Accepts BatchImageGenerationRequest or list[ImageGenerationRequest]."""
    items = payload.requests if isinstance(payload, BatchImageGenerationRequest) else payload
    if not items:
        raise HTTPException(status_code=422, detail="Requests list cannot be empty")

    job_ids: list[str] = []
    records = []
    for item in items:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        aspect = _normalize_image_aspect(item.aspect_ratio)
        orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"
        payload_dict = {
            "prompt": item.prompt,
            "installation_id": item.installation_id,
            "aspect_ratio": aspect,
            "input_images": [img.model_dump() for img in (item.input_images or [])],
            "model": item.model,
            "count": item.count,
            "project_id": item.project_id,
            "reference_media_ids": item.reference_media_ids,
        }
        records.append((job_id, "GENERATE_IMAGE", orientation, json.dumps(payload_dict)))
        job_ids.append(job_id)

    db = await get_db()
    async with _db_lock:
        for rec in records:
            await db.execute(
                """
                INSERT INTO request (id, type, orientation, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, 'PENDING', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                """,
                rec,
            )
        await db.commit()

    _wake_worker()
    logger.info("v1 Client API queued %d Image Jobs in batch", len(job_ids))
    return await _resolve_jobs_response(job_ids)


@router.post("/v1/videos/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_video(payload: VideoGenerationRequest):
    """Submit video generation task (accepts Base64 input_images for i2v or r2v)."""
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_video_aspect(payload.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    is_ref_based = payload.type in ("reference_to_video", "ingredients", "references", "omni", "r2v")
    req_type = "GENERATE_VIDEO_REFS" if is_ref_based else "GENERATE_VIDEO"

    payload_dict = {
        "prompt": payload.prompt,
        "installation_id": payload.installation_id,
        "type": payload.type,
        "input_images": [img.model_dump() for img in payload.input_images],
        "aspect_ratio": aspect,
        "duration_seconds": payload.duration_seconds,
        "project_id": payload.project_id,
        "start_media_id": payload.start_media_id,
        "end_media_id": payload.end_media_id,
        "reference_media_ids": payload.reference_media_ids,
        "model": payload.model or payload.mode or payload.model_family or payload.quality or "omni_flash",
        "quality": payload.quality,
        "dialogue": payload.dialogue,
    }

    db = await get_db()
    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, type, orientation, status, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, req_type, orientation, json.dumps(payload_dict)),
        )
        await db.commit()

    _wake_worker()
    logger.info("v1 Client API queued Video Job %s (type=%s, orientation=%s)", job_id, payload.type, orientation)
    return await _resolve_jobs_response([job_id])


@router.post("/v1/videos/generations/batch", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_videos_batch(payload: BatchVideoGenerationRequest | list[VideoGenerationRequest]):
    """Submit batch video generation tasks atomically. Accepts BatchVideoGenerationRequest or list[VideoGenerationRequest]."""
    items = payload.requests if isinstance(payload, BatchVideoGenerationRequest) else payload
    if not items:
        raise HTTPException(status_code=422, detail="Requests list cannot be empty")

    job_ids: list[str] = []
    records = []
    for item in items:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        aspect = _normalize_video_aspect(item.aspect_ratio)
        orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"
        is_ref_based = item.type in ("reference_to_video", "ingredients", "references", "omni", "r2v")
        req_type = "GENERATE_VIDEO_REFS" if is_ref_based else "GENERATE_VIDEO"

        payload_dict = {
            "prompt": item.prompt,
            "installation_id": item.installation_id,
            "type": item.type,
            "input_images": [img.model_dump() for img in item.input_images],
            "aspect_ratio": aspect,
            "duration_seconds": item.duration_seconds,
            "project_id": item.project_id,
            "start_media_id": item.start_media_id,
            "end_media_id": item.end_media_id,
            "reference_media_ids": item.reference_media_ids,
            "model": item.model or item.mode or item.model_family or item.quality or "omni_flash",
            "quality": item.quality,
            "dialogue": item.dialogue,
        }
        records.append((job_id, req_type, orientation, json.dumps(payload_dict)))
        job_ids.append(job_id)

    db = await get_db()
    async with _db_lock:
        for rec in records:
            await db.execute(
                """
                INSERT INTO request (id, type, orientation, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, 'PENDING', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                """,
                rec,
            )
        await db.commit()

    _wake_worker()
    logger.info("v1 Client API queued %d Video Jobs in batch", len(job_ids))
    return await _resolve_jobs_response(job_ids)


@router.post("/v1/jobs/status", response_model=JobsResponse)
async def get_job_status(payload: JobStatusRequest):
    """Query job status by job_ids (POST matching FlowProviderAPI contract)."""
    ids = payload.job_ids or ([payload.job_id] if payload.job_id else [])
    if not ids:
        raise HTTPException(status_code=422, detail="Either job_ids or job_id must be provided")
    return await _resolve_jobs_response(ids)


@router.get("/v1/jobs/{job_id}", response_model=JobsResponse)
async def get_job_by_id(job_id: str):
    """Query job status by job_id (GET)."""
    return await _resolve_jobs_response([job_id])


@router.get("/v1/jobs/{job_id}/executions")
async def get_job_executions(job_id: str):
    from agent.services.execution_audit import list_calls
    if not await crud.get_request(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "executions": await list_calls(job_id)}


@router.get("/v1/jobs/status/{job_id}", response_model=JobsResponse)
async def get_job_status_by_id(job_id: str):
    """Query job status by job_id (GET /v1/jobs/status/{job_id})."""
    return await _resolve_jobs_response([job_id])
