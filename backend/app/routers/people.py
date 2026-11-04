"""Monitoring: users active and inactive, what they have running and their usage. Administrators only, metadata only."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, require_admin
from ..models import ApiUsage, Conversation, Job, JobStatus, User, UserFile, UserSession, Workspace, utcnow
from ..redis_client import get_redis
from ..services import people, storage, turns

router = APIRouter(prefix="/api/v1/admin/people", tags=["people"])
LIVE = (JobStatus.queued, JobStatus.preparing, JobStatus.running)


def _running_turns(user_id) -> int:
    """Only how many: the turn's title is what the user wrote."""
    count = 0
    for cid in get_redis().smembers(f"sah:turns:user:{user_id}"):
        info = turns.active(cid.decode() if isinstance(cid, bytes) else cid)
        count += 1 if info and info.get("status") == "running" else 0
    return count


@router.get("")
def list_people(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    now = utcnow()
    week = now - timedelta(days=7)
    seen = dict(db.execute(select(UserSession.user_id, func.max(UserSession.last_seen_at)).group_by(UserSession.user_id)).all())
    used = dict(db.execute(select(ApiUsage.user_id, func.max(ApiUsage.ts)).group_by(ApiUsage.user_id)).all())
    weekly = {row[0]: row[1:] for row in db.execute(
        select(ApiUsage.user_id, func.count(), func.coalesce(func.sum(ApiUsage.prompt_tokens + ApiUsage.completion_tokens), 0))
        .where(ApiUsage.ts >= week).group_by(ApiUsage.user_id)).all()}
    jobs = dict(db.execute(select(Job.owner_id, func.count()).where(Job.status.in_(LIVE)).group_by(Job.owner_id)).all())
    out = []
    for u in db.scalars(select(User).order_by(User.username)):
        last = people.latest(seen.get(u.id), used.get(u.id), u.last_login_at)
        requests, tokens = weekly.get(u.id, (0, 0))
        out.append({"id": str(u.id), "username": u.username, "display_name": u.display_name, "role": u.role.value,
                    "state": people.presence(last, u.is_active, now), "last_seen": last, "requests_7d": requests, "tokens_7d": int(tokens),
                    "jobs_running": jobs.get(u.id, 0), "turns_running": _running_turns(u.id), "used_bytes": storage.user_usage_bytes(db, u.id)})
    return {"users": out, "online_minutes": int(people.ONLINE_WINDOW.total_seconds() // 60), "active_days": people.ACTIVE_WINDOW.days}


@router.get("/{user_id}")
def person(user_id: uuid.UUID, _: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    count = lambda model: db.scalar(select(func.count()).select_from(model).where(model.owner_id == user_id))  # noqa: E731
    live = db.scalars(select(Job).where(Job.owner_id == user_id, Job.status.in_(LIVE)).order_by(Job.created_at.desc()).limit(50)).all()
    past = db.scalars(select(Job).where(Job.owner_id == user_id, Job.status.not_in(LIVE)).order_by(Job.created_at.desc()).limit(15)).all()
    requests = db.scalars(select(ApiUsage).where(ApiUsage.user_id == user_id).order_by(ApiUsage.ts.desc()).limit(50)).all()
    return {"id": str(u.id), "username": u.username, "display_name": u.display_name, "role": u.role.value,
            "turns_running": _running_turns(u.id), "jobs_running": [people.job_public(j) for j in live],
            "jobs_recent": [people.job_public(j) for j in past], "requests": [people.request_public(r) for r in requests],
            "counts": {"conversations": count(Conversation), "files": count(UserFile), "workspaces": count(Workspace)},
            "used_bytes": storage.user_usage_bytes(db, u.id)}
