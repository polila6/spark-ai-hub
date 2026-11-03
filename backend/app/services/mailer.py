"""Outgoing e-mail: a durable outbox in PostgreSQL drained by the worker over SMTP (e.g. port 587 STARTTLS).

Mail is optional. When it is disabled every message is still recorded (status "skipped") so administrators can see
what would have been sent, and every flow that sends a link also shows that link in the UI for manual sharing.
"""
from __future__ import annotations

import html
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings, public_base_url
from ..logging_setup import log
from ..models import EmailOutbox
from ..settings_store import get_setting

BACKOFF_MIN = [1, 5, 15, 60, 240]


class MailError(Exception):
    pass


def mail_enabled(db: Session) -> bool:
    return bool(get_setting(db, "mail_enabled") and get_setting(db, "smtp_host") and get_setting(db, "smtp_from"))


def _smtp_send(db: Session, to: str, subject: str, text: str, body_html: str | None) -> None:
    host = get_setting(db, "smtp_host")
    port = int(get_setting(db, "smtp_port") or 587)
    security = get_setting(db, "smtp_security") or "starttls"
    user = get_setting(db, "smtp_username")
    password = get_setting(db, "smtp_password")
    sender = get_setting(db, "smtp_from")
    if not host or not sender:
        raise MailError("SMTP host and sender address must be configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((get_setting(db, "smtp_from_name") or "Spark AI Hub", sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    ctx = ssl.create_default_context()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"{type(e).__name__}: {e}") from e


def enqueue(db: Session, kind: str, to: str | None, subject: str, text: str, body_html: str | None = None) -> EmailOutbox | None:
    if not to:
        return None
    row = EmailOutbox(kind=kind, to_addr=to, subject=subject[:255], body_text=text, body_html=body_html,
                      status="pending" if mail_enabled(db) else "skipped")
    db.add(row)
    db.flush()
    return row


def drain(db: Session, limit: int = 20) -> int:
    """Send due messages. Called by the worker every 30 s."""
    if not mail_enabled(db):
        return 0
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(EmailOutbox).where(EmailOutbox.status == "pending", EmailOutbox.next_attempt_at <= now)
                      .order_by(EmailOutbox.id).limit(limit).with_for_update(skip_locked=True)).all()
    sent = 0
    for r in rows:
        try:
            _smtp_send(db, r.to_addr, r.subject, r.body_text, r.body_html)
            r.status, r.sent_at, r.last_error = "sent", datetime.now(timezone.utc), None
            sent += 1
        except MailError as e:
            r.attempts += 1
            r.last_error = str(e)[:2000]
            if r.attempts >= len(BACKOFF_MIN):
                r.status = "failed"
            else:
                r.next_attempt_at = now + timedelta(minutes=BACKOFF_MIN[r.attempts - 1])
            log.warning("mail.send_failed", id=r.id, kind=r.kind, attempts=r.attempts, error=str(e)[:300])
    db.commit()
    return sent


def send_test(db: Session, to: str) -> None:
    subject, text, body = render("test", "es", {})
    _smtp_send(db, to, subject, text, body)


# ------------------------------------------------------------------ templates

_T = {
    "es": {
        "test": ("Prueba de correo · Spark AI Hub", "El envío de correo de Spark AI Hub funciona correctamente.", []),
        "invite": ("Te han invitado a Spark AI Hub",
                   "{inviter} te ha dado acceso a Spark AI Hub, la plataforma de IA privada{org_de} en el DGX Spark.",
                   ["access", "Activa tu cuenta (enlace válido {hours} h): {link}", "Tu usuario es: {username}",
                    "Después entra en {base}"]),
        "verify": ("Confirma tu correo · Spark AI Hub", "Confirma que esta dirección es tuya para tu cuenta {username}.",
                   ["Abre este enlace: {link}"]),
        "signup_received": ("Hemos recibido tu solicitud · Spark AI Hub",
                            "Tu solicitud de cuenta ({username}) está pendiente de aprobación. Te avisaremos por correo.",
                            []),
        "signup_pending": ("Nueva solicitud de cuenta: {username}",
                           "{display_name} <{email}> ha solicitado una cuenta.\nMotivo: {reason}",
                           ["Revísala en {base}/users?tab=signups"]),
        "signup_approved": ("Tu cuenta de Spark AI Hub está activa", "Un administrador ha aprobado tu cuenta {username}.",
                            ["access", "Entra en {base}/login con tu usuario y contraseña."]),
        "signup_rejected": ("Solicitud de cuenta no aprobada · Spark AI Hub",
                            "Tu solicitud de cuenta ({username}) no ha sido aprobada.\n{reason}", []),
        "reset": ("Restablecer contraseña · Spark AI Hub", "Se ha pedido restablecer la contraseña de {username}.",
                  ["Elige una nueva contraseña (enlace válido {hours} h): {link}",
                   "Si no lo has pedido tú, ignora este correo."]),
        "notification": ("{title}", "{body}", ["{link_line}"]),
    },
    "en": {
        "test": ("Mail test · Spark AI Hub", "Spark AI Hub outgoing mail works.", []),
        "invite": ("You have been invited to Spark AI Hub",
                   "{inviter} gave you access to Spark AI Hub, the private AI platform{org_of} on the DGX Spark.",
                   ["access", "Activate your account (link valid {hours} h): {link}", "Your username is: {username}",
                    "Then sign in at {base}"]),
        "verify": ("Confirm your e-mail · Spark AI Hub", "Confirm this address belongs to your account {username}.",
                   ["Open this link: {link}"]),
        "signup_received": ("We received your request · Spark AI Hub",
                            "Your account request ({username}) is awaiting approval. We will e-mail you.", []),
        "signup_pending": ("New account request: {username}", "{display_name} <{email}> requested an account.\nReason: {reason}",
                           ["Review it at {base}/users?tab=signups"]),
        "signup_approved": ("Your Spark AI Hub account is active", "An administrator approved your account {username}.",
                            ["access", "Sign in at {base}/login with your username and password."]),
        "signup_rejected": ("Account request not approved · Spark AI Hub",
                            "Your account request ({username}) was not approved.\n{reason}", []),
        "reset": ("Reset your password · Spark AI Hub", "A password reset was requested for {username}.",
                  ["Choose a new password (link valid {hours} h): {link}", "If you did not request it, ignore this e-mail."]),
        "notification": ("{title}", "{body}", ["{link_line}"]),
    },
}

_ACCESS = {
    "es": ["Cómo conectarte (solo la primera vez):",
           "1. Instala Tailscale en tu ordenador: https://tailscale.com/download",
           "2. Inicia sesión en Tailscale con tu cuenta (Google, Microsoft, GitHub o correo).",
           "3. Acepta la invitación al equipo «spark»: {share}",
           "4. Con Tailscale conectado, abre {base}"],
    "en": ["How to connect (first time only):",
           "1. Install Tailscale on your computer: https://tailscale.com/download",
           "2. Sign in to Tailscale with your account (Google, Microsoft, GitHub or e-mail).",
           "3. Accept the invitation to the «spark» machine: {share}",
           "4. With Tailscale connected, open {base}"],
}


class _Safe(dict):
    def __missing__(self, key):
        return ""


def render(kind: str, lang: str, ctx: dict) -> tuple[str, str, str]:
    lang = lang if lang in _T else "es"
    subject, intro, lines = _T[lang][kind]
    org = get_settings().org_name.strip()
    c = _Safe({"base": public_base_url(), "org_de": f" de {org}" if org else "",
               "org_of": f" of {org}" if org else "", **ctx})
    c.setdefault("share", "")
    if not c.get("share"):
        c["share"] = ("el administrador te la enviará aparte" if lang == "es" else "the administrator will send it separately")
    out_lines: list[str] = []
    for ln in lines:
        if ln == "access":
            out_lines += [x.format_map(c) for x in _ACCESS[lang]]
        else:
            v = ln.format_map(c)
            if v.strip():
                out_lines.append(v)
    subj = subject.format_map(c)
    text_body = intro.format_map(c) + ("\n\n" + "\n".join(out_lines) if out_lines else "") + f"\n\n— Spark AI Hub{' · ' + org if org else ''}\n"

    def linkify(s: str) -> str:
        esc = html.escape(s)
        for word in esc.split():
            if word.startswith("http://") or word.startswith("https://"):
                esc = esc.replace(word, f'<a href="{word}" style="color:#0f766e">{word}</a>', 1)
        return esc

    items = "".join(f'<p style="margin:6px 0">{linkify(x)}</p>' for x in out_lines)
    body_html = (
        '<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;color:#0f172a">'
        '<div style="background:#0f766e;color:#fff;padding:14px 18px;border-radius:12px 12px 0 0;font-weight:600">'
        'Spark AI Hub</div><div style="border:1px solid #e2e8f0;border-top:0;padding:18px;border-radius:0 0 12px 12px">'
        f'<p style="margin:0 0 12px;white-space:pre-line">{linkify(intro.format_map(c))}</p>{items}'
        + (f'<p style="margin-top:18px;color:#64748b;font-size:12px">{html.escape(org)}</p>' if org else '') +
        '</div></div>')
    return subj, text_body, body_html


def send_template(db: Session, kind: str, to: str | None, lang: str, ctx: dict) -> EmailOutbox | None:
    subject, text, body = render(kind, lang, ctx)
    return enqueue(db, kind, to, subject, text, body)
