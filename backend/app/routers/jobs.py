"""Jobs API (owner-scoped; admins see all) with server-sent event updates."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, current_principal, require_researcher
from ..models import Artifact, Job, JobEvent, JobStatus, SchedClass
from ..redis_client import get_redis
from ..services.jobs import TERMINAL, JobError, create_job, job_public, request_cancel

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def get_job_for(db: Session, p: Principal, job_id: uuid.UUID) -> Job:
    j = db.get(Job, job_id)
    if not j or (j.owner_id != p.id and not p.is_admin):
        raise HTTPException(404, detail={"code": "not_found", "message": "Job not found"})
    return j


@router.get("")
def list_jobs(p: Principal = Depends(current_principal), db: Session = Depends(get_db),
              status: str | None = None, type: str | None = None, all_users: bool = False,
              limit: int = Query(50, le=500), offset: int = 0):
    q = select(Job).order_by(Job.created_at.desc())
    if not (all_users and p.is_admin):
        q = q.where(Job.owner_id == p.id)
    if status:
        q = q.where(Job.status.in_([JobStatus(s) for s in status.split(",")]))
    if type:
        q = q.where(Job.type == type)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return {"total": total, "items": [job_public(j, include_params=False) for j in rows]}


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    j = get_job_for(db, p, job_id)
    d = job_public(j)
    d["artifacts"] = [{"id": str(a.id), "kind": a.kind, "filename": a.filename, "mime": a.mime,
                       "size_bytes": a.size_bytes, "url": f"/api/v1/media/artifacts/{a.id}/file", "meta": a.meta}
                      for a in db.scalars(select(Artifact).where(Artifact.job_id == j.id))]
    if j.status == JobStatus.queued:
        ahead = db.scalar(select(func.count()).select_from(Job).where(
            Job.status == JobStatus.queued, (Job.priority < j.priority) |
            ((Job.priority == j.priority) & (Job.created_at < j.created_at)))) or 0
        d["queue_position"] = ahead + 1
    return d


@router.get("/{job_id}/events")
def job_events(job_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
               after: int = 0):
    get_job_for(db, p, job_id)
    rows = db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after).order_by(JobEvent.id)
                      .limit(1000)).all()
    return [{"id": e.id, "ts": e.ts, "level": e.level, "message": e.message,
             "data": e.data if p.is_admin else {k: v for k, v in (e.data or {}).items() if k != "traceback"}}
            for e in rows]


@router.post("/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    j = get_job_for(db, p, job_id)
    request_cancel(db, j)
    if j.owner_id != p.id:
        audit(db, "jobs.admin_cancel", actor=p.user, request=request, target_type="job", target_id=str(j.id))
    return job_public(j)


@router.post("/{job_id}/retry")
def retry_job(job_id: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    j = get_job_for(db, p, job_id)
    if j.status not in (JobStatus.failed, JobStatus.interrupted, JobStatus.cancelled):
        raise HTTPException(409, detail={"code": "not_retryable", "message": "Only finished unsuccessful jobs can be retried"})
    if j.type in ("hf_download", "ollama_pull"):
        raise HTTPException(409, detail={"code": "use_downloads", "message": "Resume downloads from the Downloads page"})
    try:
        nj = create_job(db, p.user, j.type, j.params, title=j.title, sched_class=j.sched_class)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"job_id": str(nj.id)}


class TestJobIn(BaseModel):
    seconds: int = Field(default=20, ge=1, le=600)
    memory_gb: float = Field(default=0, ge=0, le=64)


@router.post("/test")
def submit_test_job(body: TestJobIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    """Diagnostic job that sleeps while holding an optional reservation (scheduler/cancellation tests)."""
    try:
        j = create_job(db, p.user, "diagnostic_sleep", body.model_dump(), title=f"Diagnostic {body.seconds}s",
                       sched_class=SchedClass.batch)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return job_public(j)


@router.get("/stream/updates")
async def stream(request: Request, p: Principal = Depends(current_principal)):
    """SSE of job status/progress events for the caller's jobs (admins: all)."""
    from ..db import SessionLocal

    async def gen():
        pubsub = get_redis().pubsub()
        await asyncio.to_thread(pubsub.subscribe, "sah:job-events")
        owned: dict[str, bool] = {}
        try:
            while not await request.is_disconnected():
                msg = await asyncio.to_thread(pubsub.get_message, True, 2.0)
                if not msg or msg.get("type") != "message":
                    yield ": keep-alive\n\n"
                    continue
                import json

                ev = json.loads(msg["data"])
                jid = ev.get("job_id")
                if not p.is_admin:
                    if jid not in owned:
                        with SessionLocal() as db:
                            j = db.get(Job, uuid.UUID(jid))
                            owned[jid] = bool(j and j.owner_id == p.id)
                    if not owned[jid]:
                        continue
                yield f"data: {msg['data']}\n\n"
        finally:
            pubsub.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache",
                                                                             "X-Accel-Buffering": "no"})


_ = TERMINAL
