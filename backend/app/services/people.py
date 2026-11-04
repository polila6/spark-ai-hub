"""Who is using the hub and what is running, for administrators: usage metadata only.

The terms every user accepted say administrators see usage metadata (tokens, times, space) and never content. So nothing here
carries a chat or job title, a file name or a prompt: job titles can hold file names, and turn titles are the user's words."""
from __future__ import annotations

from datetime import datetime, timedelta

ONLINE_WINDOW = timedelta(minutes=10)  # sessions record activity every 5 minutes at most
ACTIVE_WINDOW = timedelta(days=30)


def presence(last_seen: datetime | None, enabled: bool, now: datetime) -> str:
    if not enabled:
        return "disabled"
    if last_seen is None:
        return "never"
    if now - last_seen <= ONLINE_WINDOW:
        return "online"
    return "active" if now - last_seen <= ACTIVE_WINDOW else "inactive"


def latest(*moments: datetime | None) -> datetime | None:
    known = [m for m in moments if m is not None]
    return max(known) if known else None


def job_public(job) -> dict:
    return {"id": str(job.id), "type": job.type, "status": job.status.value, "progress": job.progress,
            "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at}


def request_public(row) -> dict:
    return {"ts": row.ts, "source": row.source, "endpoint": row.endpoint, "model": row.model, "status_code": row.status_code,
            "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens, "latency_ms": row.latency_ms}
