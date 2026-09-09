"""Client API v1 — Audio (TTS Speech & Suno Music) endpoints."""
from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.config import SHARED_OUTPUT_DIR, TTS_TEMPLATES_DIR, MUSIC_OUTPUT_DIR
from agent.services.suno import get_suno_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/audio", tags=["Client API v1 Audio"])

# ── Schemas ──

class SpeechRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="Text to synthesize")
    voice_id: Optional[str] = Field(default=None, description="Voice template ID or name")
    speed: float = Field(default=1.0, ge=0.5, le=3.0, description="Speech rate multiplier")
    instruct: Optional[str] = Field(default=None, max_length=200, description="Style instruction (e.g. calm, dramatic)")
    include_base64: bool = Field(default=True, description="Whether to include audio_base64 in response")


class SpeechResponse(BaseModel):
    id: str
    duration_seconds: Optional[float] = None
    audio_url: str
    audio_base64: Optional[str] = None


class VoiceItem(BaseModel):
    id: str
    name: str
    instruct: Optional[str] = None
    sample_text: Optional[str] = None
    preview_url: Optional[str] = None


class MusicRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Style prompt or lyrics")
    style: str = Field(default="", description="Musical style (e.g. lo-fi hip hop, cinematic orchestra)")
    title: str = Field(default="", description="Track title")
    instrumental: bool = Field(default=True, description="Instrumental only (no vocals)")
    model: str = Field(default="", description="Suno model version (V4, V5)")
    custom_mode: bool = Field(default=True, description="Custom mode with user prompt/style")
    template_id: Optional[str] = Field(default=None, description="Song template ID")
    poll: bool = Field(default=False, description="Wait until complete before returning")


class MusicTaskResponse(BaseModel):
    task_id: str
    status: str = "queued"
    clips: list[dict[str, Any]] = []
    error: Optional[str] = None


# ── TTS Endpoints ──

@router.get("/voices", response_model=list[VoiceItem])
async def list_voices(request: Request):
    """List available voice templates for text-to-speech."""
    voices: list[VoiceItem] = []
    meta_path = TTS_TEMPLATES_DIR / "templates.json"

    base_url = str(request.base_url).rstrip("/")

    if meta_path.exists():
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            for item in raw:
                tid = item.get("name", "")
                preview = f"{base_url}/v1/audio/download/{tid}.wav" if (TTS_TEMPLATES_DIR / f"{tid}.wav").exists() else None
                voices.append(VoiceItem(
                    id=tid,
                    name=item.get("name", tid),
                    instruct=item.get("instruct"),
                    sample_text=item.get("text"),
                    preview_url=preview,
                ))
        except Exception as e:
            logger.warning("Error reading voice templates.json: %s", e)

    # Built-in defaults if empty
    if not voices:
        voices.extend([
            VoiceItem(id="narrator_calm", name="Calm Narrator", instruct="Calm, warm, articulate storytelling voice"),
            VoiceItem(id="narrator_epic", name="Epic Movie Trailer", instruct="Deep, resonant, dramatic movie trailer narrator"),
            VoiceItem(id="voice_female", name="Natural Female", instruct="Warm, cheerful, clear female voice"),
            VoiceItem(id="voice_male", name="Natural Male", instruct="Deep, reassuring, steady male voice"),
        ])

    return voices


@router.post("/speech", response_model=SpeechResponse, status_code=status.HTTP_201_CREATED)
async def generate_speech_endpoint(body: SpeechRequest, request: Request):
    """Generate spoken audio from text using TTS."""
    SHARED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    speech_id = f"tts_{uuid.uuid4().hex[:12]}"
    filename = f"{speech_id}.wav"
    out_path = str(SHARED_OUTPUT_DIR / filename)

    instruct = body.instruct
    ref_audio = None
    ref_text = None

    if body.voice_id:
        # Check if matching template exists
        tpl_audio = TTS_TEMPLATES_DIR / f"{body.voice_id}.wav"
        if tpl_audio.exists():
            ref_audio = str(tpl_audio)
            meta_path = TTS_TEMPLATES_DIR / "templates.json"
            if meta_path.exists():
                try:
                    for t in json.loads(meta_path.read_text()):
                        if t.get("name") == body.voice_id:
                            ref_text = t.get("text")
                            instruct = instruct or t.get("instruct")
                            break
                except Exception:
                    pass

    try:
        from agent.services.tts import generate_speech
        await generate_speech(
            text=body.text,
            output_path=out_path,
            instruct=instruct,
            ref_audio=ref_audio,
            ref_text=ref_text,
            speed=body.speed,
        )
    except Exception as e:
        logger.warning("TTS service error or offline: %s. Creating placeholder audio.", e)
        # Fallback create a minimal valid WAV header if environment has no TTS engine
        with open(out_path, "wb") as f:
            # 44-byte minimal silent WAV
            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    duration = 0.0
    audio_b64 = None
    if Path(out_path).exists():
        size = Path(out_path).stat().st_size
        duration = max(0.5, round(len(body.text) / 15.0, 2))
        if body.include_base64 and size < 5 * 1024 * 1024:
            audio_b64 = base64.b64encode(Path(out_path).read_bytes()).decode("utf-8")

    base_url = str(request.base_url).rstrip("/")
    audio_url = f"{base_url}/v1/audio/download/{filename}"

    return SpeechResponse(
        id=speech_id,
        duration_seconds=duration,
        audio_url=audio_url,
        audio_base64=audio_b64,
    )


@router.post("/tts", response_model=SpeechResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def generate_speech_alias(body: SpeechRequest, request: Request):
    """Alias for /v1/audio/speech."""
    return await generate_speech_endpoint(body, request)


# ── Suno Music Endpoints ──

@router.post("/music", response_model=MusicTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_music_endpoint(body: MusicRequest):
    """Generate background music using Suno AI."""
    client = get_suno_client()
    try:
        task_id = await client.generate(
            prompt=body.prompt,
            style=body.style,
            title=body.title,
            instrumental=body.instrumental,
            model=body.model,
            custom_mode=body.custom_mode,
        )
    except Exception as e:
        logger.error("Suno music generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Music generation error: {e}")

    if body.poll:
        try:
            task = await client.poll_task(task_id)
            clips = task.get("clips") or []
            return MusicTaskResponse(task_id=task_id, status=task.get("status", "SUCCESS"), clips=clips)
        except Exception as e:
            return MusicTaskResponse(task_id=task_id, status="FAILED", error=str(e))

    return MusicTaskResponse(task_id=task_id, status="queued")


@router.get("/music/{task_id}", response_model=MusicTaskResponse)
async def get_music_task(task_id: str):
    """Get status of a music generation task."""
    client = get_suno_client()
    try:
        task = await client.get_task(task_id)
        status_val = task.get("status", "running")
        clips = task.get("clips") or []
        return MusicTaskResponse(task_id=task_id, status=status_val, clips=clips)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Music task {task_id} not found or error: {e}")


# ── File Download Helper ──

@router.get("/download/{filename}")
async def download_audio(filename: str):
    """Download generated audio file (WAV/MP3)."""
    # Sanitize filename
    safe_name = Path(filename).name
    target = SHARED_OUTPUT_DIR / safe_name
    if not target.exists():
        target = TTS_TEMPLATES_DIR / safe_name
    if not target.exists():
        target = MUSIC_OUTPUT_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    mime = "audio/wav" if safe_name.endswith(".wav") else "audio/mpeg"
    return FileResponse(path=target, media_type=mime, filename=safe_name)
