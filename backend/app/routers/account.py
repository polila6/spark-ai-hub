"""The user's own account: notifications, data export and account deletion (GDPR rights)."""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, session_principal
from ..models import (ApiKey, Artifact, AuditEvent, Conversation, Dataset, Experiment, KbCollection, Memory, Notification, Project,
                      SavedPrompt, SchedClass, SharedResource, User, UserFile, UserSession)
from ..security import verify_password
from ..services import people, storage
from ..services.jobs import JobError, create_job

router = APIRouter(prefix="/api/v1", tags=["account"])


@router.get("/notifications")
def list_notifications(p: Principal = Depends(session_principal), db: Session = Depends(get_db), limit: int = 50):
    rows = db.scalars(select(Notification).where(Notification.user_id == p.id)
                      .order_by(Notification.created_at.desc()).limit(min(limit, 200))).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == p.id,
                                                                             Notification.read_at.is_(None))) or 0
    return {"unread": unread, "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
                                          "data": n.data, "read": n.read_at is not None, "created_at": n.created_at}
                                         for n in rows]}


@router.post("/notifications/{nid}/read")
def read_notification(nid: int, p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.id == nid, Notification.user_id == p.id)
               .values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all(p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None))
               .values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return {"ok": True}


@router.get("/account/content-accesses")
def content_accesses(p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    """Each time an administrator consulted this person's content: who, when, what and why."""
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == people.CONTENT_ACTION, AuditEvent.target_type == people.CONTENT_TARGET,
                                               AuditEvent.target_id == str(p.id)).order_by(AuditEvent.ts.desc()).limit(500)).all()
    return {"accesses": [people.access_public(e) for e in rows], "accepted_at": p.user.content_access_accepted_at}


@router.get("/account/summary")
def account_summary(p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    """What the platform stores about the user (shown in Settings → Privacy)."""
    def count(model, col):
        return db.scalar(select(func.count()).select_from(model).where(col == p.id)) or 0

    pol = storage.effective_policy(db, p.user)
    return {"conversations": count(Conversation, Conversation.owner_id), "files": count(UserFile, UserFile.owner_id),
            "memories": count(Memory, Memory.owner_id), "projects": count(Project, Project.owner_id),
            "collections": count(KbCollection, KbCollection.owner_id), "datasets": count(Dataset, Dataset.owner_id),
            "artifacts": count(Artifact, Artifact.owner_id), "api_keys": count(ApiKey, ApiKey.user_id),
            "used_bytes": storage.user_usage_bytes(db, p.id), "quota_gb": pol.storage_quota_gb}


@router.post("/account/export")
def export_account(p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    try:
        j = create_job(db, p.user, "account_export", {}, title="Exportar mis datos", sched_class=SchedClass.maintenance)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"job_id": str(j.id)}


class DeleteIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirm: str = Field(max_length=32)


def purge_user_data(db: Session, user: User) -> dict:
    """Delete the user's personal content and anonymise the account row (kept for audit integrity)."""
    s = get_settings()
    removed = {}
    for model, col in ((Conversation, Conversation.owner_id), (Memory, Memory.owner_id), (Project, Project.owner_id),
                       (KbCollection, KbCollection.owner_id), (SavedPrompt, SavedPrompt.owner_id),
                       (Experiment, Experiment.owner_id), (UserFile, UserFile.owner_id), (Dataset, Dataset.owner_id),
                       (Artifact, Artifact.owner_id)):
        if model in (Dataset, Artifact):
            for row in db.scalars(select(model).where(col == user.id)):
                try:
                    storage.safe_join(s.data_root, row.path).unlink(missing_ok=True)
                except (storage.StorageError, OSError):
                    pass
        removed[model.__tablename__] = db.execute(delete(model).where(col == user.id)).rowcount
    db.execute(delete(SharedResource).where(SharedResource.grantee_id == user.id))
    shutil.rmtree(s.data_root / "users" / str(user.id), ignore_errors=True)
    now = datetime.now(timezone.utc)
    for k in db.scalars(select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))):
        k.revoked_at = now
    for sess in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
        sess.revoked = True
    user.username = f"deleted-{str(user.id)[:8]}"
    user.email = None
    user.display_name = "Usuario eliminado"
    user.password_hash = None
    user.is_active = False
    user.preferences = {}
    user.signup_reason = None
    db.commit()
    return removed


@router.delete("/account")
def delete_account(body: DeleteIn, request: Request, p: Principal = Depends(session_principal),
                   db: Session = Depends(get_db)):
    if body.confirm not in ("ELIMINAR", "DELETE"):
        raise HTTPException(422, detail={"code": "confirm_required", "message": "Type ELIMINAR to confirm"})
    if not verify_password(p.user.password_hash, body.password):
        raise HTTPException(400, detail={"code": "wrong_password", "message": "Password is incorrect"})
    if p.is_admin and (db.scalar(select(func.count()).select_from(User).where(
            User.role == p.user.role, User.is_active.is_(True))) or 0) <= 1:
        raise HTTPException(409, detail={"code": "last_admin", "message": "The last administrator cannot delete the account"})
    uid = str(p.id)
    removed = purge_user_data(db, p.user)
    audit(db, "account.deleted", actor=None, request=request, target_type="user", target_id=uid, details=removed)
    return {"ok": True, "removed": removed}


_ = uuid


# ------------------------------------------------------------------ Android app download (tailnet only, no login needed)


def _apk():
    p = get_settings().data_root / "apps" / "spark-ai-hub.apk"
    return p if p.exists() else None


@router.get("/app/android/info")
def android_info():
    import hashlib

    p = _apk()
    if not p:
        return {"available": False}
    return {"available": True, "version": "0.2.0", "size_bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "url": "/api/v1/app/android",
            "min_android": "8.0 (API 26)", "package": "es.upv.idf.sparkhub"}


@router.get("/app/android")
def android_apk():
    from fastapi.responses import FileResponse

    p = _apk()
    if not p:
        raise HTTPException(404, detail={"code": "not_built", "message": "The Android app has not been built yet"})
    return FileResponse(p, media_type="application/vnd.android.package-archive", filename="spark-ai-hub.apk")
