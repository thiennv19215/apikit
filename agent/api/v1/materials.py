"""Client API v1 — Materials / Visual Styles endpoints."""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException

from agent.models.material import MaterialResponse
from agent.materials import (
    get_material,
    list_materials as _list_materials,
    _BUILTIN_IDS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/materials", tags=["Client API v1 Materials"])


def _to_response(material: dict) -> MaterialResponse:
    is_builtin = material.get("id") in _BUILTIN_IDS
    return MaterialResponse(
        id=material["id"],
        name=material["name"],
        style_instruction=material.get("style_instruction", ""),
        negative_prompt=material.get("negative_prompt"),
        scene_prefix=material.get("scene_prefix"),
        lighting=material.get("lighting", "Studio lighting, highly detailed"),
        is_builtin=is_builtin,
    )


@router.get("", response_model=list[MaterialResponse])
async def list_materials():
    """List all available visual styles / materials for image generation."""
    return [_to_response(m) for m in _list_materials()]


@router.get("/{material_id}", response_model=MaterialResponse)
async def get_material_detail(material_id: str):
    """Retrieve visual style details by ID."""
    mat = get_material(material_id)
    if not mat:
        raise HTTPException(status_code=404, detail=f"Material '{material_id}' not found")
    return _to_response(mat)
