from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


def normalize_image_model(value: str | None) -> str:
    norm = (value or "").strip().lower().replace("-", "_").replace(" ", "")
    if norm in ("pro", "bananapro", "gem_pix_2", "gem_pix", "nano_banana_pro", ""):
        return "NANO_BANANA_PRO"
    if norm in ("banana2", "banana_2", "narwhal", "nano_banana_2"):
        return "NANO_BANANA_2"
    return value or "NANO_BANANA_PRO"


class ImageModelContract(BaseModel):
    model: str | None = None
    image_model: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reconcile_models(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        model = data.get("model")
        alias = data.get("image_model")
        if model is not None and alias is not None:
            if normalize_image_model(model) != normalize_image_model(alias):
                raise ValueError(f"Conflicting model definitions: model='{model}', image_model='{alias}'")
            data["model"] = normalize_image_model(model)
        elif model is not None or alias is not None:
            data["model"] = normalize_image_model(model if model is not None else alias)
        return data


class InlineImageInput(BaseModel):
    image_base64: str | None = None
    media_id: str | None = None
    mime_type: str = "image/jpeg"
    file_name: str = "reference.png"
    role: Literal["start_frame", "end_frame", "reference"] | None = None


class ImageUploadRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    file_name: str | None = "upload.png"
    project_id: str | None = None
    installation_id: str | None = None
    required_credits: int = Field(default=0, description="Compatibility-only; quota prediction is not implemented.")
    excluded_project_ids: list[str] = Field(default_factory=list, description="Compatibility-only; not applied to routing.")


class ImageUploadResponse(BaseModel):
    media_id: str
    installation_id: str | None = None
    file_name: str | None = None
    media: dict[str, Any] | None = None


class ImageGenerationRequest(ImageModelContract):
    prompt: str
    installation_id: str | None = None
    input_images: list[InlineImageInput] | None = None
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    model: str | None = Field(default="NANO_BANANA_PRO", description="FlowKit model: NANO_BANANA_PRO or NANO_BANANA_2. Legacy aliases accepted.")
    image_model: str | None = Field(default=None, description="Alias for model; conflicting values return 422.")
    count: int = Field(default=1, description="Compatibility-only; worker does not implement multiple variants. Use 1.")
    variant_count: int = Field(default=1, description="Legacy count alias; multiple variants are not implemented.")
    quality: str | None = Field(default=None, description="Compatibility-only for images; not forwarded to provider.")
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
    installation_id: str | None = None
    type: str = "image_to_video"
    generation_type: str | None = Field(default=None, description="Preferred name for type; conflicting values are rejected.")
    input_images: list[InlineImageInput] = Field(default_factory=list)
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    duration_seconds: Literal[4, 6, 8, 10] = 8
    model: Literal["omni_flash"] | None = "omni_flash"
    quality: str | None = None
    mode: str | None = None
    model_family: str | None = None
    project_id: str | None = None
    start_media_id: str | None = None
    end_media_id: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    dialogue: bool = Field(default=False, description="Compatibility-only; not an audio toggle. Describe speech in prompt.")

    @model_validator(mode="before")
    @classmethod
    def normalize_contract(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        aliases = {"i2v": "image_to_video", "r2v": "reference_to_video",
                   "ingredients": "reference_to_video", "references": "reference_to_video",
                   "omni": "reference_to_video"}
        def canonical(value):
            if value is not None and not isinstance(value, str):
                raise ValueError("generation_type and type must be strings")
            return aliases.get(value, value)
        preferred = data.get("generation_type")
        legacy = data.get("type")
        if preferred is not None and legacy is not None and canonical(preferred) != canonical(legacy):
            raise ValueError("generation_type and type must agree")
        has_refs = bool(data.get("reference_media_ids")) or any(
            isinstance(img, dict) and img.get("role") == "reference"
            for img in data.get("input_images", [])
        )
        default_type = "reference_to_video" if has_refs else "image_to_video"
        value = canonical(preferred if preferred is not None else legacy or default_type)
        data["type"] = data["generation_type"] = value
        requested_model = data.get("model") or data.get("mode") or data.get("model_family") or "omni_flash"
        if isinstance(requested_model, str) and requested_model.strip().lower() in {"omni", "flash", "lite", "omni_flash"}:
            data["model"] = "omni_flash"
        else:
            raise ValueError("Client video supports only model=omni_flash")
        if value not in ("image_to_video", "reference_to_video"):
            raise ValueError("Use image_to_video (first or first+last) or reference_to_video")
        return data


class JobStatusRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    job_id: str | None = None
    operation_names: list[str] = Field(default_factory=list, description="Compatibility-only; polling requires job_id or job_ids.")

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
    image_model: str | None = Field(default="NANO_BANANA_PRO", description="Compatibility-only; not persisted. Set model on generation.")
    aspect_ratio: str | None = Field(default=None, description="Compatibility-only; not persisted. Set aspect_ratio on generation.")
    reference_media_ids: list[str] = Field(default_factory=list)
    input_images: list[InlineImageInput] = Field(default_factory=list)


class CharacterUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    image_prompt: str | None = None
    voice_description: str | None = None
    entity_type: str | None = None
    image_model: str | None = Field(default=None, description="Compatibility-only; not persisted.")
    aspect_ratio: str | None = Field(default=None, description="Compatibility-only; not persisted.")
    reference_media_ids: list[str] | None = None
    input_images: list[InlineImageInput] | None = Field(default=None, description="Compatibility-only on PATCH; use reference_media_ids to replace the reference.")


class CharacterResponse(BaseModel):
    id: str
    name: str
    entity_type: str = "character"
    description: str | None = ""
    image_prompt: str | None = ""
    voice_description: str | None = None
    image_model: str | None = "NANO_BANANA_PRO"
    aspect_ratio: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    media_id: str | None = None
    reference_image_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CharacterImageGenerationRequest(ImageModelContract):
    prompt: str
    aspect_ratio: str = "16:9"
    model: str | None = None
    image_model: str | None = Field(default=None, description="Alias for model: NANO_BANANA_PRO or NANO_BANANA_2.")
    variant_count: int = Field(default=1, description="Compatibility-only; multiple variants are not implemented.")
    input_images: list[InlineImageInput] = Field(default_factory=list)
    reference_media_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None


class CharacterVideoGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "9:16"
    duration_seconds: Literal[4, 6, 8, 10] = 8
    dialogue: bool = Field(default=False, description="Compatibility-only; describe speech in prompt.")
    project_id: str | None = None
