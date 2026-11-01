"""Job creation, inspection helpers and policy enforcement (shared by API and worker)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Job, JobEvent, JobStatus, Role, SchedClass, User
from ..redis_client import publish, wake_workers
from ..settings_store import get_setting
from .storage import effective_policy

ACTIVE = (JobStatus.queued, JobStatus.preparing, JobStatus.running)
TERMINAL = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled, JobStatus.interrupted)

# Lower value runs first.
CLASS_PRIORITY = {
    SchedClass.interactive: 10,
    SchedClass.maintenance: 20,
    SchedClass.batch: 40,
    SchedClass.scientific: 45,
    SchedClass.image: 50,
    SchedClass.video: 70,
}

# Job types that are safe to retry automatically after an interruption.
IDEMPOTENT_TYPES = {"diagnostic_sleep", "hf_download", "ollama_pull", "smoke_test", "benchmark", "image_generate", "audio_transcribe",
                    "audio_tts", "sci_infer", "backup", "comparison", "file_process", "kb_index", "account_export"}


class JobError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def create_job(db: Session, owner: User, type_: str, params: dict, *, title: str = "",
               sched_class: SchedClass = SchedClass.batch, priority: int | None = None,
               requested_resources: dict | None = None, max_attempts: int = 2) -> Job:
    if owner.role == Role.viewer and type_ not in ("comparison",):
        raise JobError("forbidden", "Viewers cannot submit jobs", 403)
    pol = effective_policy(db, owner)
    if sched_class == SchedClass.video and not pol.allow_video:
        raise JobError("forbidden", "Video generation not allowed by your resource policy", 403)
    queued = db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == owner.id, Job.status.in_(ACTIVE))) or 0
    if queued >= pol.max_queued_jobs:
        raise JobError("queue_limit", f"You already have {queued} active jobs (limit {pol.max_queued_jobs})", 429)
    retention = int(get_setting(db, "job_retention_days") or 90)
    job = Job(
        type=type_,
        owner_id=owner.id,
        status=JobStatus.queued,
        sched_class=sched_class,
        priority=(priority if priority is not None else CLASS_PRIORITY[sched_class]) - (pol.priority_boost or 0),
        title=title[:255],
        params=params,
        requested_resources=requested_resources or {},
        idempotent=type_ in IDEMPOTENT_TYPES,
        max_attempts=max_attempts if type_ in IDEMPOTENT_TYPES else 1,
        retain_until=datetime.now(timezone.utc) + timedelta(days=retention),
    )
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, message=f"Job submitted ({type_})", data={"class": sched_class.value}))
    db.commit()
    wake_workers()
    publish("sah:job-events", {"job_id": str(job.id), "status": "queued"})
    return job


def add_event(db: Session, job_id: uuid.UUID, message: str, level: str = "info", data: dict | None = None) -> None:
    db.add(JobEvent(job_id=job_id, message=message[:8000], level=level, data=data))


def request_cancel(db: Session, job: Job) -> Job:
    if job.status in TERMINAL:
        return job
    if job.status == JobStatus.queued:
        job.status = JobStatus.cancelled
        job.finished_at = datetime.now(timezone.utc)
        add_event(db, job.id, "Cancelled while queued")
    else:
        job.cancel_requested = True
        add_event(db, job.id, "Cancellation requested")
    db.commit()
    wake_workers()
    publish("sah:job-events", {"job_id": str(job.id), "status": job.status.value})
    return job


def job_public(job: Job, include_params: bool = True) -> dict:
    d = {
        "id": str(job.id),
        "type": job.type,
        "title": job.title,
        "owner_id": str(job.owner_id) if job.owner_id else None,
        "status": job.status.value,
        "sched_class": job.sched_class.value,
        "priority": job.priority,
        "progress": job.progress,
        "message": job.message,
        "result": job.result,
        "error": job.error,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "cancel_requested": job.cancel_requested,
        "requested_resources": job.requested_resources,
        "actual_resources": job.actual_resources,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
    if include_params:
        d["params"] = {k: v for k, v in (job.params or {}).items() if k not in ("token", "secret")}
    return d
