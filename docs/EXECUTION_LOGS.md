# Execution logs and media ownership

`GET /v1/jobs/{job_id}/executions` returns persisted provider calls in dispatch order:
`id`, `job_id`, `method`, `installation_id`, `project_id`, `started_at`,
`finished_at`, `status`, and `error_code`.

`returned` means the transport returned, not that the job completed. Read the
job status for completion. `failed` records a transport or decoded RPC error.
Calls are recorded before sending. Prompts, tokens, and image bytes are excluded.
The job's `installation_id` is the most recent executor, not a routing preference.
Logs begin with this release; historical jobs cannot be attributed retroactively.

For client v1 generation, raw UUID inputs must have a known profile/project owner.
An unknown UUID fails with `MEDIA_OWNER_UNKNOWN`. Inputs from different profiles
or projects fail with `MEDIA_ACCOUNT_MISMATCH`. Supply original Base64 images to
upload them into a single target profile instead. Old UUIDs without ownership
records must be re-uploaded; ownership is not inferred from UUID syntax.

Base64 sources remain in the job payload across retries. Each attempt selects a
profile, uploads its inputs there (or uses that profile's project-specific cache),
then generates on that same profile. A scoped RPC cannot silently switch profiles.
When a bound profile is unavailable, the job waits rather than reusing its UUIDs
on another profile. This release does not automatically download URL-only sources
or migrate UUID-only inputs to a different account.

Ownership identifies the extension installation and project, not a verified Google
account identity. Changing the signed-in account within an existing profile requires
re-uploading its inputs. Quota prediction remains unavailable on the batch transport.

Database storage in Docker uses `FLOW_DB_PATH=/app/data/flow_agent.db` inside the
persistent `apikit_data` volume. Back up SQLite using its backup API before deployment.
