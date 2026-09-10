from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from agent.api.v1.generations import _resolve_jobs_response, _normalize_image_aspect, _normalize_video_aspect
from agent.api.v1.schemas import (
    CharacterCreateRequest,
    CharacterImageGenerationRequest,
    CharacterResponse,
    CharacterUpdateRequest,
    CharacterVideoGenerationRequest,
    JobsResponse,
    normalize_image_model,
)
from agent.db import crud
from agent.db.schema import get_db, _db_lock
from agent.services.flow_client import get_flow_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/characters", tags=["Client API v1 Characters"])


def _as_character_response(row: dict) -> CharacterResponse:
    mid = row.get("media_id")
    ref_ids = [mid] if mid else []
    return CharacterResponse(
        id=row["id"],
        name=row["name"],
        entity_type=row.get("entity_type") or "character",
        description=row.get("description") or "",
        image_prompt=row.get("image_prompt") or "",
        voice_description=row.get("voice_description"),
        image_model=normalize_image_model(row.get("image_model") or "NANO_BANANA_PRO"),
        aspect_ratio=row.get("aspect_ratio"),
        reference_media_ids=ref_ids,
        media_id=mid,
        reference_image_url=row.get("reference_image_url"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.post("", response_model=CharacterResponse, status_code=status.HTTP_201_CREATED)
async def create_character(body: CharacterCreateRequest):
    """Create character entity (uploads Base64 reference image if provided)."""
    client = get_flow_client()
    media_id = None
    ref_url = None

    if body.input_images:
        first_img = body.input_images[0]
        if client.connected and first_img.image_base64:
            res = await client.upload_image(
                image_base64=first_img.image_base64,
                mime_type=first_img.mime_type,
            )
            if not res.get("error"):
                media_id = res.get("_mediaId") or res.get("data", {}).get("media", {}).get("name")

    if not media_id and body.reference_media_ids:
        media_id = body.reference_media_ids[0]

    raw_type = (body.entity_type or "character").lower()
    valid_types = {'character', 'location', 'creature', 'visual_asset', 'generic_troop', 'faction'}
    entity_type = raw_type if raw_type in valid_types else "character"

    char_data = await crud.create_character(
        name=body.name,
        description=body.description or "",
        image_prompt=body.image_prompt or "",
        voice_description=body.voice_description,
        entity_type=entity_type,
        media_id=media_id,
        reference_image_url=ref_url,
    )
    return _as_character_response(char_data)


@router.get("", response_model=list[CharacterResponse])
async def list_characters():
    """List all character entities."""
    chars = await crud.list_characters()
    return [_as_character_response(c) for c in chars]


@router.get("/{character_id}", response_model=CharacterResponse)
async def get_character(character_id: str):
    """Get character details by ID."""
    char = await crud.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    return _as_character_response(char)


@router.patch("/{character_id}", response_model=CharacterResponse)
async def update_character(character_id: str, body: CharacterUpdateRequest):
    """Update character details."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        char = await crud.get_character(character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        return _as_character_response(char)

    if updates.get("reference_media_ids"):
        updates["media_id"] = updates["reference_media_ids"][0]
    updates.pop("reference_media_ids", None)
    updates.pop("input_images", None)

    updated = await crud.update_character(character_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Character not found")
    return _as_character_response(updated)


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(character_id: str):
    """Delete a character."""
    deleted = await crud.delete_character(character_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Character not found")


@router.get("/{character_id}/reference-images/{index}")
async def get_reference_image(character_id: str, index: int):
    """Retrieve character reference image by index (FlowProviderAPI contract)."""
    char = await crud.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    ref_url = char.get("reference_image_url")
    if index != 0 or not ref_url:
        raise HTTPException(status_code=404, detail="Reference image not found")
    return RedirectResponse(url=ref_url, status_code=307)


@router.post("/{character_id}/images/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_character_image(character_id: str, body: CharacterImageGenerationRequest):
    """Generate image of a character."""
    char = await crud.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_image_aspect(body.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    ref_media_ids = list(body.reference_media_ids) if body.reference_media_ids else []
    if not ref_media_ids and char.get("media_id"):
        ref_media_ids = [char["media_id"]]

    payload_dict = {
        "prompt": body.prompt,
        "aspect_ratio": aspect,
        "character_id": character_id,
        "character_media_ids": ref_media_ids,
        "model": body.model,
        "input_images": [img.model_dump() for img in (body.input_images or [])],
        "project_id": body.project_id,
    }

    db = await get_db()
    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, character_id, type, orientation, status, payload_json, created_at, updated_at)
            VALUES (?, ?, 'GENERATE_CHARACTER_IMAGE', ?, 'PENDING', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, character_id, orientation, json.dumps(payload_dict)),
        )
        await db.commit()

    from agent.worker.processor import get_worker_controller
    get_worker_controller().notify_work_available()
    return await _resolve_jobs_response([job_id])


@router.post("/{character_id}/images", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
async def generate_character_image_alias(character_id: str, body: CharacterImageGenerationRequest):
    return await generate_character_image(character_id, body)


@router.post("/{character_id}/videos/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_character_video(character_id: str, body: CharacterVideoGenerationRequest):
    """Generate video of a character."""
    char = await crud.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = _normalize_video_aspect(body.aspect_ratio)
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    payload_dict = {
        "prompt": body.prompt,
        "type": "character_video",
        "aspect_ratio": aspect,
        "duration_seconds": body.duration_seconds,
        "character_id": character_id,
        "reference_media_ids": [char["media_id"]] if char.get("media_id") else [],
        "dialogue": body.dialogue,
        "project_id": body.project_id,
    }

    db = await get_db()
    async with _db_lock:
        await db.execute(
            """
            INSERT INTO request (id, character_id, type, orientation, status, payload_json, created_at, updated_at)
            VALUES (?, ?, 'GENERATE_VIDEO_REFS', ?, 'PENDING', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (job_id, character_id, orientation, json.dumps(payload_dict)),
        )
        await db.commit()

    from agent.worker.processor import get_worker_controller
    get_worker_controller().notify_work_available()
    return await _resolve_jobs_response([job_id])


@router.post("/{character_id}/videos", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
async def generate_character_video_alias(character_id: str, body: CharacterVideoGenerationRequest):
    return await generate_character_video(character_id, body)
