"""User administration. Admins manage everyone; managers onboard researchers/viewers and review sign-ups."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import public_base_url
from ..db import get_db
from ..deps import Principal, require_admin, require_manager, session_principal
from ..models import ApiKey, Invitation, Job, JobStatus, ResourcePolicy, Role, User, UserSession
from ..security import new_token, token_digest
from ..services import mailer, notify, storage, tailscale
from .auth import user_public

MANAGEABLE_BY_MANAGER = (Role.researcher, Role.viewer)

router = APIRouter(prefix="/api/v1/users", tags=["users"])

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    display_name: str = Field(default="", max_length=128)
    role: Role = Role.researcher


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    role: Role | None = None
    is_active: bool | None = None


class PolicyIn(BaseModel):
    max_concurrent_jobs: int = Field(ge=0, le=32)
    max_queued_jobs: int = Field(ge=0, le=1000)
    storage_quota_gb: float = Field(ge=0, le=100000)
    api_rpm: int = Field(ge=0, le=100000)
    monthly_token_quota: int | None = Field(default=None, ge=0)
    allow_video: bool = True
    priority_boost: int = Field(default=0, ge=-50, le=50)


def _invite(db: Session, user: User, kind: str, actor: User, hours: int = 72) -> str:
    token = new_token()
    db.add(Invitation(token_hash=token_digest(token), kind=kind, user_id=user.id, created_by=actor.id,
                      expires_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
    db.commit()
    return f"{public_base_url()}/invite/{token}"


def _guard_target(p: Principal, user: User, new_role: Role | None = None) -> None:
    """Managers may only act on researchers/viewers and may not grant elevated roles."""
    if p.is_admin:
        return
    if user.role not in MANAGEABLE_BY_MANAGER or (new_role is not None and new_role not in MANAGEABLE_BY_MANAGER):
        raise HTTPException(403, detail={"code": "admin_required",
                                         "message": "Only administrators can manage administrators and managers"})


def _send_invite(db: Session, user: User, link: str, actor: User, hours: int) -> dict:
    """E-mail the activation link (and the Tailscale share invite) when mail is configured."""
    share, share_source = tailscale.share_link_for(db, user.email)
    out = {"email_sent": False, "email_status": "no_email" if not user.email else "mail_disabled",
           "tailscale_invite": share, "tailscale_source": share_source}
    if user.email:
        row = mailer.send_template(db, "invite", user.email, notify.user_lang(user),
                                   {"inviter": actor.display_name or actor.username, "link": link, "hours": hours,
                                    "username": user.username, "share": share or ""})
        db.commit()
        if row is not None:
            out["email_status"] = row.status
            out["email_sent"] = row.status == "pending"
    return out


@router.get("")
def list_users(p: Principal = Depends(require_manager), db: Session = Depends(get_db), status: str | None = None):
    q = select(User).order_by(User.created_at)
    if status == "pending":
        q = q.where(User.signup_status == "pending")
    users = db.scalars(q).all()
    active_jobs = dict(db.execute(select(Job.owner_id, func.count()).where(
        Job.status.in_([JobStatus.queued, JobStatus.running, JobStatus.preparing])).group_by(Job.owner_id)).all())
    return [{**user_public(u), "active_jobs": active_jobs.get(u.id, 0),
             "used_bytes": storage.user_usage_bytes(db, u.id),
             "manageable": p.is_admin or u.role in MANAGEABLE_BY_MANAGER,
             "locked": bool(u.locked_until and u.locked_until > datetime.now(timezone.utc))} for u in users]


@router.get("/directory")
def directory(_: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    """Minimal list (id, username, display name) used to pick share targets."""
    return [{"id": str(u.id), "username": u.username, "display_name": u.display_name or u.username}
            for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.username))]


@router.post("")
def create_user(body: UserCreate, request: Request, p: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    if not p.is_admin and body.role not in MANAGEABLE_BY_MANAGER:
        raise HTTPException(403, detail={"code": "admin_required", "message": "Only administrators can create admins or managers"})
    username = body.username.strip().lower()
    body.email = body.email.strip().lower() if body.email else None
    if not USERNAME_RE.match(username):
        raise HTTPException(422, detail={"code": "invalid_username", "message": "Use 2-64 chars: a-z 0-9 . _ -"})
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, detail={"code": "username_taken", "message": "Username already exists"})
    if body.email and db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, detail={"code": "email_taken", "message": "Email already exists"})
    user = User(username=username, email=body.email or None, display_name=body.display_name, role=body.role,
                is_active=True, password_hash=None, invited_by=p.id)
    db.add(user)
    db.commit()
    link = _invite(db, user, "invite", p.user)
    sent = _send_invite(db, user, link, p.user, 72)
    audit(db, "users.create", actor=p.user, request=request, target_type="user", target_id=str(user.id),
          details={"username": username, "role": body.role.value, "email_status": sent["email_status"]})
    return {"user": user_public(user), "invitation_url": link, "expires_in_hours": 72, **sent}


@router.post("/{user_id}/invite")
def resend_invite(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_manager),
                  db: Session = Depends(get_db)):
    """New activation link for a user who has not set a password yet (previous links stay valid until expiry)."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    _guard_target(p, user)
    if user.password_hash:
        raise HTTPException(409, detail={"code": "already_active", "message": "User already activated the account"})
    link = _invite(db, user, "invite", p.user)
    sent = _send_invite(db, user, link, p.user, 72)
    audit(db, "users.invite_resent", actor=p.user, request=request, target_type="user", target_id=str(user.id))
    return {"invitation_url": link, "expires_in_hours": 72, **sent}


class DecisionIn(BaseModel):
    role: Role = Role.researcher
    message: str = Field(default="", max_length=2000)


@router.post("/{user_id}/approve")
def approve_signup(user_id: uuid.UUID, body: DecisionIn, request: Request, p: Principal = Depends(require_manager),
                   db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user or user.signup_status != "pending":
        raise HTTPException(404, detail={"code": "not_pending", "message": "No pending request for this user"})
    _guard_target(p, user, body.role)
    user.role, user.is_active, user.signup_status, user.invited_by = body.role, True, None, p.id
    notify.notify(db, user.id, "signup", "Tu cuenta está activa", body.message, link="/", email=False)
    mailer.send_template(db, "signup_approved", user.email, notify.user_lang(user), {"username": user.username})
    db.commit()
    audit(db, "users.signup_approved", actor=p.user, request=request, target_type="user", target_id=str(user.id),
          details={"role": body.role.value})
    return user_public(user)


@router.post("/{user_id}/reject")
def reject_signup(user_id: uuid.UUID, body: DecisionIn, request: Request, p: Principal = Depends(require_manager),
                  db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user or user.signup_status != "pending":
        raise HTTPException(404, detail={"code": "not_pending", "message": "No pending request for this user"})
    user.signup_status, user.is_active = "rejected", False
    mailer.send_template(db, "signup_rejected", user.email, notify.user_lang(user),
                         {"username": user.username, "reason": body.message})
    db.commit()
    audit(db, "users.signup_rejected", actor=p.user, request=request, target_type="user", target_id=str(user.id))
    return user_public(user)


@router.patch("/{user_id}")
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, p: Principal = Depends(require_manager),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    changes = body.model_dump(exclude_unset=True)
    _guard_target(p, user, changes.get("role"))
    if user.id == p.user.id and (changes.get("role") not in (None, Role.admin) or changes.get("is_active") is False):
        raise HTTPException(400, detail={"code": "self_demotion", "message": "You cannot demote or disable yourself"})
    for k, v in changes.items():
        setattr(user, k, v)
    if changes.get("is_active") is False:
        for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
            s.revoked = True
    db.commit()
    audit(db, "users.update", actor=p.user, request=request, target_type="user", target_id=str(user.id),
          details={k: (v.value if isinstance(v, Role) else v) for k, v in changes.items()})
    return user_public(user)


@router.post("/{user_id}/reset-link")
def reset_link(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_manager), db: Session = Depends(get_db),
               send_email: bool = False):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    _guard_target(p, user)
    link = _invite(db, user, "reset", p.user, hours=24)
    user.locked_until = None
    user.failed_logins = 0
    email_status = None
    if send_email and user.email:
        row = mailer.send_template(db, "reset", user.email, notify.user_lang(user),
                                   {"username": user.username, "link": link, "hours": 24})
        email_status = row.status if row else None
    db.commit()
    audit(db, "users.reset_link", actor=p.user, request=request, target_type="user", target_id=str(user.id))
    return {"reset_url": link, "expires_in_hours": 24, "email_status": email_status}


@router.delete("/{user_id}")
def deactivate_user(user_id: uuid.UUID, request: Request, p: Principal = Depends(require_manager), db: Session = Depends(get_db)):
    """Users are deactivated (not deleted) so that ownership of data and audit history is preserved."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    _guard_target(p, user)
    if user.id == p.user.id:
        raise HTTPException(400, detail={"code": "self_delete", "message": "You cannot deactivate yourself"})
    user.is_active = False
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
        s.revoked = True
    for k in db.scalars(select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))):
        k.revoked_at = datetime.now(timezone.utc)
    db.commit()
    audit(db, "users.deactivate", actor=p.user, request=request, target_type="user", target_id=str(user.id))
    return {"ok": True}


@router.get("/policies")
def list_policies(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"scope": r.scope, "target": r.target, "max_concurrent_jobs": r.max_concurrent_jobs,
             "max_queued_jobs": r.max_queued_jobs, "storage_quota_gb": r.storage_quota_gb, "api_rpm": r.api_rpm,
             "monthly_token_quota": r.monthly_token_quota, "allow_video": r.allow_video,
             "priority_boost": r.priority_boost} for r in db.scalars(select(ResourcePolicy))]


@router.put("/policies/{scope}/{target}")
def put_policy(scope: str, target: str, body: PolicyIn, request: Request, p: Principal = Depends(require_admin),
               db: Session = Depends(get_db)):
    if scope not in ("role", "user"):
        raise HTTPException(422, detail={"code": "invalid_scope", "message": "scope must be role|user"})
    if scope == "role" and target not in {r.value for r in Role}:
        raise HTTPException(422, detail={"code": "invalid_role", "message": "Unknown role"})
    if scope == "user":
        try:
            if not db.get(User, uuid.UUID(target)):
                raise ValueError
        except ValueError:
            raise HTTPException(404, detail={"code": "not_found", "message": "User not found"})
    row = db.scalar(select(ResourcePolicy).where(ResourcePolicy.scope == scope, ResourcePolicy.target == target))
    if not row:
        row = ResourcePolicy(scope=scope, target=target)
        db.add(row)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    audit(db, "policies.update", actor=p.user, request=request, target_type="policy", target_id=f"{scope}:{target}",
          details=body.model_dump())
    return {"ok": True}
