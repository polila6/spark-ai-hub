"""Authentication: cookie sessions with CSRF, login throttling, invitations and resets."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings, public_base_url
from ..db import get_db
from ..deps import CSRF_COOKIE, SESSION_COOKIE, Principal, client_ip, session_principal
from ..models import Invitation, Role, User, UserSession
from ..redis_client import rate_limit
from ..security import hash_password, new_token, password_problems, token_digest, verify_password
from ..services import mailer, notify, people
from ..settings_store import branding, get_setting

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

LOCKOUT_THRESHOLD = 8
LOCKOUT_MINUTES = 15


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)
    password: str = Field(min_length=10, max_length=256)
    display_name: str | None = Field(default=None, max_length=128)


def user_public(u: User) -> dict:
    return {
        "id": str(u.id),
        "username": u.username,
        "email": u.email,
        "display_name": u.display_name or u.username,
        "role": u.role.value,
        "is_active": u.is_active,
        "preferences": u.preferences or {},
        "created_at": u.created_at,
        "last_login_at": u.last_login_at,
        "signup_status": u.signup_status,
        "signup_reason": u.signup_reason,
        "email_verified": bool(u.email_verified_at),
        "terms_version": u.terms_version,
        "activated": u.password_hash is not None,
    }


DEFAULT_TERMS = {
    "es": """**Normas de uso de Spark AI Hub{org_paren}**

1. La plataforma es para actividades de investigación, docencia y trabajo{org_de}. No la uses para fines ajenos.
2. {clause2}
3. Los modelos se ejecutan en este DGX Spark: nada se envía a servicios de IA externos. Si la búsqueda en internet está activada, solo salen las consultas de búsqueda, sin datos personales.
4. No subas datos personales de terceros ni información confidencial sin autorización. Eres responsable del contenido que subes y generas.
5. Respeta las licencias de los modelos (se muestran al instalarlos) y no generes contenido ilegal o que dañe a otras personas.
6. Los recursos (memoria, GPU, disco) son compartidos: libera lo que no uses y respeta las cuotas.
7. Puedes descargar todos tus datos o eliminar tu cuenta en Ajustes → Privacidad.
{clause8_es}""".rstrip("\n"),
    "en": """**Spark AI Hub terms of use{org_paren}**

1. The platform is for research, teaching and work{org_of}. Do not use it for unrelated purposes.
2. {clause2}
3. Models run on this DGX Spark: nothing is sent to external AI services. If web search is enabled, only the search queries leave the machine, without personal data.
4. Do not upload third parties' personal data or confidential information without authorisation. You are responsible for what you upload and generate.
5. Respect model licences (shown at install time) and do not generate illegal or harmful content.
6. Resources (memory, GPU, disk) are shared: free what you do not use and respect quotas.
7. You can download all your data or delete your account in Settings → Privacy.
{clause8_en}""".rstrip("\n"),
}


CLAUSES = {
    "consent": {
        "es": ("Tus chats, archivos, documentos y memoria son **privados** frente a los demás usuarios: solo los ve quien tú compartas. "
               "**Un administrador puede consultarlos**, incluido lo que ya tengas guardado, indicando un motivo. Cada consulta queda "
               "registrada y la ves en Ajustes → Privacidad: quién, cuándo, qué y por qué. Fuera de eso, los administradores solo ven "
               "metadatos de uso (tokens, tiempos, tamaño ocupado)."),
        "en": ("Your chats, files, documents and memory are **private** from other users: only whoever you share with sees them. "
               "**An administrator can consult them**, including what you already have stored, by stating a reason. Each consultation is "
               "logged and you see it in Settings → Privacy: who, when, what and why. Apart from that, administrators only see usage "
               "metadata (tokens, times, space used)."),
        "8_es": "8. Si no aceptas estas normas, pulsa «Salir»: no podrás usar la plataforma, pero ningún administrador podrá consultar tu contenido. Para recuperar tus datos o darte de baja, habla con un administrador.",
        "8_en": "8. If you do not accept these terms, press “Sign out”: you will not be able to use the platform, but no administrator can consult your content. To get your data back or close your account, talk to an administrator.",
    },
    "institutional": {
        "es": ("Tus chats, archivos, documentos y memoria son **privados** frente a los demás usuarios: solo los ve quien tú compartas. "
               "**Los administradores de la plataforma pueden consultarlos** por motivos de seguridad y administración, conforme a la "
               "normativa de uso de los sistemas de información de tu institución, incluido lo que ya tengas guardado. Cada consulta "
               "queda registrada y la ves en Ajustes → Privacidad: quién, cuándo y qué. Fuera de eso, los administradores solo ven "
               "metadatos de uso (tokens, tiempos, tamaño ocupado)."),
        "en": ("Your chats, files, documents and memory are **private** from other users: only whoever you share with sees them. "
               "**Platform administrators can consult them** for security and administration, under your institution's rules on the use "
               "of information systems, including what you already have stored. Each consultation is logged and you see it in "
               "Settings → Privacy: who, when and what. Apart from that, administrators only see usage metadata (tokens, times, "
               "space used)."),
        "8_es": "", "8_en": "",
    },
}


def _default_terms(mode: str = "consent") -> dict:
    org = branding()["org_name"]
    c = CLAUSES.get(mode, CLAUSES["consent"])
    fill = {"org_paren": f" ({org})" if org else ""}
    return {"es": DEFAULT_TERMS["es"].format(**fill, org_de=f" de {org}" if org else "", clause2=c["es"], clause8_es=c["8_es"], clause8_en=""),
            "en": DEFAULT_TERMS["en"].format(**fill, org_of=f" at {org}" if org else "", clause2=c["en"], clause8_en=c["8_en"], clause8_es="")}


def terms_info(db: Session) -> dict:
    custom = get_setting(db, "terms_text")
    return {"version": int(get_setting(db, "terms_version") or 1),
            "text": {"es": custom, "en": custom} if custom else _default_terms(people.content_mode(db))}


def terms_cover_content(db: Session) -> bool:
    """Does accepting the terms mean consenting to content access? Not in institutional mode: nobody is asked to accept anything."""
    if people.content_mode(db) == "institutional":
        return False
    return not get_setting(db, "terms_text") or bool(get_setting(db, "terms_cover_content_access"))


def _issue_session(db: Session, user: User, request: Request, response: Response) -> UserSession:
    s = get_settings()
    token = new_token()
    csrf = new_token(24)
    sess = UserSession(
        token_hash=token_digest(token),
        csrf_token=csrf,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_ttl_hours),
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
    )
    db.add(sess)
    user.last_login_at = datetime.now(timezone.utc)
    user.failed_logins = 0
    user.locked_until = None
    db.commit()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="strict", path="/")
    # Readable by JS on purpose: double-submit CSRF token sent back in X-CSRF-Token.
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="strict", path="/")
    return sess


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request) or "unknown"
    ok_ip, retry_ip = rate_limit(f"login:ip:{ip}", limit=20, window_s=300)
    ok_user, retry_user = rate_limit(f"login:user:{body.username.lower()}", limit=10, window_s=300)
    if not (ok_ip and ok_user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail={"code": "rate_limited", "message": "Too many login attempts",
                                    "retry_after": max(retry_ip, retry_user)})
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    now = datetime.now(timezone.utc)
    if user and user.locked_until and user.locked_until > now:
        audit(db, "auth.login", actor=user, request=request, success=False, details={"reason": "locked"})
        raise HTTPException(status.HTTP_423_LOCKED, detail={"code": "account_locked", "message": "Account temporarily locked"})
    valid = verify_password(user.password_hash if user else None, body.password)
    if user and valid and user.signup_status in ("pending", "rejected"):
        code = "account_pending" if user.signup_status == "pending" else "account_rejected"
        msg = ("Your account request is awaiting approval" if code == "account_pending"
               else "Your account request was not approved")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": code, "message": msg})
    if not user or not valid or not user.is_active:
        if user:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= LOCKOUT_THRESHOLD:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            db.commit()
        audit(db, "auth.login", actor=user, request=request, success=False,
              details={"username": body.username[:64]})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "invalid_credentials", "message": "Invalid username or password"})
    sess = _issue_session(db, user, request, response)
    audit(db, "auth.login", actor=user, request=request, success=True)
    return {"user": user_public(user), "csrf_token": sess.csrf_token}


@router.post("/logout")
def logout(request: Request, response: Response, p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    if p.session:
        p.session.revoked = True
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    audit(db, "auth.logout", actor=p.user, request=request)
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    terms = terms_info(db)
    return {"user": user_public(p.user), "csrf_token": p.session.csrf_token if p.session else None,
            "needs_terms": (p.user.terms_version or 0) < terms["version"]
            or (terms_cover_content(db) and p.user.content_access_accepted_at is None)}


@router.get("/terms")
def get_terms(db: Session = Depends(get_db)):
    return terms_info(db)


@router.post("/me/accept-terms")
def accept_terms(request: Request, p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    p.user.terms_version = terms_info(db)["version"]
    p.user.terms_accepted_at = datetime.now(timezone.utc)
    covers = terms_cover_content(db)
    if covers:
        p.user.content_access_accepted_at = p.user.terms_accepted_at
    db.commit()
    audit(db, "auth.terms_accepted", actor=p.user, request=request, details={"version": p.user.terms_version, "content_access": covers})
    return {"ok": True}


@router.patch("/me/preferences")
def update_preferences(prefs: dict, p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    allowed = {"language", "theme", "favorite_models", "recent_experiments", "dashboard_layout", "default_model",
               "custom_instructions", "about_me", "memory_enabled", "email_notifications", "onboarding",
               "chat_tools", "dismissed_guides"}
    clean = {k: v for k, v in prefs.items() if k in allowed}
    merged = dict(p.user.preferences or {})
    merged.update(clean)
    p.user.preferences = merged
    db.commit()
    return {"preferences": merged}


@router.post("/me/password")
def change_password(body: PasswordChangeIn, request: Request, p: Principal = Depends(session_principal),
                    db: Session = Depends(get_db)):
    if not verify_password(p.user.password_hash, body.current_password):
        raise HTTPException(400, detail={"code": "wrong_password", "message": "Current password is incorrect"})
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(422, detail={"code": "weak_password", "message": "Password too weak", "problems": problems})
    p.user.password_hash = hash_password(body.new_password)
    # Revoke all other sessions.
    for s in db.scalars(select(UserSession).where(UserSession.user_id == p.user.id, UserSession.revoked.is_(False))):
        if not p.session or s.id != p.session.id:
            s.revoked = True
    db.commit()
    audit(db, "auth.password_change", actor=p.user, request=request)
    return {"ok": True}


@router.get("/sessions")
def my_sessions(p: Principal = Depends(session_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(UserSession).where(UserSession.user_id == p.user.id, UserSession.revoked.is_(False),
                                                UserSession.expires_at > datetime.now(timezone.utc))
                      .order_by(UserSession.created_at.desc())).all()
    return [{"id": str(s.id), "created_at": s.created_at, "last_seen_at": s.last_seen_at, "ip": s.ip,
             "user_agent": s.user_agent, "current": bool(p.session and s.id == p.session.id)} for s in rows]


@router.get("/invitation/{token}")
def inspect_invitation(token: str, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_digest(token)))
    if not inv or inv.kind == "verify" or inv.used_at or inv.expires_at < datetime.now(timezone.utc):
        raise HTTPException(404, detail={"code": "invalid_invitation", "message": "Invitation invalid or expired"})
    user = db.get(User, inv.user_id)
    return {"kind": inv.kind, "username": user.username if user else None, "expires_at": inv.expires_at}


@router.post("/invitation/accept")
def accept_invitation(body: AcceptInviteIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ok, retry = rate_limit(f"invite:ip:{client_ip(request)}", limit=10, window_s=300)
    if not ok:
        raise HTTPException(429, detail={"code": "rate_limited", "message": "Too many attempts", "retry_after": retry})
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_digest(body.token)))
    if not inv or inv.kind == "verify" or inv.used_at or inv.expires_at < datetime.now(timezone.utc):
        raise HTTPException(404, detail={"code": "invalid_invitation", "message": "Invitation invalid or expired"})
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(422, detail={"code": "weak_password", "message": "Password too weak", "problems": problems})
    user = db.get(User, inv.user_id)
    if not user:
        raise HTTPException(404, detail={"code": "invalid_invitation", "message": "User no longer exists"})
    user.password_hash = hash_password(body.password)
    user.is_active = True
    user.signup_status = None
    if body.display_name:
        user.display_name = body.display_name
    inv.used_at = datetime.now(timezone.utc)
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
        s.revoked = True
    db.commit()
    audit(db, f"auth.{inv.kind}_accepted", actor=user, request=request)
    sess = _issue_session(db, user, request, response)
    return {"user": user_public(user), "csrf_token": sess.csrf_token}


# ------------------------------------------------------------------ self sign-up, verification, password reset

USERNAME_RE = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: str = Field(min_length=5, max_length=255)
    display_name: str = Field(default="", max_length=128)
    password: str = Field(min_length=10, max_length=256)
    reason: str = Field(default="", max_length=2000)
    accept_terms: bool = False
    language: str = Field(default="es", pattern="^(es|en)$")


def _link_token(db: Session, user: User, kind: str, hours: int, actor_id=None) -> str:
    token = new_token()
    db.add(Invitation(token_hash=token_digest(token), kind=kind, user_id=user.id, created_by=actor_id,
                      expires_at=datetime.now(timezone.utc) + timedelta(hours=hours)))
    db.flush()
    return f"{public_base_url()}/{'verify' if kind == 'verify' else 'invite'}/{token}"


@router.get("/signup-info")
def signup_info(db: Session = Depends(get_db)):
    return {"mode": get_setting(db, "signup_mode") or "approval",
            "allowed_domains": get_setting(db, "signup_allowed_domains") or [],
            "mail_enabled": mailer.mail_enabled(db), "terms": terms_info(db)}


@router.post("/signup")
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    mode = get_setting(db, "signup_mode") or "approval"
    if mode == "closed":
        raise HTTPException(403, detail={"code": "signup_closed", "message": "Sign-up is closed; ask an administrator for an invitation"})
    ok, retry = rate_limit(f"signup:ip:{client_ip(request)}", limit=5, window_s=3600)
    if not ok:
        raise HTTPException(429, detail={"code": "rate_limited", "message": "Too many sign-up attempts", "retry_after": retry})
    username = body.username.strip().lower()
    email = body.email.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(422, detail={"code": "invalid_username", "message": "Use 2-64 chars: a-z 0-9 . _ -"})
    if not EMAIL_RE.match(email):
        raise HTTPException(422, detail={"code": "invalid_email", "message": "Invalid e-mail address"})
    domains = [d.lower().lstrip("@") for d in (get_setting(db, "signup_allowed_domains") or [])]
    if domains and not any(email.endswith("@" + d) or email.endswith("." + d) for d in domains):
        raise HTTPException(422, detail={"code": "domain_not_allowed",
                                         "message": f"E-mail domain not allowed (allowed: {', '.join(domains)})"})
    if not body.accept_terms:
        raise HTTPException(422, detail={"code": "terms_required", "message": "You must accept the terms of use"})
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(422, detail={"code": "weak_password", "message": "Password too weak", "problems": problems})
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, detail={"code": "username_taken", "message": "Username already exists"})
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, detail={"code": "email_taken", "message": "E-mail already registered"})
    pending = mode == "approval"
    user = User(username=username, email=email, display_name=body.display_name.strip() or username,
                password_hash=hash_password(body.password), role=Role.researcher, is_active=not pending,
                signup_status="pending" if pending else None, signup_reason=body.reason.strip() or None,
                terms_version=terms_info(db)["version"], terms_accepted_at=datetime.now(timezone.utc),
                content_access_accepted_at=datetime.now(timezone.utc) if terms_cover_content(db) else None,
                preferences={"language": body.language})
    db.add(user)
    db.flush()
    if mailer.mail_enabled(db):
        mailer.send_template(db, "verify", email, body.language,
                             {"username": username, "link": _link_token(db, user, "verify", 24 * 7)})
    if pending:
        mailer.send_template(db, "signup_received", email, body.language, {"username": username})
        notify.notify_managers(db, "signup", f"Nueva solicitud de cuenta: {username}",
                               f"{user.display_name} <{email}>" + (f" — {user.signup_reason}" if user.signup_reason else ""),
                               link="/users?tab=signups", data={"user_id": str(user.id)}, email=False)
        for m in notify.managers(db):
            mailer.send_template(db, "signup_pending", m.email, notify.user_lang(m),
                                 {"username": username, "display_name": user.display_name, "email": email,
                                  "reason": user.signup_reason or "—"})
    db.commit()
    audit(db, "auth.signup", actor=user, request=request, details={"mode": mode, "email_domain": email.split("@")[-1]})
    return {"status": "pending" if pending else "active", "username": username}


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)


@router.post("/verify-email")
def verify_email(body: TokenIn, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_digest(body.token), Invitation.kind == "verify"))
    if not inv or inv.used_at or inv.expires_at < datetime.now(timezone.utc):
        raise HTTPException(404, detail={"code": "invalid_link", "message": "Link invalid or expired"})
    user = db.get(User, inv.user_id)
    inv.used_at = datetime.now(timezone.utc)
    if user:
        user.email_verified_at = inv.used_at
    db.commit()
    return {"ok": True, "username": user.username if user else None, "pending": bool(user and user.signup_status == "pending")}


class ResetRequestIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@router.post("/password-reset-request")
def password_reset_request(body: ResetRequestIn, request: Request, db: Session = Depends(get_db)):
    """Always answers 200 (no account enumeration). Sends a 2-hour reset link when mail is configured."""
    ok, _ = rate_limit(f"reset:ip:{client_ip(request)}", limit=5, window_s=3600)
    if not ok:
        return {"ok": True, "mail_enabled": mailer.mail_enabled(db)}
    user = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.is_active.is_(True)))
    if user and mailer.mail_enabled(db):
        link = _link_token(db, user, "reset", 2)
        mailer.send_template(db, "reset", user.email, notify.user_lang(user), {"username": user.username, "link": link, "hours": 2})
        db.commit()
        audit(db, "auth.reset_requested", actor=user, request=request)
    return {"ok": True, "mail_enabled": mailer.mail_enabled(db)}
