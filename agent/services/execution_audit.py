"""Durable routing evidence without storing prompts, tokens, or image bytes."""
import uuid
from contextvars import ContextVar

from agent.db.schema import get_db, _db_lock

current_job: ContextVar[str | None] = ContextVar("flow_job", default=None)


async def start_call(method, installation_id, project_id):
    job_id = current_job.get()
    if not job_id:
        return None
    call_id = str(uuid.uuid4())
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "INSERT INTO execution_log (id, job_id, method, installation_id, project_id, status) "
            "VALUES (?, ?, ?, ?, ?, 'running')",
            (call_id, job_id, method, installation_id, project_id),
        )
        await db.execute("UPDATE request SET installation_id=? WHERE id=?", (installation_id, job_id))
        await db.commit()
    return call_id


async def finish_call(call_id, result):
    if not call_id:
        return
    from agent.worker._parsing import _is_error
    error = _is_error(result)
    message = str(result.get("error") or result.get("data") or "").lower()
    code = next((marker.upper() for marker in (
        "public_error_user_quota_reached", "public_error_per_model_daily_quota_reached",
        "captcha_failed", "no_at_token", "no_flow_tab", "timeout", "extension disconnected",
    ) if marker in message), "PROVIDER_ERROR" if error else None)
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "UPDATE execution_log SET status=?, error_code=?, "
            "finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            ("failed" if error else "returned", code, call_id),
        )
        await db.commit()


async def list_calls(job_id):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM execution_log WHERE job_id=? ORDER BY rowid", (job_id,))
    return [dict(row) for row in await cursor.fetchall()]


async def remember_media(media_id, installation_id, project_id):
    if not media_id or not installation_id:
        return
    db = await get_db()
    async with _db_lock:
        await db.execute(
            "INSERT OR REPLACE INTO media_owner (media_id, installation_id, project_id) VALUES (?, ?, ?)",
            (media_id, installation_id, project_id),
        )
        await db.commit()


async def media_owner(media_id):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM media_owner WHERE media_id=?", (media_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None
