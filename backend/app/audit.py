"""Audit trail for administrative and security-relevant actions."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from .logging_setup import log, request_id_var
from .models import AuditEvent, User


def audit(
    db: Session,
    action: str,
    *,
    actor: User | None = None,
    request: Request | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    success: bool = True,
    details: dict | None = None,
    commit: bool = True,
) -> None:
    ip = None
    if request is not None:
        from .deps import client_ip

        ip = client_ip(request)
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            actor_name=actor.username if actor else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id else None,
            success=success,
            ip=ip,
            request_id=request_id_var.get(),
            details=details or {},
        )
    )
    if commit:
        db.commit()
    log.info("audit", action=action, actor=actor.username if actor else None, target=target_id, success=success)
