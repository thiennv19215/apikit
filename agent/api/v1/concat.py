"""Client API v1 — Video Concat & Audio Mix endpoints."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.config import SHARED_OUTPUT_DIR
from agent.services.post_process import merge_videos, add_narration, add_music

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/videos", tags=["Client API v1 Video Concat"])


class VideoConcatRequest(BaseModel):
    video_urls: list[str] = Field(..., min_length=1, description="List of video URLs or paths to concatenate in sequence")
    narration_audio_url: Optional[str] = Field(default=None, description="Optional narration voiceover audio URL")
    narration_volume: float = Field(default=1.0, ge=0.0, le=2.0, description="Volume for voiceover")
    music_url: Optional[str] = Field(default=None, description="Optional background music URL")
    music_volume: float = Field(default=0.3, ge=0.0, le=2.0, description="Volume for background music")


class VideoConcatResponse(BaseModel):
    id: str
    status: str
    video_url: str
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


async def _download_file(url: str, dest_path: Path) -> None:
    """Download a remote URL to local file path or copy if local."""
    if url.startswith("http://") or url.startswith("https://"):
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest_path.write_bytes(resp.content)
    elif Path(url).exists():
        shutil.copy2(url, dest_path)
    else:
        raise HTTPException(status_code=400, detail=f"File source not reachable or found: {url}")


@router.post("/concat", response_model=VideoConcatResponse, status_code=status.HTTP_201_CREATED)
async def concat_videos_endpoint(body: VideoConcatRequest, request: Request):
    """Concatenate multiple video clips and optionally mix voiceover and background music."""
    SHARED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    concat_id = f"concat_{uuid.uuid4().hex[:12]}"
    out_filename = f"{concat_id}.mp4"
    final_output_path = str(SHARED_OUTPUT_DIR / out_filename)

    with tempfile.TemporaryDirectory(prefix="flowkit_concat_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        local_video_paths: list[str] = []

        # 1. Download all video clips
        for idx, v_url in enumerate(body.video_urls):
            local_clip = tmp_path / f"clip_{idx:03d}.mp4"
            try:
                await _download_file(v_url, local_clip)
                local_video_paths.append(str(local_clip))
            except Exception as e:
                logger.error("Failed to fetch video %s: %s", v_url, e)
                raise HTTPException(status_code=400, detail=f"Failed to fetch video {idx}: {e}")

        # 2. Concatenate video clips
        merged_step_path = str(tmp_path / "merged_stage.mp4")
        if len(local_video_paths) == 1:
            shutil.copy2(local_video_paths[0], merged_step_path)
            success = True
        else:
            success = merge_videos(local_video_paths, merged_step_path)

        if not success or not Path(merged_step_path).exists():
            raise HTTPException(status_code=500, detail="Failed to concatenate video clips using ffmpeg")

        current_stage_path = merged_step_path

        # 3. Overlay Narration if provided
        if body.narration_audio_url:
            local_narr = tmp_path / "narration.wav"
            try:
                await _download_file(body.narration_audio_url, local_narr)
                narr_output = str(tmp_path / "narrated_stage.mp4")
                narr_ok = add_narration(
                    video_path=current_stage_path,
                    narration_path=str(local_narr),
                    output_path=narr_output,
                    narration_volume=body.narration_volume,
                )
                if narr_ok and Path(narr_output).exists():
                    current_stage_path = narr_output
                else:
                    logger.warning("add_narration failed; proceeding with base video")
            except Exception as e:
                logger.warning("Failed to download or apply narration audio: %s", e)

        # 4. Overlay Music if provided
        if body.music_url:
            local_music = tmp_path / "music.mp3"
            try:
                await _download_file(body.music_url, local_music)
                music_output = str(tmp_path / "music_stage.mp4")
                music_ok = add_music(
                    video_path=current_stage_path,
                    music_path=str(local_music),
                    output_path=music_output,
                    music_volume=body.music_volume,
                )
                if music_ok and Path(music_output).exists():
                    current_stage_path = music_output
                else:
                    logger.warning("add_music failed; proceeding with current video")
            except Exception as e:
                logger.warning("Failed to download or apply background music: %s", e)

        # 5. Move final file to output dir
        shutil.copy2(current_stage_path, final_output_path)

    base_url = str(request.base_url).rstrip("/")
    video_url = f"{base_url}/v1/videos/download/{out_filename}"

    return VideoConcatResponse(
        id=concat_id,
        status="complete",
        video_url=video_url,
    )


@router.get("/download/{filename}")
async def download_video(filename: str):
    """Download generated or concatenated video MP4 file."""
    safe_name = Path(filename).name
    target = SHARED_OUTPUT_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(path=target, media_type="video/mp4", filename=safe_name)
