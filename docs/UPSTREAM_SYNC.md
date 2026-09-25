# Upstream FlowKit Sync Policy

## Upstream source

The upstream implementation is `crisng95/flowkit`.

Apikit should continue receiving upstream FlowKit changes for shared implementation code:
Flow transport, Chrome extension, generation/provider services, workers, review tooling,
dashboard behavior, tests, and supporting setup tooling.

## Apikit V1 ownership

The V1 API is owned by Apikit and is not replaced by upstream FlowKit.

Protected V1 surfaces:

- `agent/api/v1/**`
- `flowkit_client.py`
- `flowkit_mcp.py`
- `V1_ENDPOINTS.md`
- `docs/CLIENT_V1.md`
- `docs/V1_ENDPOINTS.md`
- V1 contract/error/job tests such as `tests/unit/test_v1_*.py`

V1 should call the shared FlowKit core/services. Do not fork or duplicate Flow transport,
provider payload, reCAPTCHA, polling, or generation internals inside V1.

When upstream changes those internals, update the shared core normally and only adapt the
V1 adapter/service boundary when required. Preserve the external V1 request/response
contract unless the change is intentionally a V1 API change.

## Shared files

Some shared files also contain Apikit-specific behavior, including multi-profile/quota
failover, deployment integration, and V1 router registration. Merge upstream changes into
those files; do not replace them wholesale if that would remove Apikit behavior.

## Required sync checks

1. Read repository docs and agent instructions before changing code.
2. Compare against the latest upstream `main`.
3. Merge upstream core changes.
4. Confirm the protected V1 surfaces did not change unexpectedly.
5. Run upstream/core tests and V1 contract tests.
6. Update docs in the same change when provider behavior, API contracts, orchestration,
   storage, architecture, or deployment behavior changes.

## Baseline

This policy was established while syncing against
`crisng95/flowkit@bc051f2308db050b0d987b696c843664d483b515` and updated to
`crisng95/flowkit@cd9f156 (v1.2.1)` (bypass `extension_hijack_detected` + Trusted Types CSP).


## V1 multi-extension ownership

Apikit's multi-extension/profile routing policy for external V1 clients lives behind
`agent/services/v1_multi_extension.py`. V1 controllers and V1 worker dispatch should use
that boundary instead of reading FlowClient private connection state directly.

FlowKit Agent/internal `/api/*` behavior must not be coupled to this V1 routing layer.
The underlying Flow client may retain connection-registry compatibility needed by the current
runtime, but V1-specific selection, quota affinity and media-owner routing belong to the
Apikit-owned V1 boundary.

See `docs/V1_MULTI_EXTENSION.md`.
