"""Request dependencies: authentication, CSRF, roles and resource-level authorisation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import ApiKey, Role, SharedResource, User, UserSession, Visibility
from .security import constant_time_equals, token_digest

SESSION_COOKIE = "sah_session"
CSRF_COOKIE = "sah_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User
    via: str  # "session" | "api_key"
    session: UserSession | None = None
    api_key: ApiKey | None = None

    @property
    def is_admin(self) -> bool:
        return self.user.role == Role.admin

    @property
    def is_manager(self) -> bool:
        """Admins and delegated managers (user onboarding, sign-up approval, model requests)."""
        return self.user.role in (Role.admin, Role.manager)

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def client_ip(request: Request) -> str | None:
    # Caddy sets X-Forwarded-For; Tailscale Serve adds Tailscale-User-* headers on the hop to Caddy.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _session_principal(request: Request, db: Session) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_digest(token)))
    now = datetime.now(timezone.utc)
    if not sess or sess.revoked or sess.expires_at < now:
        return None
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        return None
    if (now - sess.last_seen_at) > timedelta(minutes=5):
        sess.last_seen_at = now
        db.commit()
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_time_equals(header, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "csrf_failed", "message": "CSRF token missing or invalid"})
    return Principal(user=user, via="session", session=sess)


def api_key_principal(request: Request, db: Session, scope: str) -> Principal | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        key = auth[7:].strip()
    elif request.headers.get("x-api-key"):  # Anthropic-style clients (Claude Code: ANTHROPIC_API_KEY)
        key = request.headers["x-api-key"].strip()
    else:
        return None
    row = db.scalar(select(ApiKey).where(ApiKey.key_hash == token_digest(key)))
    now = datetime.now(timezone.utc)
    if not row or row.revoked_at or (row.expires_at and row.expires_at < now):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "invalid_api_key", "message": "Invalid, expired or revoked API key"})
    if scope not in (row.scopes or []):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "scope_denied", "message": f"API key lacks scope '{scope}'"})
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "user_inactive", "message": "Key owner is disabled"})
    if not row.last_used_at or (now - row.last_used_at) > timedelta(minutes=1):
        row.last_used_at = now
        db.commit()
    return Principal(user=user, via="api_key", api_key=row)


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = _session_principal(request, db)
    if p is None:
        # Job/async API also accepts API keys with the "jobs" scope (never admin endpoints).
        p = api_key_principal(request, db, scope="jobs") if request.headers.get("authorization") else None
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "not_authenticated", "message": "Login required"})
    request.state.principal = p
    return p


def session_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Interactive-only endpoints (settings, keys, admin): API keys are never accepted."""
    p = _session_principal(request, db)
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "not_authenticated", "message": "Login required"})
    request.state.principal = p
    return p


def require_admin(p: Principal = Depends(session_principal)) -> Principal:
    if not p.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "admin_required", "message": "Administrator role required"})
    return p


def require_manager(p: Principal = Depends(session_principal)) -> Principal:
    if not p.is_manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "manager_required", "message": "Manager or administrator role required"})
    return p


def require_researcher(p: Principal = Depends(current_principal)) -> Principal:
    if p.user.role not in (Role.admin, Role.manager, Role.researcher):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "researcher_required", "message": "Researcher role required"})
    return p


# ---------------------------------------------------------------- resource authorisation


def can_read(db: Session, p: Principal, resource_type: str, resource_id: uuid.UUID,
             owner_id: uuid.UUID | None, visibility: Visibility) -> bool:
    if p.is_admin and resource_type in ("model",):
        return True
    if owner_id == p.id:
        return True
    if visibility == Visibility.public:
        return True
    if visibility == Visibility.shared:
        grant = db.scalar(
            select(SharedResource.id).where(
                SharedResource.resource_type == resource_type,
                SharedResource.resource_id == resource_id,
                SharedResource.grantee_id == p.id,
            )
        )
        return grant is not None
    return False


def visible_filter(model_cls, p: Principal, resource_type: str):
    """SQL filter: rows owned by the principal, public, or explicitly shared with them."""
    shared_ids = select(SharedResource.resource_id).where(
        SharedResource.resource_type == resource_type, SharedResource.grantee_id == p.id
    )
    return or_(
        model_cls.owner_id == p.id,
        model_cls.visibility == Visibility.public,
        model_cls.id.in_(shared_ids),
    )


def not_found(what: str = "resource") -> HTTPException:
    # 404 (not 403) for private resources of other users avoids leaking existence.
    return HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found", "message": f"{what} not found"})
