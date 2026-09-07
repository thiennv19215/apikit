from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status

from agent.api.v1.schemas import (
    GeneratedMedia,
    ImageGenerationRequest,
    ImageUploadRequest,
    ImageUploadResponse,
    JobError,
    JobsResponse,
    JobStatusRequest,
    VideoGenerationRequest,
)
from agent.db import crud
from agent.db.schema import get_db, _db_lock
from agent.services.flow_client import get_flow_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Client API v1"])


def _normalize_image_aspect(aspect: str) -> str:
    mapping = {
        "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
        "3:4": "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE",
        "4:3": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
        "IMAGE_ASPECT_RATIO_LANDSCAPE": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "IMAGE_ASPECT_RATIO_PORTRAIT": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "IMAGE_ASPECT_RATIO_SQUARE": "IMAGE_ASPECT_RATIO_SQUARE",
        "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE": "IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE",
        "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    }
    return mapping.get(str(aspect).strip(), "IMAGE_ASPECT_RATIO_LANDSCAPE")


def _normalize_video_aspect(aspect: str) -> str:
    mapping = {
        "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
        "VIDEO_ASPECT_RATIO_LANDSCAPE": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        "VIDEO_ASPECT_RATIO_PORTRAIT": "VIDEO_ASPECT_RATIO_PORTRAIT",
    }
    return mapping.get(aspect, "VIDEO_ASPECT_RATIO_PORTRAIT")


@router.post("/v1/media", response_model=ImageUploadResponse)
async def upload_image(payload: ImageUploadRequest):
    """Upload Base64 image into Google Flow via connected extension."""
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(status_code=503, detail="No browser extension connected")

    result = await client.upload_image(
        image_base64=payload.image_base64,
        mime_type=payload.mime_type,
        file_name=payload.file_name or "image.jpg",
    )
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])

    media_id = result.get("_mediaId") or result.get("data", {}).get("media", {}).get("name")
    if not media_id:
        raise HTTPException(status_code=502, detail="Failed to retrieve uploaded media ID")

    return ImageUploadResponse(media_id=media_id, file_name=payload.file_name)


@router.post("/v1/images/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_image(payload: ImageGenerationRequest):
    """Submit image generation task (accepts text prompt and optional Base64 reference images)."""
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_image_aspect(payload.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    payload_dict = {
        "prompt": payload.prompt,
        "aspect_ratio": aspect,
        "input_images": [img.model_dump() for img in (payload.input_images or [])],
        "model": payload.model,
        "count": payload.count,
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

    logger.info("v1 Client API queued Image Job %s (orientation=%s)", job_id, orientation)
    return JobsResponse(
        job_id=job_id,
        type="image",
        generation_type="image",
        status="queued",
    )


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
        "type": payload.type,
        "input_images": [img.model_dump() for img in payload.input_images],
        "aspect_ratio": aspect,
        "duration_seconds": payload.duration_seconds,
        "start_media_id": payload.start_media_id,
        "end_media_id": payload.end_media_id,
        "reference_media_ids": payload.reference_media_ids,
        "model": payload.model or payload.quality,
        "quality": payload.quality,
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

    logger.info("v1 Client API queued Video Job %s (type=%s, orientation=%s)", job_id, payload.type, orientation)
    return JobsResponse(
        job_id=job_id,
        type="video",
        generation_type=payload.type,
        status="queued",
    )


async def _resolve_job_response(job_id: str) -> JobsResponse:
    req = await crud.get_request(job_id)
    if not req:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

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

    # Extract generation_type from payload if available
    generation_type = job_type
    if req.get("payload_json"):
        try:
            pj = json.loads(req["payload_json"])
            if pj.get("type"):
                generation_type = pj["type"]
        except Exception:
            pass

    media_items: list[GeneratedMedia] = []
    if req.get("output_url"):
        media_items.append(
            GeneratedMedia(
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
        )

    return JobsResponse(
        job_id=job_id,
        type=job_type,
        generation_type=generation_type,
        status=job_status,
        media=media_items,
        error=job_error,
        installation_id=req.get("installation_id"),
    )


@router.post("/v1/jobs/status", response_model=JobsResponse)
async def get_job_status(payload: JobStatusRequest):
    """Query job status by job_id (POST)."""
    return await _resolve_job_response(payload.job_id)


@router.get("/v1/jobs/{job_id}", response_model=JobsResponse)
async def get_job_by_id(job_id: str):
    """Query job status by job_id (GET)."""
    return await _resolve_job_response(job_id)


@router.get("/v1/jobs/status/{job_id}", response_model=JobsResponse)
async def get_job_status_by_id(job_id: str):
    """Query job status by job_id (GET /v1/jobs/status/{job_id})."""
    return await _resolve_job_response(job_id)
