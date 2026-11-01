"""Sign in with Google, Microsoft (Entra ID) or any OpenID Connect provider.

Authorization-code flow with PKCE, state bound to the browser (Lax cookie) and a nonce. The ID token is obtained
directly from the provider's token endpoint over TLS (OIDC Core 3.1.3.7), and its iss/aud/exp/nonce are checked.
Existing accounts are linked by e-mail only when the provider vouches for it; new accounts follow the sign-up mode
(open → active, approval → pending for a manager, closed → refused).
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..models import Role, User, UserIdentity
from ..redis_client import get_redis, rate_limit
from ..services import notify
from ..settings_store import get_setting
from .auth import _issue_session, terms_info

router = APIRouter(prefix="/api/v1/auth/oidc", tags=["auth"])
STATE_COOKIE = "sah_oidc"
GENERIC_TENANTS = ("common", "organizations", "consumers")


def _providers(db: Session) -> dict[str, dict]:
    out = {}
    if get_setting(db, "oidc_google_client_id") and get_setting(db, "oidc_google_client_secret"):
        out["google"] = {"name": "Google", "issuer": "https://accounts.google.com",
                         "client_id": get_setting(db, "oidc_google_client_id"),
                         "secret": get_setting(db, "oidc_google_client_secret"), "trust_email": "claim"}
    if get_setting(db, "oidc_microsoft_client_id") and get_setting(db, "oidc_microsoft_client_secret"):
        tenant = (get_setting(db, "oidc_microsoft_tenant") or "organizations").strip()
        out["microsoft"] = {"name": "Microsoft", "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
                            "client_id": get_setting(db, "oidc_microsoft_client_id"),
                            "secret": get_setting(db, "oidc_microsoft_client_secret"),
                            # e-mail is only trusted for linking when the app is limited to one organisation
                            "trust_email": "never" if tenant in GENERIC_TENANTS else "always", "tenant": tenant}
    if get_setting(db, "oidc_generic_issuer") and get_setting(db, "oidc_generic_client_id"):
        out["oidc"] = {"name": get_setting(db, "oidc_generic_name") or "SSO",
                       "issuer": get_setting(db, "oidc_generic_issuer").rstrip("/"),
                       "client_id": get_setting(db, "oidc_generic_client_id"),
                       "secret": get_setting(db, "oidc_generic_client_secret"), "trust_email": "claim"}
    return out


def redirect_uri(provider: str) -> str:
    return f"{get_settings().public_base_url}/api/v1/auth/oidc/{provider}/callback"


def _discovery(issuer: str) -> dict:
    r = get_redis()
    key = f"oidc:disc:{hashlib.sha256(issuer.encode()).hexdigest()[:16]}"
    if (hit := r.get(key)):
        return json.loads(hit)
    with httpx.Client(timeout=15) as c:
        d = c.get(f"{issuer}/.well-known/openid-configuration").raise_for_status().json()
    r.setex(key, 86400, json.dumps(d))
    return d


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise ValueError("ID token ilegible") from e


def _page(location: str) -> HTMLResponse:
    """A same-site navigation so the SameSite=Strict session cookie set here is sent on the next request."""
    loc = html.escape(location, quote=True)
    return HTMLResponse(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={loc}">'
                        f'<script>location.replace({json.dumps(location)})</script><a href="{loc}">Continuar</a>',
                        headers={"Cache-Control": "no-store"})


def _fail(code: str) -> HTMLResponse:
    return _page(f"/login?sso_error={code}")


@router.get("/providers")
def providers(db: Session = Depends(get_db)):
    return [{"id": k, "name": v["name"]} for k, v in _providers(db).items()]


@router.get("/{provider}/start")
def start(provider: str, request: Request, next: str = "/", db: Session = Depends(get_db)):
    prov = _providers(db).get(provider)
    if not prov:
        raise HTTPException(404, detail={"code": "not_configured", "message": "Proveedor no configurado"})
    ok, _ = rate_limit(f"oidc:start:{request.client.host if request.client else 'x'}", 30, 300)
    if not ok:
        raise HTTPException(429, detail={"code": "rate_limited", "message": "Demasiados intentos"})
    disc = _discovery(prov["issuer"])
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    nxt = next if next.startswith("/") and not next.startswith("//") else "/"
    get_redis().setex(f"oidc:state:{state}", 600, json.dumps({"provider": provider, "nonce": nonce,
                                                               "verifier": verifier, "next": nxt}))
    q = {"response_type": "code", "client_id": prov["client_id"], "redirect_uri": redirect_uri(provider),
         "scope": "openid email profile", "state": state, "nonce": nonce,
         "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
         "prompt": "select_account"}
    resp = RedirectResponse(f"{disc['authorization_endpoint']}?{urlencode(q)}", status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, secure=get_settings().cookie_secure,
                    samesite="lax", path="/api/v1/auth/oidc")
    return resp


def _username(db: Session, email: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9._-]+", "", (email.split("@")[0] if email else name).lower())[:40] or "usuario"
    if not base[0].isalnum():
        base = "u" + base
    cand, n = base, 1
    while db.scalar(select(User.id).where(User.username == cand)):
        n += 1
        cand = f"{base}{n}"
    return cand


@router.get("/{provider}/callback")
def callback(provider: str, request: Request, code: str = "", state: str = "", error: str = "",
             db: Session = Depends(get_db)):
    if error:
        return _fail("cancelled")
    raw = get_redis().getdel(f"oidc:state:{state}") if state else None
    if not raw or request.cookies.get(STATE_COOKIE) != state:
        return _fail("state")
    st = json.loads(raw)
    prov = _providers(db).get(provider)
    if not prov or st["provider"] != provider or not code:
        return _fail("state")
    try:
        disc = _discovery(prov["issuer"])
        with httpx.Client(timeout=20) as c:
            tok = c.post(disc["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider),
                "client_id": prov["client_id"], "client_secret": prov["secret"] or "", "code_verifier": st["verifier"]},
                headers={"Accept": "application/json"}).raise_for_status().json()
        cl = _claims(tok["id_token"])
    except (httpx.HTTPError, KeyError, ValueError):
        return _fail("token")
    iss_ok = cl.get("iss") == disc.get("issuer") or (
        provider == "microsoft" and re.fullmatch(r"https://login\.microsoftonline\.com/[0-9a-f-]{36}/v2\.0", str(cl.get("iss"))) is not None)
    aud = cl.get("aud")
    aud_ok = aud == prov["client_id"] or (isinstance(aud, list) and prov["client_id"] in aud)
    if not (iss_ok and aud_ok and cl.get("nonce") == st["nonce"] and int(cl.get("exp", 0)) > time.time() - 60):
        return _fail("claims")
    subject = str(cl.get("sub") or cl.get("oid") or "")
    email = str(cl.get("email") or (cl.get("preferred_username") if provider == "microsoft" else "") or "").strip().lower()
    name = str(cl.get("name") or email.split("@")[0] or "Usuario")[:128]
    trusted = {"always": True, "never": False}.get(prov["trust_email"], cl.get("email_verified") in (True, "true"))
    if not subject:
        return _fail("claims")

    ident = db.scalar(select(UserIdentity).where(UserIdentity.provider == provider, UserIdentity.subject == subject))
    user = db.get(User, ident.user_id) if ident else None
    if user is None and email and trusted:
        user = db.scalar(select(User).where(User.email == email))
    created = False
    if user is None:
        mode = get_setting(db, "signup_mode") or "approval"
        if mode == "closed":
            return _fail("closed")
        domains = [d.lower().lstrip("@") for d in (get_setting(db, "signup_allowed_domains") or [])]
        if not email or (domains and not any(email.endswith("@" + d) or email.endswith("." + d) for d in domains)):
            return _fail("domain")
        if db.scalar(select(User.id).where(User.email == email)):
            return _fail("link")  # an account with this e-mail exists but the provider does not vouch for it
        pending = mode == "approval"
        user = User(username=_username(db, email, name), email=email, display_name=name, password_hash=None,
                    role=Role.researcher, is_active=not pending, signup_status="pending" if pending else None,
                    signup_reason=f"Registro con {prov['name']}", email_verified_at=datetime.now(timezone.utc) if trusted else None,
                    preferences={"language": "es"})
        db.add(user)
        db.flush()
        created = True
        if pending:
            notify.notify_managers(db, "signup", f"Nueva solicitud de cuenta ({prov['name']}): {user.username}",
                                   f"{name} <{email}>", link="/users?tab=signups", data={"user_id": str(user.id)}, email=True)
    if ident is None:
        db.add(UserIdentity(user_id=user.id, provider=provider, subject=subject, email=email or None,
                            last_login_at=datetime.now(timezone.utc)))
    else:
        ident.last_login_at = datetime.now(timezone.utc)
    db.commit()
    audit(db, "auth.oidc", actor=user, request=request, details={"provider": provider, "created": created})
    if user.signup_status == "pending":
        return _page("/login?sso_error=pending")
    if user.signup_status == "rejected" or not user.is_active:
        return _fail("inactive")
    resp = _page(st["next"])
    _issue_session(db, user, request, resp)
    resp.delete_cookie(STATE_COOKIE, path="/api/v1/auth/oidc")
    _ = terms_info
    return resp
