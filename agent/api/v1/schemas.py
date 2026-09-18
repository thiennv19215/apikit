from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


def normalize_image_model(value: str | None) -> str:
    norm = (value or "").strip().lower().replace("-", "_").replace(" ", "")
    if norm in ("pro", "bananapro", "gem_pix_2", "gem_pix", "nano_banana_pro", ""):
        return "NANO_BANANA_PRO"
    if norm in ("banana2", "banana_2", "narwhal", "nano_banana_2"):
        return "NANO_BANANA_2"
    if norm in ("banana2_lite", "banana_2_lite", "nano_banana_2_lite", "harbor_seal", "harborseal", "lite"):
        return "NANO_BANANA_2_LITE"
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
    image_base64: str | None = Field(default=None, description="Base64-encoded image content.")
    image_url: str | None = Field(default=None, description="Direct URL to image (S3, CDN, web).")
    media_id: str | None = None
    mime_type: str = "image/jpeg"
    file_name: str = "reference.png"
    role: Literal["start_frame", "end_frame", "reference"] | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_content(cls, data):
        if not isinstance(data, dict):
            return data
        if not data.get("image_base64") and not data.get("image_url"):
            raise ValueError("Direct media IDs are not allowed on Client v1. Pass images as image_base64 or image_url.")
        return data


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
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200, description="Client request key used to safely retry without creating a duplicate job.")
    installation_id: str | None = None
    input_images: list[InlineImageInput] | None = None
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    model: str | None = Field(default="NANO_BANANA_PRO", description="FlowKit model: NANO_BANANA_PRO, NANO_BANANA_2, or NANO_BANANA_2_LITE. Legacy aliases accepted.")
    image_model: str | None = Field(default=None, description="Alias for model; conflicting values return 422.")
    count: int = Field(default=1, ge=1, le=4, description="Number of images to generate (1-4). Default 1.")
    variant_count: int = Field(default=1, ge=1, le=4, description="Alias for count (1-4).")
    quality: str | None = Field(default=None, description="Compatibility-only for images; not forwarded to provider.")
    reference_media_ids: list[str] = Field(default_factory=list)
    project_id: str | None = Field(default=None, description="Google Flow project ID")

    @model_validator(mode="before")
    @classmethod
    def reject_raw_media_ids(cls, data):
        if not isinstance(data, dict):
            return data
        if data.get("reference_media_ids"):
            raise ValueError("Direct reference_media_ids are not allowed on Client v1. Pass images as base64 in input_images.")
        return data

    @model_validator(mode="after")
    def sync_counts(self):
        if self.variant_count > 1 and self.count == 1:
            self.count = self.variant_count
        elif self.count > 1 and self.variant_count == 1:
            self.variant_count = self.count
        elif self.count != self.variant_count:
            raise ValueError(f"Conflicting count definitions: count={self.count}, variant_count={self.variant_count}")
        return self


class ImageUpscaleRequest(BaseModel):
    media_id: str | None = Field(default=None, description="Direct Google Flow media_id if already uploaded.")
    image_base64: str | None = Field(default=None, description="Base64-encoded JPEG/PNG image to upscale directly.")
    image_url: str | None = Field(default=None, description="Direct URL of image to upscale.")
    quality: Literal["2K", "4K", "2k", "4k"] = Field(default="2K", description="Upscale resolution: 2K or 4K.")
    resolution: Literal["2K", "4K", "2k", "4k"] | None = Field(default=None, description="Alias for quality.")
    project_id: str | None = None
    installation_id: str | None = None
    download: bool = Field(default=False, description="If true, return raw binary JPEG attachment instead of JSON.")

    @model_validator(mode="before")
    @classmethod
    def validate_inputs(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("media_id") and not data.get("image_base64") and not data.get("image_url"):
            raise ValueError("Must provide either media_id, image_base64, or image_url to upscale.")
        res = data.get("resolution") or data.get("quality") or "2K"
        data["quality"] = str(res).upper()
        return data


class ImageEditRequest(BaseModel):
    prompt: str = Field(..., description="Prompt describing the desired modification or scene.")
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    installation_id: str | None = None
    base_image_base64: str | None = Field(default=None, description="Base64-encoded source image to transform.")
    base_image_url: str | None = Field(default=None, description="URL of source image to transform.")
    base_media_id: str | None = Field(default=None, description="Existing media ID of the base image.")
    input_images: list[InlineImageInput] = Field(default_factory=list, description="Reference images for character/style.")
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    model: str | None = Field(default=None, description="Image model name (NANO_BANANA_PRO, NANO_BANANA_2, NANO_BANANA_2_LITE).")
    image_model: str | None = None
    count: int = Field(default=1, ge=1, le=4, description="Number of variants (1-4).")
    variant_count: int = Field(default=1, ge=1, le=4)
    seed: int | None = None
    project_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_inputs(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("base_image_base64") and not data.get("base_media_id") and not data.get("base_image_url"):
            imgs = data.get("input_images", [])
            if imgs and isinstance(imgs, list) and isinstance(imgs[0], dict):
                if imgs[0].get("image_base64"):
                    data["base_image_base64"] = imgs[0]["image_base64"]
                    data["input_images"] = imgs[1:]
                elif imgs[0].get("image_url"):
                    data["base_image_url"] = imgs[0]["image_url"]
                    data["input_images"] = imgs[1:]
                elif imgs[0].get("media_id"):
                    data["base_media_id"] = imgs[0]["media_id"]
                    data["input_images"] = imgs[1:]
                else:
                    raise ValueError("Image edit requires a base image (base_image_base64, base_image_url, or base_media_id).")
            else:
                raise ValueError("Image edit requires a base image (base_image_base64, base_image_url, or base_media_id).")
        m = data.get("model") or data.get("image_model")
        if m:
            data["model"] = normalize_image_model(m)
        return data

    @model_validator(mode="after")
    def sync_counts(self):
        if self.variant_count > 1 and self.count == 1:
            self.count = self.variant_count
        elif self.count > 1 and self.variant_count == 1:
            self.variant_count = self.count
        return self


class BatchImageGenerationRequest(BaseModel):
    requests: list[ImageGenerationRequest] = Field(..., min_length=1, description="List of image generation tasks.")


class VideoGenerationRequest(BaseModel):
    prompt: str
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200, description="Client request key used to safely retry without creating a duplicate job.")
    installation_id: str | None = None
    type: str = "image_to_video"
    generation_type: str | None = Field(default=None, description="Preferred name for type; conflicting values are rejected.")
    input_images: list[InlineImageInput] = Field(default_factory=list)
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    duration_seconds: Literal[4, 6, 8, 10] = 8
    resolution: Literal["360p", "720p"] = "720p"
    model: Literal["omni_flash"] | None = "omni_flash"
    quality: str | None = None
    mode: str | None = None
    model_family: str | None = None
    project_id: str | None = None
    start_media_id: str | None = None
    end_media_id: str | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    dialogue: bool = Field(default=False, description="Compatibility-only; not an audio toggle. Describe speech in prompt.")
    seed: int | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_contract(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("start_media_id") or data.get("end_media_id") or data.get("reference_media_ids"):
            raise ValueError(
                "Direct media IDs (start_media_id, end_media_id, reference_media_ids) are not allowed on Client v1. "
                "Pass images strictly as base64 in input_images (using image_base64)."
            )
        aliases = {
            "i2v": "image_to_video",
            "frames_to_video": "image_to_video",
            "frame_to_video": "image_to_video",
            "start_end": "image_to_video",
            "first_last": "image_to_video",
            "start_end_frame_2_video": "image_to_video",
            "first_and_last_frames_to_video": "image_to_video",
            "image_to_video": "image_to_video",
            "r2v": "reference_to_video",
            "ingredients": "reference_to_video",
            "references": "reference_to_video",
            "omni": "reference_to_video",
            "reference_to_video": "reference_to_video",
            "t2v": "text_to_video",
            "text": "text_to_video",
            "text_to_video": "text_to_video",
        }

        def canonical(value):
            if value is not None and not isinstance(value, str):
                raise ValueError("generation_type and type must be strings")
            if value is None:
                return None
            return aliases.get(value.strip().lower(), value)

        preferred = data.get("generation_type")
        legacy = data.get("type")
        if preferred is not None and legacy is not None and canonical(preferred) != canonical(legacy):
            raise ValueError("generation_type and type must agree")

        has_refs = bool(data.get("reference_media_ids")) or any(
            isinstance(img, dict) and img.get("role") == "reference"
            for img in data.get("input_images", [])
        )
        has_images = bool(data.get("input_images"))
        if preferred or legacy:
            default_type = canonical(preferred or legacy)
        elif has_refs:
            default_type = "reference_to_video"
        elif has_images:
            default_type = "image_to_video"
        else:
            default_type = "text_to_video"

        canonical_type = canonical(preferred if preferred is not None else legacy or default_type)
        if canonical_type not in ("image_to_video", "reference_to_video", "text_to_video"):
            raise ValueError("Use image_to_video (first or first+last), reference_to_video, or text_to_video")

        data["type"] = canonical_type
        raw_specific = (preferred or legacy or "").strip().lower()
        if raw_specific in ("start_end", "first_last", "start_end_frame_2_video", "first_and_last_frames_to_video"):
            data["generation_type"] = "start_end"
        else:
            data["generation_type"] = canonical_type

        requested_model = data.get("model") or data.get("mode") or data.get("model_family") or "omni_flash"
        if isinstance(requested_model, str) and requested_model.strip().lower() in {"omni", "flash", "lite", "omni_flash"}:
            data["model"] = "omni_flash"
        else:
            raise ValueError("Client video supports only model=omni_flash")
        return data


class BatchVideoGenerationRequest(BaseModel):
    requests: list[VideoGenerationRequest] = Field(..., min_length=1, description="List of video generation tasks.")



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
    # Flow's provider-side handle. It is absent while this job is still in the
    # local queue, and becomes available after the worker has submitted it.
    operation_id: str | None = None
    project_id: str | None = None
    routing_scope: str | None = None
    provider: str = "google_flow"
    type: Literal["image", "video"] | str = "image"
    generation_type: str = "image"
    status: Literal["queued", "running", "complete", "failed"] | str = "queued"
    phase: Literal["queued", "submitting", "polling", "complete", "failed"] | str | None = None
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
    operation_id: str | None = None
    type: str | None = None
    generation_type: str | None = None
    status: str | None = None
    media: list[GeneratedMedia] = Field(default_factory=list)
    error: JobError | None = None
    installation_id: str | None = None


class JobPollResponse(BaseModel):
    job_id: str = Field(..., description="Mã tác vụ duy nhất.")
    id: str | None = Field(default=None, description="Alias cho job_id.")
    status: Literal["queued", "running", "complete", "failed"] | str = Field(..., description="Trạng thái hiện tại: queued, running, complete, failed.")
    type: Literal["image", "video"] | str = Field(default="video", description="Loại tác vụ: video hoặc image.")
    url: str | None = Field(default=None, description="Đường dẫn trực tiếp tới file media khi đã hoàn thành.")
    media: list[GeneratedMedia] = Field(default_factory=list, description="Danh sách media kết quả.")
    error: JobError | str | None = Field(default=None, description="Thông tin lỗi nếu tác vụ thất bại.")
    installation_id: str | None = Field(default=None, description="ID extension profile thực thi tác vụ.")



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
    count: int = Field(default=1, ge=1, le=4, description="Number of images to generate (1-4). Default 1.")
    variant_count: int = Field(default=1, ge=1, le=4, description="Alias for count (1-4).")
    input_images: list[InlineImageInput] = Field(default_factory=list)
    reference_media_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None

    @model_validator(mode="after")
    def sync_counts(self):
        if self.variant_count > 1 and self.count == 1:
            self.count = self.variant_count
        elif self.count > 1 and self.variant_count == 1:
            self.variant_count = self.count
        elif self.count != self.variant_count:
            raise ValueError(f"Conflicting count definitions: count={self.count}, variant_count={self.variant_count}")
        return self


class CharacterVideoGenerationRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "9:16"
    duration_seconds: Literal[4, 6, 8, 10] = 8
    dialogue: bool = Field(default=False, description="Compatibility-only; describe speech in prompt.")
    project_id: str | None = None
