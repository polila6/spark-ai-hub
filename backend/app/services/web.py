"""Internet access for the chat agent: search through the private SearXNG instance and SSRF-safe page fetching.

Only public addresses are fetched: every hop (redirects included) is resolved and checked, and the connection is
pinned to the checked IP so DNS rebinding cannot reach the Spark, the tailnet or the Docker networks.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..settings_store import get_setting

MAX_BYTES = 6 * 1024 * 1024
MAX_REDIRECTS = 5
ALLOWED_PORTS = {80, 443, 8080, 8443}
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) SparkAIHub/0.3 (+private research assistant)"
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json",
              "application/xml", "text/xml", "application/rss+xml", "application/atom+xml", "text/csv")

ENGINES = {
    "web": "google cse,yahoo,yandex,wikipedia,duckduckgo,brave",
    "science": "arxiv,google scholar,semantic scholar,openalex,crossref,pubmed",
    "news": "yahoo news,google news",
    "code": "github,stackoverflow,mdn,google cse",
}


class WebError(Exception):
    pass


# ------------------------------------------------------------------ search


def search(db, query: str, category: str = "web", language: str | None = None, limit: int = 8,
           time_range: str | None = None) -> list[dict]:
    if not get_setting(db, "web_search_enabled"):
        raise WebError("La búsqueda web está desactivada por el administrador")
    params = {"q": query[:400], "format": "json", "engines": ENGINES.get(category, ENGINES["web"]), "safesearch": 1}
    if language and language != "auto":
        params["language"] = language
    if time_range in ("day", "week", "month", "year"):
        params["time_range"] = time_range
    base = (get_setting(db, "searxng_url") or "http://searxng:8080").rstrip("/")
    try:
        r = httpx.get(f"{base}/search", params=params, timeout=25, headers={"X-Real-IP": "127.0.0.1"})
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise WebError(f"El buscador no responde ({type(e).__name__})") from e
    seen: set[str] = set()
    out: list[dict] = []
    for x in data.get("results") or []:
        url = x.get("url") or ""
        key = url.split("#")[0].rstrip("/")
        if not url.startswith(("http://", "https://")) or key in seen:
            continue
        seen.add(key)
        out.append({"title": (x.get("title") or url)[:200], "url": url, "snippet": re.sub(r"\s+", " ", x.get("content") or "")[:400],
                    "engine": x.get("engine"), "published": (x.get("publishedDate") or "")[:10] or None})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ fetching


def _public_ip(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WebError(f"No se puede resolver {host}") from e
    addrs = {i[4][0] for i in infos}
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise WebError(f"Dirección no permitida: {host} es una dirección privada o interna")
    return sorted(addrs, key=lambda a: ":" in a)[0]  # prefer IPv4


def _check_url(url: str) -> tuple[str, str, int]:
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise WebError("Solo se pueden abrir URLs http(s)")
    if u.username or u.password:
        raise WebError("URLs con credenciales no permitidas")
    port = u.port or (443 if u.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise WebError(f"Puerto {port} no permitido")
    return u.hostname, _public_ip(u.hostname, port), port


def fetch(url: str, max_bytes: int = MAX_BYTES, accept: str | None = None) -> dict:
    """GET a public URL. Returns {url, status, content_type, body (bytes)}; raises WebError."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host, ip, port = _check_url(current)
        u = urlsplit(current)
        netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{u.port}" if u.port else "")
        pinned = urlunsplit((u.scheme, netloc, u.path or "/", u.query, ""))
        headers = {"Host": host if not u.port else f"{host}:{u.port}", "User-Agent": USER_AGENT,
                   "Accept": accept or "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
                   "Accept-Language": "es,en;q=0.8"}
        try:
            with httpx.Client(timeout=httpx.Timeout(15, read=20), follow_redirects=False) as c:
                with c.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise WebError(f"La página supera {max_bytes // (1024 * 1024)} MB")
                    return {"url": current, "status": r.status_code, "content_type": r.headers.get("content-type", ""),
                            "body": bytes(body)}
        except httpx.HTTPError as e:
            raise WebError(f"No se pudo abrir {host}: {type(e).__name__}") from e
    raise WebError("Demasiadas redirecciones")


def html_to_text(html: bytes | str, base_url: str = "") -> tuple[str, str]:
    """Readable text of a web page (title, markdown-ish text). Scripts, menus and footers are dropped."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "aside", "button",
                   "template", "canvas"]):
        t.decompose()
    for t in soup.select("[role=navigation], [aria-hidden=true], .cookie, .cookies, #cookie-banner, .advert, .ads"):
        t.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines: list[str] = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "th", "blockquote", "dt", "dd", "figcaption"]):
        if el.find_parent(["li", "td", "th", "blockquote", "pre"]) and el.name not in ("pre",):
            continue
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            lines.append("#" * int(el.name[1]) + " " + txt)
        elif el.name == "li":
            lines.append("- " + txt)
        elif el.name == "pre":
            lines.append("```\n" + el.get_text()[:4000].strip("\n") + "\n```")
        else:
            lines.append(txt)
    text = "\n".join(lines)
    plain = re.sub(r"\n{3,}", "\n\n", root.get_text("\n", strip=True))
    if len(text) < 0.3 * len(plain):  # pages without semantic markup (text in bare divs)
        text = plain
    return title, text


def read_page(url: str) -> dict:
    """Fetch and convert a page or PDF to text: {url, title, text, content_type}."""
    r = fetch(url)
    ct = r["content_type"].split(";")[0].strip().lower()
    if r["status"] >= 400:
        raise WebError(f"La página respondió {r['status']}")
    body = r["body"]
    if ct == "application/pdf" or body[:5] == b"%PDF-":
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(body)
            pages = [pdf[i].get_textpage().get_text_range() for i in range(min(len(pdf), 60))]
        except Exception as e:  # noqa: BLE001
            raise WebError(f"PDF ilegible: {e}") from e
        text = "\n\n".join(f"--- página {i + 1} ---\n{t.strip()}" for i, t in enumerate(pages) if t.strip())
        return {"url": r["url"], "title": urlsplit(r["url"]).path.rsplit("/", 1)[-1], "text": text, "content_type": "application/pdf"}
    if not ct or any(ct.startswith(t) for t in TEXT_TYPES):
        if ct in ("text/html", "application/xhtml+xml") or (not ct and b"<html" in body[:2000].lower()):
            title, text = html_to_text(body, r["url"])
        else:
            title, text = "", body.decode(errors="replace")
        return {"url": r["url"], "title": title, "text": text.strip(), "content_type": ct or "text/html"}
    raise WebError(f"Tipo de contenido no legible: {ct}")
