"""Monitoring: users active and inactive, what they have running and their usage, for administrators.

Content (chats, files, workspaces) only for people who accepted the terms that allow it, with a reason each time. The access is
logged before anything is returned, so if logging fails nothing is read. Each person sees these lines in Settings → Privacy."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, require_admin
from ..models import ApiUsage, Conversation, Job, JobStatus, Message, User, UserFile, UserSession, Workspace, utcnow
from ..redis_client import get_redis
from ..services import files as files_svc
from ..services import people, storage, turns
from ..services import workspace as workspace_svc

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
    mode = people.content_mode(db)
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
                    "jobs_running": jobs.get(u.id, 0), "turns_running": _running_turns(u.id), "used_bytes": storage.user_usage_bytes(db, u.id),
                    "content_consent": people.consented(u, mode)})
    return {"content_mode": mode, "users": out, "online_minutes": int(people.ONLINE_WINDOW.total_seconds() // 60), "active_days": people.ACTIVE_WINDOW.days}


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
            "used_bytes": storage.user_usage_bytes(db, u.id),
            "content_consent": people.consented(u, people.content_mode(db)), "content_mode": people.content_mode(db)}


# ------------------------------------------------------------------ content, with consent and a reason


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)  # required in consent mode (checked against the mode), optional in institutional


class WorkspaceFileIn(ReasonIn):
    path: str = Field(min_length=1, max_length=1024)


def _consenting(db: Session, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    if not people.consented(u, people.content_mode(db)):
        raise HTTPException(403, detail={"code": "content_consent_missing", "message": "This person has not accepted the terms that allow it"})
    return u


def _log(db: Session, request: Request, p: Principal, owner: User, kind: str, what: str, reason: str) -> None:
    mode = people.content_mode(db)
    if not people.reason_ok(mode, reason):
        raise HTTPException(422, detail={"code": "reason_required", "message": f"Write a reason of at least {people.MIN_REASON} characters"})
    audit(db, people.CONTENT_ACTION, actor=p.user, request=request, target_type=people.CONTENT_TARGET, target_id=str(owner.id),
          details={"kind": kind, "what": what[:200], "reason": reason.strip(), "mode": mode})


def _owned(db: Session, model, object_id: uuid.UUID, owner: User):
    row = db.get(model, object_id)
    if row is None or row.owner_id != owner.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "Not found"})
    return row


@router.post("/{user_id}/content")
def content_index(user_id: uuid.UUID, body: ReasonIn, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = _consenting(db, user_id)
    _log(db, request, p, u, "list", "chat, file and workspace names", body.reason)
    chats = db.scalars(select(Conversation).where(Conversation.owner_id == u.id).order_by(Conversation.updated_at.desc()).limit(200)).all()
    files = db.scalars(select(UserFile).where(UserFile.owner_id == u.id).order_by(UserFile.created_at.desc()).limit(500)).all()
    spaces = db.scalars(select(Workspace).where(Workspace.owner_id == u.id).order_by(Workspace.last_used_at.desc())).all()
    return {"conversations": [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at} for c in chats],
            "files": [{"id": str(f.id), "filename": f.filename, "folder": f.folder, "size_bytes": f.size_bytes, "created_at": f.created_at} for f in files],
            "workspaces": [{"id": str(w.id), "name": w.name, "size_bytes": w.size_bytes} for w in spaces]}


@router.post("/{user_id}/conversations/{conversation_id}")
def content_conversation(user_id: uuid.UUID, conversation_id: uuid.UUID, body: ReasonIn, request: Request,
                         p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = _consenting(db, user_id)
    c = _owned(db, Conversation, conversation_id, u)
    _log(db, request, p, u, "conversation", c.title or str(c.id), body.reason)
    rows = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id).limit(2000)).all()
    return {"title": c.title, "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at, "model": m.model_alias,
                                            "attachments": [a.get("name") for a in (m.attachments or [])]} for m in rows]}


def _download(path, filename: str) -> FileResponse:
    """Always as a download: the viewer's browser must not run what someone else uploaded."""
    return FileResponse(path, media_type="application/octet-stream", filename=filename,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.post("/{user_id}/files/{file_id}")
def content_file(user_id: uuid.UUID, file_id: uuid.UUID, body: ReasonIn, request: Request,
                 p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = _consenting(db, user_id)
    f = _owned(db, UserFile, file_id, u)
    path = files_svc.abs_path(f)
    if not path.exists():
        raise HTTPException(410, detail={"code": "file_missing", "message": "File no longer exists"})
    _log(db, request, p, u, "file", f.filename, body.reason)
    return _download(path, f.filename)


@router.post("/{user_id}/workspaces/{workspace_id}")
def content_workspace(user_id: uuid.UUID, workspace_id: uuid.UUID, body: ReasonIn, request: Request,
                      p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = _consenting(db, user_id)
    w = _owned(db, Workspace, workspace_id, u)
    _log(db, request, p, u, "workspace", w.name, body.reason)
    return {"name": w.name, "tree": workspace_svc.tree(w)}


@router.post("/{user_id}/workspaces/{workspace_id}/file")
def content_workspace_file(user_id: uuid.UUID, workspace_id: uuid.UUID, body: WorkspaceFileIn, request: Request,
                           p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = _consenting(db, user_id)
    w = _owned(db, Workspace, workspace_id, u)
    try:
        path, _mime = workspace_svc.raw(w, body.path)
    except Exception as e:  # noqa: BLE001  (outside the workspace, missing, a folder)
        raise HTTPException(404, detail={"code": "not_found", "message": "File not found"}) from e
    _log(db, request, p, u, "workspace_file", f"{w.name}/{body.path}", body.reason)
    return _download(path, path.name)
