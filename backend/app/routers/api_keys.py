"""Personal API keys and usage statistics."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, session_principal
from ..models import ApiKey, ApiUsage, Role
from ..security import new_api_key
from ..services.storage import effective_policy

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

VALID_SCOPES = {"inference", "jobs"}


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_in_days: int | None = Field(default=90, ge=1, le=730)
    scopes: list[str] = ["inference"]
    rpm_limit: int | None = Field(default=None, ge=1, le=100000)
    monthly_token_quota: int | None = Field(default=None, ge=1)


def _pub(k: ApiKey) -> dict:
    now = datetime.now(timezone.utc)
    return {"id": str(k.id), "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "created_at": k.created_at,
            "expires_at": k.expires_at, "revoked_at": k.revoked_at, "last_used_at": k.last_used_at,
            "rpm_limit": k.rpm_limit, "monthly_token_quota": k.monthly_token_quota,
            "active": not k.revoked_at and (not k.expires_at or k.expires_at > now),
            "user_id": str(k.user_id)}


@router.get("")
def list_keys(p: Principal = Depends(session_principal), db: Session = Depends(get_db), all_users: bool = False):
    q = select(ApiKey).order_by(ApiKey.created_at.desc())
    if not (all_users and p.is_admin):
        q = q.where(ApiKey.user_id == p.id)
    return [_pub(k) for k in db.scalars(q)]


@router.post("")
def create_key(body: KeyIn, request: Request, p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    scopes = sorted(set(body.scopes))
    if not scopes or not set(scopes) <= VALID_SCOPES:
        raise HTTPException(422, detail={"code": "invalid_scopes", "message": f"Scopes must be within {sorted(VALID_SCOPES)}"})
    if p.user.role == Role.viewer and "jobs" in scopes:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Viewers cannot create job keys"})
    active = db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.user_id == p.id, ApiKey.revoked_at.is_(None))) or 0
    if active >= 20:
        raise HTTPException(429, detail={"code": "too_many_keys", "message": "Maximum 20 active keys"})
    pol = effective_policy(db, p.user)
    if body.rpm_limit and body.rpm_limit > pol.api_rpm and not p.is_admin:
        raise HTTPException(403, detail={"code": "over_policy", "message": f"Your policy allows at most {pol.api_rpm} rpm"})
    full, prefix, digest = new_api_key()
    k = ApiKey(user_id=p.id, name=body.name, prefix=prefix, key_hash=digest, scopes=scopes,
               expires_at=(datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)) if body.expires_in_days else None,
               rpm_limit=body.rpm_limit, monthly_token_quota=body.monthly_token_quota)
    db.add(k)
    db.commit()
    audit(db, "api_keys.create", actor=p.user, request=request, target_type="api_key", target_id=str(k.id),
          details={"name": body.name, "scopes": scopes})
    # The full key is returned exactly once and never stored.
    return {**_pub(k), "key": full}


@router.delete("/{key_id}")
def revoke(key_id: uuid.UUID, request: Request, p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    k = db.get(ApiKey, key_id)
    if not k or (k.user_id != p.id and not p.is_admin):
        raise HTTPException(404, detail={"code": "not_found", "message": "Key not found"})
    k.revoked_at = k.revoked_at or datetime.now(timezone.utc)
    db.commit()
    audit(db, "api_keys.revoke", actor=p.user, request=request, target_type="api_key", target_id=str(k.id))
    return {"ok": True}


@router.get("/usage")
def usage(p: Principal = Depends(session_principal), db: Session = Depends(get_db), days: int = 30,
          all_users: bool = False):
    since = datetime.now(timezone.utc) - timedelta(days=min(days, 365))
    q = select(func.date_trunc("day", ApiUsage.ts).label("day"), ApiUsage.model,
               func.count().label("requests"), func.sum(ApiUsage.prompt_tokens).label("prompt_tokens"),
               func.sum(ApiUsage.completion_tokens).label("completion_tokens"),
               func.avg(ApiUsage.latency_ms).label("avg_latency_ms"),
               func.count().filter(ApiUsage.status_code >= 400).label("errors")) \
        .where(ApiUsage.ts >= since).group_by("day", ApiUsage.model).order_by("day")
    if not (all_users and p.is_admin):
        q = q.where(ApiUsage.user_id == p.id)
    rows = db.execute(q).all()
    return [{"day": r.day, "model": r.model, "requests": r.requests, "prompt_tokens": int(r.prompt_tokens or 0),
             "completion_tokens": int(r.completion_tokens or 0), "avg_latency_ms": round(float(r.avg_latency_ms or 0), 1),
             "errors": int(r.errors or 0)} for r in rows]
