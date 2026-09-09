"""FlowKit MCP Server.

Exposes FlowKit's AI filmmaking automation capabilities as standard Model Context
Protocol (MCP) tools for Antigravity, Claude Desktop, Cursor, Windsurf, Zed, etc.

To configure in Claude Desktop / Antigravity / Cursor:
{
  "mcpServers": {
    "flowkit": {
      "command": "python",
      "args": ["<path_to_repo>/flowkit_mcp.py"]
    }
  }
}
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

# Support both mcp 2.x and mcp 1.x
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        MCPServer = None

from flowkit_client import FlowKitClient, FlowKitError

client = FlowKitClient()

if MCPServer is None:
    print("Error: 'mcp' package not found. Install it with: pip install mcp", file=sys.stderr)
    sys.exit(1)

app = MCPServer("flowkit")


@app.tool()
def flowkit_health() -> str:
    """Check status of FlowKit server, extension connection, and Google Flow tab state."""
    try:
        data = client.health()
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e), "hint": "Ensure FlowKit is running: python -m agent.main"})


@app.tool()
def flowkit_list_accounts() -> str:
    """List all connected browser profiles/extensions with quota availability and Flow tab status."""
    try:
        data = client.list_accounts()
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_reset_account_quota(installation_id: str) -> str:
    """Reset quota status for a specific browser profile/extension."""
    try:
        data = client.reset_account_quota(installation_id)
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_reset_all_accounts_quota() -> str:
    """Reset quota status for all connected browser profiles/extensions."""
    try:
        data = client.reset_all_accounts_quota()
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_list_materials() -> str:
    """List all available image styles (materials) like realistic, 3d_pixar, anime, stop_motion, minecraft."""
    try:
        mats = client.list_materials()
        return json.dumps(mats, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_create_project(name: str, material: str, story: str = "") -> str:
    """Create a new video project and set it as active.
    
    Args:
        name: Project title
        material: Visual style (e.g. realistic, 3d_pixar, anime)
        story: Brief storyline summary
    """
    try:
        proj = client.create_project(name=name, material=material, story=story)
        return json.dumps(proj, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_create_character(
    project_id: str,
    name: str,
    entity_type: str = "character",
    image_prompt: str = "",
    description: str = "",
    voice_description: str = "",
) -> str:
    """Create a character or location entity and attach it to a project.
    
    Args:
        project_id: Project UUID
        name: Entity name/alias in English (e.g. Luna, The Commander)
        entity_type: 'character', 'location', or 'asset'
        image_prompt: Visual prompt for generating reference image
        description: Character bio or details
        voice_description: Voice characteristics for TTS
    """
    try:
        char = client.create_character(
            project_id=project_id,
            name=name,
            entity_type=entity_type,
            image_prompt=image_prompt,
            description=description,
            voice_description=voice_description,
        )
        return json.dumps(char, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_create_video(project_id: str, title: str = "Episode 1", aspect_ratio: str = "VERTICAL") -> str:
    """Create a new video episode in a project.
    
    Args:
        project_id: Project UUID
        title: Video episode title
        aspect_ratio: 'VERTICAL' (9:16 Shorts/Reels) or 'HORIZONTAL' (16:9 YouTube)
    """
    try:
        vid = client.create_video(project_id=project_id, title=title, aspect_ratio=aspect_ratio)
        return json.dumps(vid, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_create_scene(
    video_id: str,
    scene_number: int,
    prompt: str,
    video_prompt: str = "",
    narrator_text: str = "",
    character_names: str = "",
    chain_type: str = "FIRST_FRAME",
    parent_scene_id: Optional[str] = None,
) -> str:
    """Create a scene in a video.
    
    CRITICAL RULES:
    - prompt: ACTION ONLY. Never describe character appearance here (reference images handle consistency).
    - video_prompt: Sub-clip timed format: '0-3s: [action]. 3-6s: [action]. 6-8s: [action].'

    Args:
        video_id: Video UUID
        scene_number: 1, 2, 3...
        prompt: Action-only scene image prompt
        video_prompt: Sub-clip timed video prompt
        narrator_text: Voiceover narration or speech text
        character_names: Comma-separated names of entities in this scene (e.g. 'Luna, Ray')
        chain_type: 'FIRST_FRAME', 'START_END_FRAME', or 'CONTINUATION'
        parent_scene_id: Previous scene UUID if chaining
    """
    try:
        chars = [c.strip() for c in character_names.split(",") if c.strip()] if character_names else []
        sc = client.create_scene(
            video_id=video_id,
            scene_number=scene_number,
            prompt=prompt,
            video_prompt=video_prompt,
            narrator_text=narrator_text,
            character_names=chars,
            chain_type=chain_type,
            parent_scene_id=parent_scene_id,
        )
        return json.dumps(sc, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_batch_generate_refs(project_id: str) -> str:
    """Batch submit reference image generation for all entities in a project."""
    try:
        res = client.batch_generate_refs(project_id=project_id)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_batch_generate_scene_images(video_id: str, orientation: str = "VERTICAL") -> str:
    """Batch submit scene image generation for all scenes in a video."""
    try:
        res = client.batch_generate_scene_images(video_id=video_id, orientation=orientation)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_batch_generate_scene_videos(video_id: str, orientation: str = "VERTICAL") -> str:
    """Batch submit video generation for all scenes in a video."""
    try:
        res = client.batch_generate_scene_videos(video_id=video_id, orientation=orientation)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_get_batch_status(
    project_id: Optional[str] = None,
    video_id: Optional[str] = None,
    req_type: Optional[str] = None,
) -> str:
    """Check aggregate status of batch requests.
    
    Returns total, pending, processing, completed, failed, and done flag.
    """
    try:
        status = client.get_batch_status(project_id=project_id, video_id=video_id, req_type=req_type)
        return json.dumps(status, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_poll_batch(
    project_id: Optional[str] = None,
    video_id: Optional[str] = None,
    req_type: Optional[str] = None,
    interval: int = 10,
    timeout: int = 600,
) -> str:
    """Poll batch status until all requests are done or timeout occurs."""
    try:
        status = client.poll_batch(
            project_id=project_id,
            video_id=video_id,
            req_type=req_type,
            interval=float(interval),
            timeout=float(timeout),
        )
        return json.dumps(status, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_generate_narrator(video_id: str, voice_template_id: Optional[str] = None) -> str:
    """Generate AI TTS voiceover for all scenes with narrator_text in a video."""
    try:
        res = client.generate_narrator(video_id=video_id, voice_template_id=voice_template_id)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_v1_generate_video(
    prompt: str,
    start_frame_base64: str,
    end_frame_base64: Optional[str] = None,
    duration_seconds: int = 8,
    aspect_ratio: str = "9:16",
) -> str:
    """Invoke Gemini Omni Flash video generation directly via Client V1 API.
    
    Args:
        prompt: Motion description
        start_frame_base64: First frame image as Base64 string
        end_frame_base64: Optional last frame image as Base64 string for Start+End transition
        duration_seconds: 4, 6, 8, or 10
        aspect_ratio: '9:16' (Portrait) or '16:9' (Landscape)
    """
    try:
        input_images = [{"image_base64": start_frame_base64, "mime_type": "image/jpeg", "role": "start_frame"}]
        if end_frame_base64:
            input_images.append({"image_base64": end_frame_base64, "mime_type": "image/jpeg", "role": "end_frame"})

        res = client.v1_generate_video(
            prompt=prompt,
            input_images=input_images,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
        )
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_v1_generate_image(
    prompt: str,
    aspect_ratio: str = "16:9",
    image_base64: Optional[str] = None,
) -> str:
    """Generate image using Banana Pro/2 via Client V1 API."""
    try:
        inputs = [{"image_base64": image_base64, "mime_type": "image/jpeg"}] if image_base64 else []
        res = client.v1_generate_image(prompt=prompt, aspect_ratio=aspect_ratio, input_images=inputs)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_v1_generate_speech(
    text: str,
    voice_id: Optional[str] = None,
    speed: float = 1.0,
    instruct: Optional[str] = None,
) -> str:
    """Generate voiceover narration audio using TTS via Client V1 API."""
    try:
        res = client.v1_generate_speech(text=text, voice_id=voice_id, speed=speed, instruct=instruct)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_v1_generate_music(
    prompt: str,
    style: str = "",
    title: str = "",
    instrumental: bool = True,
) -> str:
    """Generate background soundtrack using Suno AI via Client V1 API."""
    try:
        res = client.v1_generate_music(prompt=prompt, style=style, title=title, instrumental=instrumental)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_v1_concat_videos(
    video_urls: list[str],
    narration_audio_url: Optional[str] = None,
    music_url: Optional[str] = None,
    narration_volume: float = 1.0,
    music_volume: float = 0.3,
) -> str:
    """Concatenate video clips and mix narration/music into final video file via Client V1 API."""
    try:
        res = client.v1_concat_videos(
            video_urls=video_urls,
            narration_audio_url=narration_audio_url,
            music_url=music_url,
            narration_volume=narration_volume,
            music_volume=music_volume,
        )
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@app.tool()
def flowkit_v1_poll_job(job_id: str, interval: float = 10.0, timeout: float = 900.0) -> str:
    """Poll a Client V1 generation job until status is complete or failed."""
    try:
        job = client.poll_job(job_id=job_id, interval=interval, timeout=timeout)
        return json.dumps(job, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def main():
    """Run MCP server stdio loop."""
    app.run()


if __name__ == "__main__":
    main()
