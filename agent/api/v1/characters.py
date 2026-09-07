from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from agent.api.v1.schemas import (
    CharacterCreateRequest,
    CharacterImageGenerationRequest,
    CharacterResponse,
    CharacterUpdateRequest,
    CharacterVideoGenerationRequest,
    JobsResponse,
)
from agent.db import crud
from agent.db.schema import get_db, _db_lock
from agent.services.flow_client import get_flow_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/characters", tags=["Client API v1 Characters"])


def _as_character_response(row: dict) -> CharacterResponse:
    return CharacterResponse(
        id=row["id"],
        name=row["name"],
        description=row.get("description") or "",
        image_prompt=row.get("image_prompt") or "",
        media_id=row.get("media_id"),
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

    char_data = await crud.create_character(
        name=body.name,
        description=body.description or "",
        image_prompt=body.image_prompt or "",
        entity_type=body.entity_type or "PERSON",
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


@router.post("/{character_id}/images/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_character_image(character_id: str, body: CharacterImageGenerationRequest):
    """Generate image of a character."""
    char = await crud.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = "IMAGE_ASPECT_RATIO_LANDSCAPE" if "16:9" in body.aspect_ratio else "IMAGE_ASPECT_RATIO_PORTRAIT"
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    payload_dict = {
        "prompt": body.prompt,
        "aspect_ratio": aspect,
        "character_id": character_id,
        "character_media_ids": [char["media_id"]] if char.get("media_id") else [],
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

    return JobsResponse(
        job_id=job_id,
        type="image",
        generation_type="character_image",
        status="queued",
    )


@router.post("/{character_id}/videos/generations", response_model=JobsResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_character_video(character_id: str, body: CharacterVideoGenerationRequest):
    """Generate video of a character."""
    char = await crud.get_character(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    job_id = f"job_{uuid.uuid4().hex[:16]}"
    aspect = "VIDEO_ASPECT_RATIO_LANDSCAPE" if "16:9" in body.aspect_ratio else "VIDEO_ASPECT_RATIO_PORTRAIT"
    orientation = "HORIZONTAL" if "LANDSCAPE" in aspect else "VERTICAL"

    payload_dict = {
        "prompt": body.prompt,
        "type": "character_video",
        "aspect_ratio": aspect,
        "duration_seconds": body.duration_seconds,
        "character_id": character_id,
        "reference_media_ids": [char["media_id"]] if char.get("media_id") else [],
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

    return JobsResponse(
        job_id=job_id,
        type="video",
        generation_type="character_video",
        status="queued",
    )
