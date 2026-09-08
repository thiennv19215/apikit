"""Public client availability, without exposing internal account details."""
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from agent import config
from agent.db.schema import get_db
from agent.services.flow_client import get_flow_client

router = APIRouter(tags=["Client API v1"])


class Capability(BaseModel):
    available: bool
    reason: str | None = None


class ClientHealth(BaseModel):
    status: Literal["ready", "degraded", "maintenance", "unavailable"]
    maintenance: bool
    accepting_requests: bool
    message: str
    retry_after_seconds: int | None = None
    capabilities: dict[str, Capability] = Field(default_factory=dict)


def maintenance_response() -> ClientHealth:
    return ClientHealth(
        status="maintenance", maintenance=True, accepting_requests=False,
        message="Backend is under maintenance. Please try again later.",
        retry_after_seconds=60,
        capabilities={name: Capability(available=False, reason="MAINTENANCE")
                      for name in ("image_generation", "video_generation")},
    )


@router.get("/v1/health", response_model=ClientHealth,
            responses={503: {"model": ClientHealth, "description": "Maintenance or unavailable"}})
async def client_health(response: Response):
    response.headers["Cache-Control"] = "no-store"
    if config.CLIENT_MAINTENANCE:
        response.status_code = 503
        response.headers["Retry-After"] = "60"
        return maintenance_response()

    try:
        db = await get_db()
        async with db.execute("SELECT 1") as cursor:
            await cursor.fetchone()
        client = get_flow_client()
        provider_ready = client.connected and any(
            item.get("available") for item in client.list_extensions()
        )
        reason = None if provider_ready else "PROVIDER_UNAVAILABLE"
    except Exception:
        provider_ready = False
        reason = "BACKEND_UNAVAILABLE"

    # The captured batch request supports Abra first-frame only. Start/end and
    # R2V stay disabled until their own payloads are captured and verified.
    video_first_frame_ready = bool(provider_ready)
    if not provider_ready:
        response.status_code = 503
        response.headers["Retry-After"] = "10"
    return ClientHealth(
        status="ready" if provider_ready else "unavailable",
        maintenance=False, accepting_requests=bool(provider_ready),
        message=("Image and Omni Flash video generation are available."
                 if provider_ready else "Backend cannot currently accept generation requests."),
        retry_after_seconds=None if provider_ready else 10,
        capabilities={
            "image_generation": Capability(available=bool(provider_ready), reason=reason),
            "video_generation": Capability(available=bool(provider_ready), reason=reason),
            "video_first_frame": Capability(available=video_first_frame_ready, reason=reason),
            "video_start_end": Capability(available=bool(provider_ready), reason=reason),
            "video_reference": Capability(available=bool(provider_ready), reason=reason),
        },
    )
