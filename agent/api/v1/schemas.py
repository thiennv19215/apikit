from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class InlineImageInput(BaseModel):
    image_base64: str | None = None
    media_id: str | None = None
    mime_type: str = "image/jpeg"
    file_name: str = "reference.png"


class ImageUploadRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    file_name: str | None = "upload.png"
    project_id: str | None = None
    installation_id: str | None = None
    required_credits: int = 0
    excluded_project_ids: list[str] = Field(default_factory=list)


class ImageUploadResponse(BaseModel):
    media_id: str
    file_name: str | None = None
    media: dict[str, Any] | None = None


class ImageGenerationRequest(BaseModel):
    prompt: str
    input_images: list[InlineImageInput] | None = None
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    model: str | None = "pro"
    count: int = 1
    variant_count: int = 1
    quality: str | None = None
    project_id: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_counts(self):
        if self.variant_count > 1 and self.count == 1:
            self.count = self.variant_count
        elif self.count > 1 and self.variant_count == 1:
            self.variant_count = self.count
        return self


class VideoGenerationRequest(BaseModel):
    prompt: str
    type: str = "image_to_video"
    input_images: list[InlineImageInput] = Field(default_factory=list)
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    duration_seconds: int = 8
    model: str | None = "omni_flash"
    quality: str | None = None
    mode: str | None = None
    model_family: str | None = None
    project_id: str | None = None
    start_media_id: str | None = None
    end_media_id: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    dialogue: bool = False


class JobStatusRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    job_id: str | None = None
    operation_names: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_ids(self):
        if self.job_id and self.job_id not in self.job_ids:
            self.job_ids.append(self.job_id)
        return self


class GeneratedMedia(BaseModel):
    id: str | None = None
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
    retryable: bool = False
    outcome_unknown: bool = False
    upstream_code: str | None = None
    upstream_status: str | None = None


class Job(BaseModel):
    id: str
    project_id: str | None = None
    routing_scope: str | None = None
    provider: str = "google_flow"
    type: Literal["image", "video"] | str = "image"
    generation_type: str = "image"
    status: Literal["queued", "running", "complete", "failed"] | str = "queued"
    media: list[GeneratedMedia] = Field(default_factory=list)
    error: JobError | None = None
    installation_id: str | None = None


class JobMetadata(BaseModel):
    request_id: str | None = None
    project_id: str | None = None
    routing_scope: str | None = None
    poll_after_seconds: int | None = 10
    counts: dict[str, int] = Field(default_factory=dict)
    done: bool = False


class JobsResponse(BaseModel):
    jobs: list[Job] = Field(default_factory=list)
    metadata: JobMetadata = Field(default_factory=JobMetadata)

    # Convenience and backward-compatibility fields:
    job_id: str | None = None
    type: str | None = None
    generation_type: str | None = None
    status: str | None = None
    media: list[GeneratedMedia] = Field(default_factory=list)
    error: JobError | None = None
    installation_id: str | None = None


# ─── Character Schemas ────────────────────────────────────────

class CharacterCreateRequest(BaseModel):
    name: str
    description: str | None = ""
    image_prompt: str | None = ""
    voice_description: str | None = None
    entity_type: str | None = "character"
    image_model: str | None = "pro"
    aspect_ratio: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    input_images: list[InlineImageInput] = Field(default_factory=list)


class CharacterUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    image_prompt: str | None = None
    voice_description: str | None = None
    entity_type: str | None = None
    image_model: str | None = None
    aspect_ratio: str | None = None
    reference_media_ids: list[str] | None = None
    input_images: list[InlineImageInput] | None = None


class CharacterResponse(BaseModel):
    id: str
    name: str
    entity_type: str = "character"
    description: str | None = ""
    image_prompt: str | None = ""
    voice_description: str | None = None
    image_model: str | None = "pro"
    aspect_ratio: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    media_id: str | None = None
    reference_image_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CharacterImageGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "16:9"
    model: str | None = None
    variant_count: int = 1
    input_images: list[InlineImageInput] = Field(default_factory=list)
    reference_media_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None


class CharacterVideoGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "9:16"
    duration_seconds: int = 8
    dialogue: bool = False
    project_id: str | None = None
