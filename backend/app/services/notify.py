"""In-app notifications (bell in the header) with optional e-mail copy."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Notification, Role, User
from ..redis_client import publish
from . import mailer


def user_lang(user: User | None) -> str:
    return ((user.preferences or {}).get("language") if user else None) or "es"


def notify(db: Session, user_id: uuid.UUID, kind: str, title: str, body: str = "", link: str | None = None,
           data: dict | None = None, email: bool = True) -> Notification:
    n = Notification(user_id=user_id, kind=kind, title=title[:255], body=body, link=link, data=data or {})
    db.add(n)
    db.flush()
    user = db.get(User, user_id)
    if email and user and user.email and (user.preferences or {}).get("email_notifications", True):
        full = f"{get_settings().public_base_url}{link}" if link and link.startswith("/") else (link or "")
        mailer.send_template(db, "notification", user.email, user_lang(user),
                             {"title": title, "body": body, "link_line": full})
    try:
        publish(f"sah:notify:{user_id}", {"id": n.id, "kind": kind, "title": title})
    except Exception:  # noqa: BLE001
        pass
    return n


def managers(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role.in_([Role.admin, Role.manager]), User.is_active.is_(True))))


def notify_managers(db: Session, kind: str, title: str, body: str = "", link: str | None = None,
                    data: dict | None = None, email: bool = True) -> None:
    for u in managers(db):
        notify(db, u.id, kind, title, body, link, data, email=email)
