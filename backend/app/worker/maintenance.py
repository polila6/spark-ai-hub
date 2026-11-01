"""Periodic maintenance run by the worker: scheduled backups and retention policies."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..config import get_settings
from ..db import SessionLocal
from ..logging_setup import log
from ..models import ApiUsage, Job, JobEvent, JobStatus, MetricSample, Role, SchedClass, User
from ..redis_client import get_redis
from ..services.jobs import TERMINAL, create_job
from ..settings_store import get_setting


def _once(key: str, ttl: int) -> bool:
    return bool(get_redis().set(f"sah:once:{key}", "1", nx=True, ex=ttl))


def periodic() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        sched = get_setting(db, "backup_schedule")
        hour = int(get_setting(db, "backup_hour") or 3)
        if sched in ("daily", "weekly") and now.hour == hour and (sched == "daily" or now.weekday() == 6):
            if _once(f"backup:{now:%Y%m%d}", 26 * 3600):
                admin = db.scalar(select(User).where(User.role == Role.admin, User.is_active.is_(True)))
                if admin:
                    create_job(db, admin, "backup", {}, title="Scheduled backup", sched_class=SchedClass.maintenance)
                    log.info("maintenance.backup_scheduled")
        if not _once(f"retention:{now:%Y%m%d%H}", 3600):
            return
        log_days = int(get_setting(db, "log_retention_days") or 30)
        met_days = int(get_setting(db, "metrics_retention_days") or 14)
        db.execute(delete(ApiUsage).where(ApiUsage.ts < now - timedelta(days=log_days)))
        db.execute(delete(MetricSample).where(MetricSample.ts < now - timedelta(days=met_days),
                                              MetricSample.resolution == "1m"))
        db.execute(delete(MetricSample).where(MetricSample.ts < now - timedelta(days=met_days * 6)))
        expired = db.scalars(select(Job).where(Job.retain_until < now, Job.status.in_(TERMINAL))).all()
        s = get_settings()
        for j in expired:
            # Job metadata is kept; bulky event logs and scratch dirs are removed. Artifacts are user data
            # and are never deleted automatically.
            db.execute(delete(JobEvent).where(JobEvent.job_id == j.id))
            shutil.rmtree(s.jobs_dir / str(j.id), ignore_errors=True)
            j.retain_until = None
        db.commit()
        _ = JobStatus
