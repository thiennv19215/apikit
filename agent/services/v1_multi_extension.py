"""Apikit V1 multi-extension routing boundary.

FlowKit upstream remains the execution/core implementation.  This module owns
Apikit's V1-only routing policy: choosing an eligible extension/profile,
preserving media ownership affinity, and carrying installation/project affinity
between V1 retries.

Keep HTTP concerns out of this layer.  Callers translate V1RoutingError into
their own API/worker error shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.config import USE_BATCH_RPC
from agent.services import execution_audit


async def media_owner(media_id: str):
    """Delegate to execution_audit.media_owner unless monkeypatched on this module."""
    return await execution_audit.media_owner(media_id)


class V1RoutingError(RuntimeError):
    """Raised when a V1 request cannot be routed to a usable Flow profile."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class V1RoutingContext:
    installation_id: str | None = None
    project_id: str = ""


class V1MultiExtensionRouter:
    """V1-specific adapter over the FlowKit execution client.

    The underlying Flow client still owns WebSocket connections and Flow RPCs.
    This class owns only Apikit V1 policy and avoids spreading knowledge of
    private multi-extension internals across V1 controllers and workers.
    """

    def __init__(self, client: Any):
        self.client = client

    def _profiles(self) -> list[dict]:
        list_extensions = getattr(self.client, "list_extensions", None)
        if not callable(list_extensions):
            return []
        try:
            return list(list_extensions() or [])
        except Exception:
            return []

    def ensure_connected(self) -> None:
        if getattr(self.client, "connected", False):
            return
        if self._profiles():
            return
        raise V1RoutingError("PROFILE_UNAVAILABLE", "Flow extension is not connected")

    def available_profile_count(self) -> int:
        profiles = self._profiles()
        if profiles:
            return sum(1 for p in profiles if p.get("available", True))
        return 1 if getattr(self.client, "connected", False) else 0

    def has_available_profile(self) -> bool:
        selector = getattr(self.client, "_select_extension", None)
        if callable(selector):
            return selector(require_token=not USE_BATCH_RPC) is not None
        return self.available_profile_count() > 0

    async def resolve_context(self, payload: dict) -> V1RoutingContext:
        """Resolve V1 installation/project affinity without changing V1 contract."""
        pid = str(payload.get("project_id") or "")
        inst_id = payload.get("installation_id")

        # A previous executor may have exhausted quota.  The V1 contract treats
        # installation_id as last executor/affinity hint, not a permanent lock.
        exhausted = getattr(self.client, "is_installation_exhausted", None)
        if inst_id and callable(exhausted) and exhausted(inst_id):
            inst_id = None
            pid = ""

        # UUID-only provider media cannot move between Google Flow accounts.
        # Preserve the owner profile/project. Base64 images can be uploaded again
        # on another profile, so they intentionally do not pin routing here.
        raw_ids = list(payload.get("reference_media_ids") or [])
        raw_ids += list(payload.get("character_media_ids") or [])
        raw_ids += [
            payload[key]
            for key in ("start_media_id", "end_media_id", "base_media_id")
            if payload.get(key)
        ]
        raw_ids += [
            img["media_id"]
            for img in payload.get("input_images", [])
            if isinstance(img, dict)
            and img.get("media_id")
            and not img.get("image_base64")
        ]

        for media_id in dict.fromkeys(raw_ids):
            owner = await media_owner(media_id)
            if not owner:
                if inst_id:
                    continue
                raise V1RoutingError(
                    "MEDIA_OWNER_UNKNOWN",
                    "upload the original image again before using this UUID",
                )
            owner_inst = owner.get("installation_id")
            owner_pid = owner.get("project_id")
            if (inst_id and inst_id != owner_inst) or (pid and pid != owner_pid):
                raise V1RoutingError(
                    "MEDIA_ACCOUNT_MISMATCH",
                    "inputs must share an owner/project; re-upload the original images",
                )
            inst_id = owner_inst
            pid = owner_pid or ""

        selector = getattr(self.client, "_select_extension", None)
        if not callable(selector):
            # Compatibility for tests/single-client implementations that do not
            # expose the Apikit connection pool.
            return V1RoutingContext(inst_id, pid)

        selected = selector(
            require_token=not USE_BATCH_RPC,
            preferred_installation=inst_id,
            preferred_project_id=pid or None,
        )
        if selected is None:
            raise V1RoutingError("PROFILE_UNAVAILABLE", "no eligible profile")

        sessions = getattr(self.client, "_extensions", {})
        selected_profile = sessions.get(selected, {})
        selected_inst_id = selected_profile.get("installation_id")
        selected_pid = selected_profile.get("flow_project_id")

        if inst_id and selected_inst_id != inst_id:
            raise V1RoutingError(
                "PROFILE_UNAVAILABLE",
                "requested installation is not an eligible authenticated profile",
            )
        if pid and not inst_id and selected_pid != pid:
            raise V1RoutingError(
                "PROFILE_UNAVAILABLE",
                "requested project has no available profile",
            )

        if not inst_id:
            inst_id = selected_inst_id
        if not inst_id:
            raise V1RoutingError(
                "PROFILE_UNAVAILABLE",
                "selected profile has no installation_id; reconnect the Flow extension",
            )

        if not pid:
            resolver = getattr(self.client, "_batch_project_id", None)
            if not callable(resolver):
                raise V1RoutingError(
                    "PROFILE_UNAVAILABLE",
                    "selected profile has no project resolver",
                )
            try:
                pid = resolver("", preferred_installation=inst_id)
            except Exception as exc:
                raise V1RoutingError(
                    "PROFILE_UNAVAILABLE",
                    f"selected profile has no usable Flow project: {exc}",
                ) from exc

        return V1RoutingContext(inst_id, str(pid or ""))

    async def upload_image(
        self,
        *,
        image_base64: str,
        mime_type: str = "image/jpeg",
        project_id: str = "",
        file_name: str = "image.jpg",
        preferred_installation: str | None = None,
    ) -> dict:
        """Upload through the FlowKit client while keeping V1 profile affinity."""
        return await self.client.upload_image(
            image_base64=image_base64,
            mime_type=mime_type,
            project_id=project_id,
            file_name=file_name,
            preferred_installation=preferred_installation,
        )


def get_v1_multi_extension_router(client: Any) -> V1MultiExtensionRouter:
    return V1MultiExtensionRouter(client)
