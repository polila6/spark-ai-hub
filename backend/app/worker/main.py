"""Background worker: durable job dispatch with resource-aware scheduling.

Job state lives in Postgres; Redis only carries wake-up notifications. On start-up the worker
reconciles jobs left running by a previous process (interrupted → retried only if idempotent).
"""
from __future__ import annotations

import os
import signal
import socket
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..config import get_settings
from ..db import SessionLocal
from ..logging_setup import log, setup_logging
from ..models import Job, JobStatus, User
from ..redis_client import get_redis, publish
from ..services import scheduler
from ..services.jobs import add_event
from ..services.storage import effective_policy, ensure_layout
from .context import Cancelled, JobContext
from .handlers import HANDLERS

# Unique per process start: the container hostname and pid 1 are identical across restarts.
WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
MAX_THREADS = int(os.environ.get("SAH_WORKER_THREADS", "6"))
TYPE_LIMITS = {"hf_download": 2, "ollama_pull": 2}
HEARTBEAT_S = 10
STALE_S = 45

_stop = threading.Event()
_running: dict = {}
_lock = threading.Lock()


def utcnow():
    return datetime.now(timezone.utc)


def reconcile_jobs() -> None:
    """Interrupt/re-queue jobs whose worker stopped heart-beating (runs at start-up and periodically)."""
    cutoff = utcnow() - timedelta(seconds=STALE_S)
    with _lock:
        mine = set(_running.keys())
    with SessionLocal() as db:
        stale = [j for j in db.scalars(select(Job).where(
            Job.status.in_([JobStatus.preparing, JobStatus.running]),
            (Job.heartbeat_at.is_(None)) | (Job.heartbeat_at < cutoff))).all() if j.id not in mine]
        for j in stale:
            if j.idempotent and j.attempts < j.max_attempts and not j.cancel_requested:
                j.status = JobStatus.queued
                j.message = "Re-queued after worker restart"
                add_event(db, j.id, "Worker restarted while job was running; re-queued (idempotent)", "warning")
            else:
                j.status = JobStatus.interrupted
                j.finished_at = utcnow()
                j.error = "Interrupted by worker restart" + ("" if j.idempotent else " (not retried: non-idempotent)")
                add_event(db, j.id, j.error, "warning")
            scheduler.release(j.id)
        db.commit()
        if stale:
            log.info("worker.reconciled_jobs", count=len(stale))


def _heartbeat() -> None:
    while not _stop.wait(HEARTBEAT_S):
        with _lock:
            ids = list(_running.keys())
        if not ids:
            continue
        try:
            with SessionLocal() as db:
                for j in db.scalars(select(Job).where(Job.id.in_(ids))):
                    j.heartbeat_at = utcnow()
                db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.heartbeat_failed", error=str(e))


def _maintenance() -> None:
    while not _stop.wait(30):
        try:
            reconcile_jobs()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.reconcile_failed", error=str(e))
        try:
            from ..services import mailer

            with SessionLocal() as db:
                mailer.drain(db)
        except Exception as e:  # noqa: BLE001
            log.warning("worker.mail_failed", error=str(e))
        try:
            scheduler.sync_ollama()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.ollama_sync_failed", error=str(e))
        try:
            scheduler.ensure_resident()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.resident_failed", error=str(e))
        try:
            scheduler.sweep_idle()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.sweep_failed", error=str(e))
        try:
            from ..services import tasks

            tasks.dispatch_due()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.tasks_failed", error=str(e))
        try:
            _connectors_due()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.connectors_failed", error=str(e))
        try:
            from .maintenance import periodic

            periodic()
        except Exception as e:  # noqa: BLE001
            log.warning("worker.periodic_failed", error=str(e))


def _connectors_due() -> None:
    from ..models import Job, JobStatus, KbConnector, SchedClass, User
    from ..services import connectors
    from ..services.jobs import JobError, create_job

    with SessionLocal() as db:
        for c in connectors.due(db):
            busy = db.query(Job).filter(Job.type == "connector_sync", Job.status.in_([JobStatus.queued, JobStatus.running]),
                                        Job.params["connector_id"].astext == str(c.id)).count()
            if busy:
                continue
            try:
                create_job(db, db.get(User, c.owner_id), "connector_sync", {"connector_id": str(c.id)},
                           title=f"Sincronizar {c.name}", sched_class=SchedClass.batch)
            except JobError as e:
                c.status, c.status_message = "error", e.message
                db.commit()
        _ = KbConnector


def _execute(job_id) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        ctx = JobContext(job)
        handler = HANDLERS[job.type]
    status, error = JobStatus.completed, None
    t0 = time.time()
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.running
            job.heartbeat_at = utcnow()
            db.commit()
        publish("sah:job-events", {"job_id": str(job_id), "status": "running"})
        handler.run(ctx)
        if ctx.cancelled():
            status = JobStatus.cancelled
    except Cancelled:
        status = JobStatus.cancelled
    except Exception as e:  # noqa: BLE001
        status = JobStatus.failed
        error = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
        rem = getattr(e, "remediation", None)
        ctx.log(f"Failed: {error}", "error", {"traceback": traceback.format_exc()[-6000:],
                                              "remediation": (rem or "")[:6000]})
        log.error("worker.job_failed", job=str(job_id), type=ctx.type, error=error)
    finally:
        try:
            handler.cleanup(ctx)
        except Exception as e:  # noqa: BLE001
            log.warning("worker.cleanup_failed", job=str(job_id), error=str(e))
        scheduler.release(job_id)
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = status
            job.error = error
            job.finished_at = utcnow()
            if status == JobStatus.completed:
                job.progress = 1.0
            job.actual_resources = {**(job.actual_resources or {}), **ctx.actual,
                                    "wall_seconds": round(time.time() - t0, 2)}
            add_event(db, job_id, f"Job {status.value}", "error" if status == JobStatus.failed else "info")
            db.commit()
        publish("sah:job-events", {"job_id": str(job_id), "status": status.value})
        with _lock:
            _running.pop(job_id, None)


def _dispatch(pool: ThreadPoolExecutor) -> None:
    with SessionLocal() as db:
        queued = db.scalars(select(Job).where(Job.status == JobStatus.queued)
                            .order_by(Job.priority, Job.created_at).limit(100)).all()
        running_by_owner = dict(db.execute(select(Job.owner_id, func.count()).where(
            Job.status.in_([JobStatus.preparing, JobStatus.running])).group_by(Job.owner_id)).all())
        running_by_type = dict(db.execute(select(Job.type, func.count()).where(
            Job.status.in_([JobStatus.preparing, JobStatus.running])).group_by(Job.type)).all())
        candidates = []
        for j in queued:
            if j.type not in HANDLERS:
                j.status = JobStatus.failed
                j.error = f"No handler for job type '{j.type}'"
                j.finished_at = utcnow()
                continue
            owner = db.get(User, j.owner_id) if j.owner_id else None
            if owner is not None:
                pol = effective_policy(db, owner)
                if running_by_owner.get(j.owner_id, 0) >= max(pol.max_concurrent_jobs, 1):
                    _set_wait(j, f"Waiting: per-user concurrency limit ({pol.max_concurrent_jobs})")
                    continue
            if running_by_type.get(j.type, 0) >= TYPE_LIMITS.get(j.type, 99):
                _set_wait(j, f"Waiting: max {TYPE_LIMITS[j.type]} concurrent {j.type} jobs")
                continue
            # Count the candidate immediately so several jobs submitted together cannot exceed the limits.
            running_by_owner[j.owner_id] = running_by_owner.get(j.owner_id, 0) + 1
            running_by_type[j.type] = running_by_type.get(j.type, 0) + 1
            candidates.append(j.id)
        db.commit()
    for jid in candidates:
        with _lock:
            if len(_running) >= MAX_THREADS:
                return
        with SessionLocal() as db:
            job = db.get(Job, jid)
            if job.status != JobStatus.queued:
                continue
            handler = HANDLERS[job.type]
            try:
                req = handler.requirements(db, job)
            except Exception as e:  # noqa: BLE001
                job.status = JobStatus.failed
                job.error = f"Cannot estimate resources: {e}"
                job.finished_at = utcnow()
                db.commit()
                continue
            job.requested_resources = {**(job.requested_resources or {}), "memory_gb": req.memory_gb,
                                       "gpu_heavy": req.gpu_heavy, "basis": req.note}
            db.commit()
            sched_class = job.sched_class.value
            owner_id = job.owner_id
        if req.memory_gb > 0 or req.gpu_heavy:
            ok, reason = scheduler.try_reserve("job", jid, req.memory_gb, gpu_heavy=req.gpu_heavy,
                                               sched_class=sched_class, note=f"job {jid}")
            if not ok:
                with SessionLocal() as db:
                    _set_wait(db.get(Job, jid), f"Waiting for resources: {reason}")
                    db.commit()
                continue
        with SessionLocal() as db:
            job = db.get(Job, jid)
            job.status = JobStatus.preparing
            job.attempts += 1
            job.started_at = job.started_at or utcnow()
            job.worker_id = WORKER_ID
            job.heartbeat_at = utcnow()
            job.message = None
            add_event(db, jid, f"Started on {WORKER_ID} (attempt {job.attempts})")
            db.commit()
        with _lock:
            _running[jid] = pool.submit(_execute, jid)
        log.info("worker.started_job", job=str(jid), owner=str(owner_id))


def _set_wait(job: Job, msg: str) -> None:
    if job.message != msg:
        job.message = msg


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level, "worker")
    ensure_layout()
    log.info("worker.start", worker=WORKER_ID, threads=MAX_THREADS)
    reconcile_jobs()
    try:
        scheduler.reconcile()
    except Exception as e:  # noqa: BLE001
        log.warning("worker.reconcile_failed", error=str(e))
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    threading.Thread(target=_heartbeat, daemon=True).start()
    threading.Thread(target=_maintenance, daemon=True).start()
    pubsub = get_redis().pubsub()
    pubsub.subscribe("sah:jobs")
    pool = ThreadPoolExecutor(max_workers=MAX_THREADS, thread_name_prefix="job")
    while not _stop.is_set():
        try:
            open("/tmp/worker-alive", "w").write(str(time.time()))
            _dispatch(pool)
        except Exception as e:  # noqa: BLE001
            log.error("worker.dispatch_error", error=str(e), tb=traceback.format_exc()[-2000:])
        try:
            pubsub.get_message(timeout=3.0)
        except Exception:  # noqa: BLE001
            time.sleep(3)
    log.info("worker.stopping")
    pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
