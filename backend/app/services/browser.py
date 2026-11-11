"""Client of the isolated page browser (container `browser`): renders pages that need JavaScript.

The browser has no volumes, secrets or capabilities, a fresh Chromium and an empty profile per page, and its only way out
is the filtering proxy (public addresses only). Everything that comes back is DATA: text, links, image URLs, a JPEG. This
module never executes or saves anything from the page by itself. The URL is checked here too (same SSRF rules as the
static reader) before it is sent."""
from __future__ import annotations

import base64
import os
import re
import time

import httpx

from .web import WebError, _check_url

BASE = os.environ.get("SAH_BROWSER_URL", "http://browser:3000").rstrip("/")
TIMEOUT = httpx.Timeout(75, connect=4)
CHALLENGE_RE = re.compile(r"just a moment|checking your browser|verify you are (?:a )?human|enable javascript and cookies|"
                          r"attention required|access denied|captcha|comprueba que eres humano|verificando que eres", re.I)
_alive = {"at": 0.0, "ok": False}


def available() -> bool:
    """The browser answers its health check (cached for 20 seconds)."""
    now = time.time()
    if now - _alive["at"] < 20:
        return _alive["ok"]
    try:
        ok = httpx.get(f"{BASE}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        ok = False
    _alive.update(at=now, ok=ok)
    return ok


def looks_blocked(text: str, status: int = 200) -> bool:
    """A bot-protection or error page instead of the content."""
    t = (text or "").strip()
    return status >= 400 or len(t) < 200 and bool(CHALLENGE_RE.search(t)) or bool(CHALLENGE_RE.search(t[:500])) and len(t) < 1500


def render(url: str, wait_for: str | None = None, screenshot: bool = False, full_page: bool = False) -> dict:
    """{final_url, status, title, description, text, links[{text,href}], images[{src,alt,w,h}], og_image, screenshot (bytes|None),
    blocked[]}. Raises WebError (what failed, in Spanish)."""
    _check_url(url)
    payload = {"url": url, "screenshot": bool(screenshot), "full_page": bool(full_page)}
    if wait_for:
        payload["wait_for"] = str(wait_for)[:200]
    try:
        r = httpx.post(f"{BASE}/render", json=payload, timeout=TIMEOUT)
    except httpx.ConnectError:
        raise WebError("El navegador aislado no está disponible (contenedor `browser` parado).")
    except httpx.HTTPError as e:
        raise WebError(f"El navegador aislado no respondió a tiempo ({type(e).__name__}).")
    if r.status_code == 429:
        raise WebError("El navegador aislado está ocupado con otras páginas; reintenta en unos segundos.")
    try:
        d = r.json()
    except ValueError:
        raise WebError("Respuesta no válida del navegador aislado.")
    if not d.get("ok"):
        raise WebError(f"El navegador no pudo abrir la página: {d.get('error') or 'error desconocido'}")
    text = (d.get("text") or "").strip()
    if text.startswith("Bloqueado:"):
        raise WebError(f"Dirección no permitida desde el navegador: {text[:160]}")
    shot = d.get("screenshot_b64")
    return {"final_url": d.get("final_url") or url, "status": int(d.get("status") or 0), "title": (d.get("title") or "").strip(),
            "description": d.get("description") or "", "text": text, "links": d.get("links") or [], "images": d.get("images") or [],
            "og_image": d.get("og_image") or "", "screenshot": base64.b64decode(shot) if shot else None,
            "blocked": d.get("blocked") or [], "elapsed_ms": d.get("elapsed_ms") or 0}
