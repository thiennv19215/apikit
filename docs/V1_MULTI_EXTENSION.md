# Apikit V1 Multi-Extension Boundary

## Goal

FlowKit upstream remains the execution/core implementation used by its internal Agent/API
workflow. Apikit V1 is an additional public integration surface for external backend clients.

Apikit adds multi-extension/profile routing for V1 without making Agent callers depend on
that routing policy.

## Runtime flow

```text
FlowKit Agent / Skills / MCP
          |
       /api/*
          |
          v
   FlowKit shared core
          ^
          |
   V1MultiExtensionRouter
          ^
          |
       /v1/*
          ^
          |
   Backend client
```

The Flow client still owns WebSocket connections and provider RPC execution. The V1 routing
boundary owns V1-specific policy:

- choose an eligible extension/profile
- keep `installation_id` affinity between retries
- move away from an exhausted previous executor when inputs can be re-uploaded
- pin UUID-only provider media to the profile/project that owns it
- reject cross-account media combinations
- resolve the Flow project associated with the selected profile

## Compatibility rule

Do not change the public V1 request/response contract when refactoring routing. Backend clients
should continue calling the same `/v1/*` endpoints.

Do not make FlowKit Agent/internal `/api/*` calls depend on `V1MultiExtensionRouter`.

## Upstream sync rule

When syncing `crisng95/flowkit`:

1. update FlowKit core/extension/provider behavior normally;
2. preserve `agent/services/v1_multi_extension.py` as Apikit-owned integration code;
3. adapt the router if upstream changes Flow client internals;
4. keep V1 contract tests and multi-extension routing tests green.

The current implementation intentionally leaves the Flow client's connection registry in place
for runtime compatibility. V1 code should not reach into `_extensions`, `_select_extension`,
or `_batch_project_id` directly; those compatibility calls are isolated inside the V1 routing
boundary.
