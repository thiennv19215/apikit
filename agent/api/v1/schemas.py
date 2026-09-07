from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class InlineImageInput(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"


class ImageUploadRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    file_name: str | None = None


class ImageUploadResponse(BaseModel):
    media_id: str
    file_name: str | None = None


class ImageGenerationRequest(BaseModel):
    prompt: str
    input_images: list[InlineImageInput] | None = None
    aspect_ratio: Literal["16:9", "9:16", "1:1", "IMAGE_ASPECT_RATIO_LANDSCAPE", "IMAGE_ASPECT_RATIO_PORTRAIT", "IMAGE_ASPECT_RATIO_SQUARE"] = "16:9"
    model: str | None = None
    count: int = 1
    quality: str | None = None


class VideoGenerationRequest(BaseModel):
    prompt: str
    type: Literal[
        "image_to_video", "start_to_video", "frames_to_video",
        "reference_to_video", "ingredients", "references", "omni", "r2v", "frames"
    ] | str = "image_to_video"
    input_images: list[InlineImageInput] = Field(default_factory=list)
    aspect_ratio: Literal["16:9", "9:16", "VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"] = "9:16"
    duration_seconds: Literal[4, 6, 8, 10] | int = 8
    quality: str | None = None
    start_media_id: str | None = None
    end_media_id: str | None = None
    reference_media_ids: list[str] | None = None


class JobStatusRequest(BaseModel):
    job_id: str


class GeneratedMedia(BaseModel):
    type: Literal["image", "video"] | str = "image"
    url: str | None = None
    media_id: str | None = None
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None


class JobError(BaseModel):
    code: str
    message: str
    details: Any = None


class JobsResponse(BaseModel):
    job_id: str
    type: Literal["image", "video"] | str = "image"
    generation_type: str = "image"
    status: Literal["queued", "running", "complete", "failed"] | str = "queued"
    media: list[GeneratedMedia] = Field(default_factory=list)
    error: JobError | None = None
    installation_id: str | None = None


# ─── Character Schemas ────────────────────────────────────────

class CharacterCreateRequest(BaseModel):
    name: str
    description: str | None = ""
    image_prompt: str | None = ""
    entity_type: str | None = "PERSON"
    input_images: list[InlineImageInput] = Field(default_factory=list)


class CharacterUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    image_prompt: str | None = None


class CharacterResponse(BaseModel):
    id: str
    name: str
    description: str | None = ""
    image_prompt: str | None = ""
    media_id: str | None = None
    reference_image_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CharacterImageGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "16:9"


class CharacterVideoGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "9:16"
    duration_seconds: int = 8
