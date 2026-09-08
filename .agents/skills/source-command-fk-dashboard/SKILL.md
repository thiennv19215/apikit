---
name: "source-command-fk-dashboard"
description: "Migrated source command `fk-dashboard`"
---

# source-command-fk-dashboard

Use this skill when the user asks to run the migrated source command `fk-dashboard`.

## Command Template

Show live GLA status in Codex statusline.

Usage: `/fk-dashboard`

The GLA statusline shows real-time project status at the bottom of Codex:

```
Opus 4.6 (1M ctx) ctx:14% rl:18%/5h 67%/7d | GLA: ✓ext Operation Hormu 40sc img:40 vid:40 4K:26 ▶0/5
```

## What it shows

- **OMC info**: model, context usage, rate limits (5h & 7d)
- **GLA info**: extension status, project name, scene count, image/video/4K progress, worker slots

## Setup

Statusline is auto-configured by `bash setup.sh`. To manually configure:

```bash
# In .Codex/settings.local.json, add:
{
  "statusLine": {
    "type": "command",
    "command": "<project_root>/scripts/statusline.sh"
  }
}
```

Requires `jq` and `curl` installed.
