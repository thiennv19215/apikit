"""FlowKit Python SDK Client.

Provides a unified, easy-to-use Python interface for AI Agents to interact with
FlowKit backend (FastAPI at http://127.0.0.1:8100), manage projects, entities,
scenes, trigger throttled batch jobs, poll statuses, and invoke V1 generations.

Usage:
    from flowkit_client import FlowKitClient

    client = FlowKitClient()
    print(client.health())

    # Create project
    proj = client.create_project(name="SciFi Mission", material="realistic", story="...")
    pid = proj["id"]

    # Create character
    char = client.create_character(pid, name="Commander Ray", entity_type="character",
                                   image_prompt="Portrait of space commander...")
    
    # Generate refs -> scenes -> videos
    client.batch_generate_refs(pid)
    client.poll_batch(project_id=pid, req_type="GENERATE_REF_IMAGE")
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

import urllib.error
import urllib.parse
import urllib.request


class FlowKitError(Exception):
    """Base exception for FlowKit client errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class FlowKitClient:
    """Client for communicating with the FlowKit local automation server."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
    ):
        if not base_url:
            base_url = os.getenv("FLOWKIT_BASE_URL", "https://apikit.shopcongngheso5.io.vn")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── Internal HTTP helper ──

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        files: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{self.base_url}{endpoint}"
        h = {"Accept": "application/json"}
        if headers:
            h.update(headers)

        if HAS_HTTPX:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_data,
                        data=data,
                        files=files,
                        headers=h,
                    )
                    if not (200 <= resp.status_code < 300):
                        raise FlowKitError(
                            f"HTTP {resp.status_code} on {method} {endpoint}: {resp.text}",
                            status_code=resp.status_code,
                            response_body=resp.text,
                        )
                    if resp.status_code == 204 or not resp.content:
                        return None
                    try:
                        return resp.json()
                    except Exception:
                        return resp.text
            except httpx.HTTPError as e:
                raise FlowKitError(f"HTTP request error: {e}") from e
        else:
            # Fallback to urllib
            if params:
                query_str = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
                url = f"{url}?{query_str}"

            req_headers = dict(h)
            body_bytes = None
            if json_data is not None:
                req_headers["Content-Type"] = "application/json"
                body_bytes = json.dumps(json_data).encode("utf-8")

            req = urllib.request.Request(url=url, data=body_bytes, headers=req_headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    res_body = response.read().decode("utf-8")
                    if not res_body:
                        return None
                    try:
                        return json.loads(res_body)
                    except Exception:
                        return res_body
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8") if e.fp else ""
                raise FlowKitError(
                    f"HTTP {e.code} on {method} {endpoint}: {err_body}",
                    status_code=e.code,
                    response_body=err_body,
                ) from e
            except Exception as e:
                raise FlowKitError(f"Connection error: {e}") from e

    # ── 1. Health & Status ──

    def health(self) -> Dict[str, Any]:
        """Check server status and Chrome Extension connection.
        
        Returns:
            {"status": "ok", "version": "...", "extension_connected": bool, "ws": {...}}
        """
        return self._request("GET", "/health")

    def is_extension_connected(self) -> bool:
        """Return True if Chrome Extension is connected to WebSocket."""
        try:
            h = self.health()
            return bool(h.get("extension_connected", False))
        except Exception:
            return False

    def wait_for_extension(self, timeout: float = 60.0, interval: float = 2.0) -> bool:
        """Block until Chrome Extension connects or timeout occurs."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_extension_connected():
                return True
            time.sleep(interval)
        return False

    def v1_health(self) -> Dict[str, Any]:
        """Check Client V1 API readiness and capabilities."""
        return self._request("GET", "/v1/health")

    # ── 2. Materials & Models ──

    def list_materials(self) -> List[Dict[str, Any]]:
        """List all available image styles (materials) like realistic, 3d_pixar, anime, etc."""
        return self._request("GET", "/api/materials")

    def list_models(self) -> Dict[str, Any]:
        """List configured video and image models."""
        return self._request("GET", "/api/models")

    def change_model(self, model_type: str, model_name: str) -> Dict[str, Any]:
        """Update active model configuration."""
        return self._request("POST", "/api/models/change", json_data={"type": model_type, "model": model_name})

    # ── 3. Active Project ──

    def get_active_project(self) -> Dict[str, Any]:
        """Get the ID and info of currently active working project."""
        return self._request("GET", "/api/active-project")

    def set_active_project(self, project_id: str) -> Dict[str, Any]:
        """Set the active working project."""
        return self._request("POST", "/api/active-project", json_data={"project_id": project_id})

    # ── 4. Projects ──

    def list_projects(self) -> List[Dict[str, Any]]:
        """List all video projects in database."""
        return self._request("GET", "/api/projects")

    def get_project(self, project_id: str) -> Dict[str, Any]:
        """Get full details of a project including entities and videos."""
        return self._request("GET", f"/api/projects/{project_id}")

    def create_project(
        self,
        name: str,
        material: str,
        story: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new project.
        
        Args:
            name: Project title
            material: Art style key (e.g. 'realistic', '3d_pixar', 'anime')
            story: Summary of storyline
            metadata: Additional metadata
        """
        payload = {
            "name": name,
            "material": material,
            "story": story,
            "metadata": metadata or {},
        }
        res = self._request("POST", "/api/projects", json_data=payload)
        # Auto-set as active
        if isinstance(res, dict) and "id" in res:
            try:
                self.set_active_project(res["id"])
            except Exception:
                pass
        return res

    def patch_project(self, project_id: str, **fields: Any) -> Dict[str, Any]:
        """Update fields of an existing project."""
        return self._request("PATCH", f"/api/projects/{project_id}", json_data=fields)

    def delete_project(self, project_id: str) -> Any:
        """Delete a project and all its associated data."""
        return self._request("DELETE", f"/api/projects/{project_id}")

    # ── 5. Characters & Entities ──

    def list_characters(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List characters/entities, optionally filtered by project."""
        if project_id:
            return self._request("GET", f"/api/projects/{project_id}/characters")
        return self._request("GET", "/api/characters")

    def get_character(self, character_id: str) -> Dict[str, Any]:
        """Get character details."""
        return self._request("GET", f"/api/characters/{character_id}")

    def create_character(
        self,
        project_id: str,
        name: str,
        entity_type: str = "character",
        image_prompt: str = "",
        description: str = "",
        voice_description: str = "",
        media_id: Optional[str] = None,
        reference_image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a character/location entity and attach it to a project.
        
        Args:
            project_id: Project UUID
            name: Entity name/alias (English recommended)
            entity_type: 'character', 'location', or 'asset'
            image_prompt: Visual prompt for generating reference image
            description: Narrative description
            voice_description: Voice traits for TTS narration
            media_id: UUID if already generated, otherwise None
            reference_image_url: URL if available
        """
        payload = {
            "name": name,
            "entity_type": entity_type,
            "image_prompt": image_prompt,
            "description": description,
            "voice_description": voice_description,
        }
        if media_id:
            payload["media_id"] = media_id
        if reference_image_url:
            payload["reference_image_url"] = reference_image_url

        char = self._request("POST", "/api/characters", json_data=payload)
        cid = char["id"]
        # Attach to project
        self._request("POST", f"/api/projects/{project_id}/characters/{cid}")
        return char

    def patch_character(self, character_id: str, **fields: Any) -> Dict[str, Any]:
        """Update character fields (e.g. media_id, image_prompt)."""
        return self._request("PATCH", f"/api/characters/{character_id}", json_data=fields)

    def delete_character(self, character_id: str) -> Any:
        """Delete a character."""
        return self._request("DELETE", f"/api/characters/{character_id}")

    # ── 6. Videos & Scenes ──

    def list_videos(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List videos, optionally filtered by project_id."""
        params = {"project_id": project_id} if project_id else None
        return self._request("GET", "/api/videos", params=params)

    def get_video(self, video_id: str) -> Dict[str, Any]:
        """Get video details and its ordered scenes."""
        return self._request("GET", f"/api/videos/{video_id}")

    def create_video(
        self,
        project_id: str,
        title: str = "Video Episode",
        aspect_ratio: str = "VERTICAL",
    ) -> Dict[str, Any]:
        """Create a new video in a project.
        
        Args:
            project_id: Project UUID
            title: Video title
            aspect_ratio: 'VERTICAL' (9:16) or 'HORIZONTAL' (16:9)
        """
        payload = {
            "project_id": project_id,
            "title": title,
            "aspect_ratio": aspect_ratio,
        }
        return self._request("POST", "/api/videos", json_data=payload)

    def delete_video(self, video_id: str) -> Any:
        """Delete a video."""
        return self._request("DELETE", f"/api/videos/{video_id}")

    def list_scenes(self, video_id: str) -> List[Dict[str, Any]]:
        """List all scenes in a video."""
        return self._request("GET", "/api/scenes", params={"video_id": video_id})

    def get_scene(self, scene_id: str) -> Dict[str, Any]:
        """Get scene details."""
        return self._request("GET", f"/api/scenes/{scene_id}")

    def create_scene(
        self,
        video_id: str,
        scene_number: int,
        prompt: str,
        video_prompt: str = "",
        narrator_text: str = "",
        character_names: Optional[List[str]] = None,
        chain_type: str = "FIRST_FRAME",
        parent_scene_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a scene in a video.
        
        Args:
            video_id: Video UUID
            scene_number: 1-indexed scene position
            prompt: Action-only scene image prompt (NO character appearance descriptions!)
            video_prompt: Sub-clip timed video prompt (e.g. '0-3s: [action]. 3-6s: [action].')
            narrator_text: Narration speech or subtitle
            character_names: List of entity names appearing in this scene
            chain_type: 'FIRST_FRAME', 'START_END_FRAME', or 'CONTINUATION'
            parent_scene_id: Optional UUID of previous scene for transition chaining
        """
        payload = {
            "video_id": video_id,
            "scene_number": scene_number,
            "prompt": prompt,
            "video_prompt": video_prompt or prompt,
            "narrator_text": narrator_text,
            "character_names": character_names or [],
            "chain_type": chain_type,
        }
        if parent_scene_id:
            payload["parent_scene_id"] = parent_scene_id

        return self._request("POST", "/api/scenes", json_data=payload)

    def patch_scene(self, scene_id: str, **fields: Any) -> Dict[str, Any]:
        """Update mutable fields of a scene (prompt, video_prompt, narrator_text, etc.)."""
        return self._request("PATCH", f"/api/scenes/{scene_id}", json_data=fields)

    def delete_scene(self, scene_id: str) -> Any:
        """Delete a scene."""
        return self._request("DELETE", f"/api/scenes/{scene_id}")

    # ── 7. Batch Pipeline (Throttled Generation) ──

    def submit_batch(self, requests: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Submit a batch of requests to the worker queue.
        
        The server automatically throttles execution (max 5 concurrent, 10s cooldown).
        DO NOT write custom loop scripts. Submit all requests via this method!

        Args:
            requests: List of request dicts with:
                - 'type': 'GENERATE_REF_IMAGE', 'GENERATE_IMAGE', 'GENERATE_VIDEO', 'UPSCALE_VIDEO'
                - 'project_id', 'video_id', 'scene_id', 'character_id' (as applicable)
                - 'orientation': 'VERTICAL' or 'HORIZONTAL'
        """
        return self._request("POST", "/api/requests/batch", json_data={"requests": requests})

    def get_batch_status(
        self,
        project_id: Optional[str] = None,
        video_id: Optional[str] = None,
        req_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query aggregate status of batch requests.
        
        Returns:
            {"total": int, "pending": int, "processing": int, "completed": int,
             "failed": int, "done": bool, "all_succeeded": bool}
        """
        params = {}
        if project_id:
            params["project_id"] = project_id
        if video_id:
            params["video_id"] = video_id
        if req_type:
            params["type"] = req_type
        return self._request("GET", "/api/requests/batch-status", params=params)

    def poll_batch(
        self,
        project_id: Optional[str] = None,
        video_id: Optional[str] = None,
        req_type: Optional[str] = None,
        interval: float = 10.0,
        timeout: float = 1800.0,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Poll batch-status until done=True or timeout.
        
        Args:
            project_id: Optional filter
            video_id: Optional filter
            req_type: Optional filter ('GENERATE_IMAGE', 'GENERATE_VIDEO', etc.)
            interval: Poll interval seconds (default 10s)
            timeout: Max seconds to wait (default 30 min)
            on_progress: Optional callback function receiving status dict
        """
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_batch_status(project_id=project_id, video_id=video_id, req_type=req_type)
            if on_progress:
                on_progress(status)
            if status.get("done", False):
                return status
            time.sleep(interval)

        raise TimeoutError(f"Batch generation timed out after {timeout} seconds")

    # ── High-Level Convenience Batch Generators ──

    def batch_generate_refs(self, project_id: str) -> Dict[str, Any]:
        """Batch submit reference image generation for all entities in project."""
        entities = self.list_characters(project_id)
        requests = []
        for ent in entities:
            # Locations are horizontal/landscape, characters are vertical/portrait
            orientation = "HORIZONTAL" if ent.get("entity_type") in ("location", "landscape", "background") else "VERTICAL"
            requests.append({
                "type": "GENERATE_REF_IMAGE",
                "project_id": project_id,
                "character_id": ent["id"],
                "orientation": orientation,
            })
        if not requests:
            return {"queued": 0, "message": "No entities found"}
        return self.submit_batch(requests)

    def batch_generate_scene_images(self, video_id: str, orientation: str = "VERTICAL") -> Dict[str, Any]:
        """Batch submit scene image generation for all scenes in video."""
        scenes = self.list_scenes(video_id)
        requests = []
        for sc in scenes:
            requests.append({
                "type": "GENERATE_IMAGE",
                "video_id": video_id,
                "scene_id": sc["id"],
                "orientation": orientation,
            })
        if not requests:
            return {"queued": 0, "message": "No scenes found"}
        return self.submit_batch(requests)

    def batch_generate_scene_videos(self, video_id: str, orientation: str = "VERTICAL") -> Dict[str, Any]:
        """Batch submit video generation for all scenes in video."""
        scenes = self.list_scenes(video_id)
        requests = []
        for sc in scenes:
            requests.append({
                "type": "GENERATE_VIDEO",
                "video_id": video_id,
                "scene_id": sc["id"],
                "orientation": orientation,
            })
        if not requests:
            return {"queued": 0, "message": "No scenes found"}
        return self.submit_batch(requests)

    # ── 8. Upload & Direct Image Proxy ──

    def upload_image(
        self,
        file_path: Optional[Union[str, Path]] = None,
        image_base64: Optional[str] = None,
        project_id: str = "",
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload an image (file path or base64) directly to Google Flow and return its media_id UUID."""
        if not file_path and not image_base64:
            raise ValueError("Either file_path or image_base64 must be provided")

        payload: Dict[str, Any] = {
            "project_id": project_id,
        }

        if image_base64:
            payload["image_base64"] = image_base64
            payload["file_name"] = file_name or "image.png"
            if mime_type:
                payload["mime_type"] = mime_type
        else:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")

            guessed_mime, _ = mimetypes.guess_type(str(path))
            payload["mime_type"] = mime_type or guessed_mime or "image/jpeg"
            payload["file_name"] = file_name or path.name

            with open(path, "rb") as f:
                payload["image_base64"] = base64.b64encode(f.read()).decode("utf-8")

        return self._request("POST", "/api/flow/upload-image", json_data=payload)

    # ── 9. TTS Narration & Voice ──

    def generate_narrator(
        self,
        video_id: str,
        voice_template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate AI voice narration for all scenes having narrator_text."""
        payload = {}
        if voice_template_id:
            payload["voice_template_id"] = voice_template_id
        return self._request("POST", f"/api/videos/{video_id}/narrate", json_data=payload)

    def list_voice_templates(self) -> List[Dict[str, Any]]:
        """List saved ElevenLabs/TTS voice templates."""
        return self._request("GET", "/api/tts/templates")

    # ── 10. Suno AI Music Generation ──

    def list_music_templates(self) -> List[Dict[str, Any]]:
        """List predefined genre song templates."""
        return self._request("GET", "/api/music/templates")

    def generate_music(
        self,
        prompt: str,
        style: str,
        title: str,
        instrumental: bool = True,
    ) -> Dict[str, Any]:
        """Generate background soundtrack using Suno AI."""
        payload = {
            "prompt": prompt,
            "style": style,
            "title": title,
            "instrumental": instrumental,
        }
        return self._request("POST", "/api/music/generate", json_data=payload)

    # ── 11. Client V1 API (Gemini Omni Flash & Direct Base64) ──

    def v1_generate_video(
        self,
        prompt: str,
        input_images: List[Dict[str, Any]],
        duration_seconds: int = 8,
        aspect_ratio: str = "9:16",
        model: str = "omni_flash",
    ) -> Dict[str, Any]:
        """Directly invoke Omni Flash video generation via Client V1 API.
        
        Args:
            prompt: Text prompt describing motion/cinematics
            input_images: List of dicts:
                - First Frame: [{"image_base64": "...", "mime_type": "image/jpeg", "role": "start_frame"}]
                - Start+End: [{"image_base64": "...", "role": "start_frame"}, {"image_base64": "...", "role": "end_frame"}]
                - R2V: 1-7 dicts with role='reference'
            duration_seconds: 4, 6, 8, or 10
            aspect_ratio: '9:16' or '16:9'
            model: 'omni_flash'
        """
        payload = {
            "prompt": prompt,
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "model": model,
            "input_images": input_images,
        }
        return self._request("POST", "/v1/videos/generations", json_data=payload)

    def v1_generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "9:16",
        input_images: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Directly invoke image generation via Client V1 API."""
        payload = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "input_images": input_images or [],
        }
        return self._request("POST", "/v1/images/generations", json_data=payload)

    def v1_get_job(self, job_id: str) -> Dict[str, Any]:
        """Check status of a Client V1 generation job."""
        return self._request("GET", f"/v1/jobs/{job_id}")

    def v1_list_materials(self) -> List[Dict[str, Any]]:
        """List all visual styles / materials available in FlowKit."""
        return self._request("GET", "/v1/materials")

    def v1_list_voices(self) -> List[Dict[str, Any]]:
        """List available voice templates for text-to-speech."""
        return self._request("GET", "/v1/audio/voices")

    def v1_generate_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        instruct: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate speech audio from text using TTS."""
        payload = {
            "text": text,
            "voice_id": voice_id,
            "speed": speed,
            "instruct": instruct,
        }
        return self._request("POST", "/v1/audio/speech", json_data=payload)

    def v1_generate_music(
        self,
        prompt: str,
        style: str = "",
        title: str = "",
        instrumental: bool = True,
        poll: bool = False,
    ) -> Dict[str, Any]:
        """Generate background music using Suno AI via Client V1."""
        payload = {
            "prompt": prompt,
            "style": style,
            "title": title,
            "instrumental": instrumental,
            "poll": poll,
        }
        return self._request("POST", "/v1/audio/music", json_data=payload)

    def v1_get_music(self, task_id: str) -> Dict[str, Any]:
        """Get status of a music generation task."""
        return self._request("GET", f"/v1/audio/music/{task_id}")

    def v1_concat_videos(
        self,
        video_urls: List[str],
        narration_audio_url: Optional[str] = None,
        music_url: Optional[str] = None,
        narration_volume: float = 1.0,
        music_volume: float = 0.3,
    ) -> Dict[str, Any]:
        """Concatenate video clips and mix narration/music into a final movie."""
        payload = {
            "video_urls": video_urls,
            "narration_audio_url": narration_audio_url,
            "music_url": music_url,
            "narration_volume": narration_volume,
            "music_volume": music_volume,
        }
        return self._request("POST", "/v1/videos/concat", json_data=payload)

    def v1_create_character(
        self,
        name: str,
        description: str = "",
        image_prompt: str = "",
        voice_description: Optional[str] = None,
        entity_type: str = "character",
        input_images: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create a character entity with reference images via Client V1."""
        payload = {
            "name": name,
            "description": description,
            "image_prompt": image_prompt,
            "voice_description": voice_description,
            "entity_type": entity_type,
            "input_images": input_images or [],
        }
        return self._request("POST", "/v1/characters", json_data=payload)

    def v1_list_characters(self) -> List[Dict[str, Any]]:
        """List all characters in database via Client V1."""
        return self._request("GET", "/v1/characters")

    def v1_get_character(self, character_id: str) -> Dict[str, Any]:
        """Get character details by ID via Client V1."""
        return self._request("GET", f"/v1/characters/{character_id}")


# ── CLI Quick Test ──
if __name__ == "__main__":
    client = FlowKitClient()
    print("Testing connection to FlowKit backend...")
    try:
        status = client.health()
        print("Connected successfully!")
        print(json.dumps(status, indent=2))
    except Exception as e:
        print(f"FlowKit server not running or error: {e}")
        print("Start it with: python -m agent.main")
