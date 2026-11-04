"""Who is using the hub and what is running, for administrators.

The list and the summary are usage metadata (tokens, times, space): no chat or job title, file name or prompt, because job titles
can hold file names and turn titles are the user's words. Content is a separate step: only for people who accepted the terms
that allow it, with a reason each time, and every consultation is logged and shown to the person (see routers/people.py)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..audit import audit
from ..settings_store import get_setting, set_setting

ONLINE_WINDOW = timedelta(minutes=10)  # sessions record activity every 5 minutes at most
ACTIVE_WINDOW = timedelta(days=30)


def presence(last_seen: datetime | None, enabled: bool, now: datetime) -> str:
    if not enabled:
        return "disabled"
    if last_seen is None:
        return "never"
    if now - last_seen <= ONLINE_WINDOW:
        return "online"
    return "active" if now - last_seen <= ACTIVE_WINDOW else "inactive"


def latest(*moments: datetime | None) -> datetime | None:
    known = [m for m in moments if m is not None]
    return max(known) if known else None


CONTENT_ACTION = "admin.content_access"
CONTENT_TARGET = "user_content"


MODES = ("consent", "institutional")
MIN_REASON = 10


def content_mode(db: Session) -> str:
    mode = get_setting(db, "content_access_mode")
    return mode if mode in MODES else "consent"


def consented(user, mode: str = "consent") -> bool:
    """In consent mode the earlier acceptance does not count: those terms said administrators do not read content."""
    return mode == "institutional" or user.content_access_accepted_at is not None


def reason_ok(mode: str, reason: str) -> bool:
    return mode == "institutional" or len((reason or "").strip()) >= MIN_REASON


NOTICE_TITLE = "Cambio en las normas de uso: acceso de los administradores / Terms change: administrator access"
NOTICE_BODY = (
    "ES: Los administradores de la plataforma pueden consultar tus chats, archivos y workspaces por motivos de seguridad y "
    "administración, conforme a la normativa de uso de los sistemas de información de tu institución. Cada consulta queda "
    "registrada y la ves en Ajustes → Privacidad: quién, cuándo y qué. Las normas completas están en esa misma página.\n\n"
    "EN: Platform administrators may consult your chats, files and workspaces for security and administration, under your "
    "institution's rules on the use of information systems. Each consultation is logged and you see it in Settings → Privacy: "
    "who, when and what. The full terms are on that page.")


def access_public(event) -> dict:
    """One line of what a person sees about consultations of their content."""
    d = event.details or {}
    return {"ts": event.ts, "by": event.actor_name, "kind": d.get("kind"), "what": d.get("what"), "reason": d.get("reason")}


def job_public(job) -> dict:
    return {"id": str(job.id), "type": job.type, "status": job.status.value, "progress": job.progress,
            "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at}


def request_public(row) -> dict:
    return {"ts": row.ts, "source": row.source, "endpoint": row.endpoint, "model": row.model, "status_code": row.status_code,
            "prompt_tokens": row.prompt_tokens, "completion_tokens": row.completion_tokens, "latency_ms": row.latency_ms}


def switch_mode(db: Session, mode: str, actor=None, request=None, via: str = "api") -> dict:
    """Change the basis for administrators consulting content. Going institutional tells every active person, once, and never
    asks anyone to accept anything. The log each person sees in Settings, Privacy does not depend on the mode."""
    from sqlalchemy import select

    from ..models import User
    from . import notify

    if mode not in MODES:
        raise ValueError("mode must be consent|institutional")
    before = content_mode(db)
    set_setting(db, "content_access_mode", mode, getattr(actor, "id", None))
    told = 0
    if mode == "institutional" and before != "institutional":
        for u in db.scalars(select(User).where(User.is_active.is_(True))):
            notify.notify(db, u.id, "policy", NOTICE_TITLE, NOTICE_BODY, link="/settings")
            told += 1
        db.commit()
    audit(db, "settings.content_access_mode", actor=actor, request=request, details={"from": before, "to": mode, "notified": told, "via": via})
    return {"mode": mode, "notified": told}
