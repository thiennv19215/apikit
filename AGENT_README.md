# FlowKit — Agent Integration & Quickstart Guide

This package contains the full **FlowKit AI Filmmaking Automation Engine**, designed to be directly discovered, invoked, and controlled by AI Agents (Antigravity, Claude Code, Gemini CLI, Cursor, OpenAI Codex, AutoGen, CrewAI).

---

## 1. Quickstart (3 Steps)

### Step 1: Install dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Start the FlowKit backend server
```bash
python -m agent.main
# Listens on http://127.0.0.1:8100 and WebSocket ws://127.0.0.1:8100/ws
```

### Step 3: Load the Chrome Extension
1. Open Google Chrome and go to `chrome://extensions`.
2. Enable **Developer mode** (top-right).
3. Click **Load unpacked** and select the `extension/` directory.
4. Navigate to `https://labs.google/fx/tools/flow` and sign in.
5. Verify health check returns:
```bash
curl -s http://127.0.0.1:8100/health
# Expect: {"status": "ok", "extension_connected": true, ...}
```

---

## 2. Four Ways AI Agents Can Invoke FlowKit

### Way 1: Open Standard Agent Skills (`.agents/skills/` or `/fk-<name>`)
FlowKit ships with **36 modular skills**:
- **Antigravity / Cursor / Codex:** Standard `.agents/skills/fk-<name>/SKILL.md` files with YAML frontmatter.
- **Claude Code:** Slash commands `/fk-<name>` in `.claude/commands/`.
- **Gemini CLI:** Commands in `.gemini/commands/fk/` and documented in `GEMINI.md`.

Example skills:
- `/fk-pipeline`: Smart full-pipeline orchestrator
- `/fk-create-project`: Scaffold a project with entities, materials, and scenes
- `/fk-gen-refs`: Generate character/location visual reference images
- `/fk-gen-images`: Generate scene initial frames
- `/fk-gen-videos`: Generate scene videos with Veo 3 / Omni Flash
- `/fk-gen-narrator`: AI voiceover narration via ElevenLabs/TTS
- `/fk-concat`: Concatenate all scene clips into a single movie file

### Way 2: Python Client SDK (`flowkit_client.py`)
Agents can import `FlowKitClient` and run high-level Python commands:
```python
from flowkit_client import FlowKitClient

client = FlowKitClient()
client.health()

# Create project
proj = client.create_project(name="Cyberpunk 2099", material="realistic", story="A futuristic thriller")
pid = proj["id"]

# Create entity
char = client.create_character(pid, name="Kaelen", entity_type="character",
                               image_prompt="Portrait of cybernetic detective with blue optic lens")

# Generate references & poll until complete
client.batch_generate_refs(pid)
client.poll_batch(project_id=pid, req_type="GENERATE_REF_IMAGE")
```

### Way 3: Model Context Protocol Server (`flowkit_mcp.py`)
Configure FlowKit as an MCP server in Claude Desktop, Cursor, Antigravity, or Zed:
```json
{
  "mcpServers": {
    "flowkit": {
      "command": "python",
      "args": ["<path_to_repo>/flowkit_mcp.py"]
    }
  }
}
```
Exposes native tools: `flowkit_health`, `flowkit_create_project`, `flowkit_create_character`, `flowkit_create_scene`, `flowkit_batch_generate_*`, `flowkit_poll_batch`, `flowkit_v1_generate_video`, etc.

### Way 4: REST Batch API
Endpoints are documented in `docs/AGENT_API_INTERNAL.md` and `docs/CLIENT_V1.md`.
Batch queue endpoint: `POST /api/requests/batch` with aggregate polling at `GET /api/requests/batch-status`.

---

## 3. Critical Rules for AI Agents

1. **Media ID is always UUID:** `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Never use `CAMS...` strings.
2. **Scene Prompts = ACTION ONLY:** Never describe character appearance in scene prompts. Reference images ensure visual consistency.
3. **References must exist before scenes:** Verify all entities have `media_id` before starting scene generation.
4. **No throwaway scripts:** Never loop curl requests. Submit all items via `POST /api/requests/batch`; server throttles (max 5 concurrent, 10s cooldown).
5. **Video Prompts use sub-clip timing:** Structure 8s video prompt into segments: `0-3s: [action]. 3-6s: [action]. 6-8s: [action].`
