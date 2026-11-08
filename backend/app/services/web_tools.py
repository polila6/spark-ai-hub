"""Bigger web tools for the assistant, built on services/web.py (public addresses only, size limits, no redirects into private
networks): read many pages at once, call public APIs (GET/POST), crawl a site politely, and keep what was fetched as files the
sandbox scripts can then process (the sandbox itself has no network)."""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urldefrag, urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from . import web

MAX_URLS = 10
MAX_PARALLEL = 5
MAX_API_BYTES = 8 * 1024 * 1024
MAX_BODY_SEND = 200 * 1024
CRAWL_SECONDS = 120.0
CRAWL_DELAY = 0.4  # seconds between requests to the same host
FORBIDDEN_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "cookie", "proxy-authorization", "upgrade", "te"}
SKIP_EXT = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|ico|css|js|mjs|woff2?|ttf|eot|mp[34]|avi|mov|zip|gz|tar|rar|7z|exe|dmg|iso|bin)(?:\?|$)", re.I)


class ToolError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ many pages at once

def read_many(urls: list[str], max_workers: int = MAX_PARALLEL) -> list[dict]:
    """Read each URL (read_page) in parallel; one failing does not stop the rest. Result order = input order."""
    urls = [u.strip() for u in urls if str(u).strip()][:MAX_URLS]
    if not urls:
        raise ToolError("Falta la lista de URLs")

    def one(u: str) -> dict:
        try:
            p = web.read_page(u)
            return {"url": u, "ok": True, "title": p["title"] or u, "text": p["text"], "via": p.get("via"), "final_url": p["url"]}
        except web.WebError as e:
            return {"url": u, "ok": False, "error": str(e)[:300]}
        except Exception as e:  # noqa: BLE001
            return {"url": u, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]}

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(urls)))) as ex:
        return list(ex.map(one, urls))


def excerpt(text: str, query: str | None, limit: int) -> str:
    """The first `limit` characters, or — with a query — the paragraphs that mention its words most."""
    if len(text) <= limit:
        return text
    if not query:
        return text[:limit] + "\n[... recortado]"
    words = set(re.findall(r"\w{3,}", query.lower()))
    paras = [p for p in re.split(r"\n(?=#)|\n\n", text) if p.strip()]
    order = sorted(range(len(paras)), key=lambda i: -sum(paras[i].lower().count(w) for w in words))
    pick, size = set(), 0
    for i in order:
        if size + len(paras[i]) > limit:
            continue
        pick.add(i)
        size += len(paras[i])
    return "\n\n".join(paras[i] for i in sorted(pick)) or text[:limit]


# ------------------------------------------------------------------ API calls

def clean_headers(headers: dict | None) -> dict:
    out = {}
    for k, v in (headers or {}).items():
        k = str(k).strip()
        if not k or k.lower() in FORBIDDEN_HEADERS or any(c in k + str(v) for c in "\r\n"):
            continue
        out[k] = str(v)[:2000]
    return out


def request(url: str, method: str = "GET", params: dict | None = None, headers: dict | None = None, json_body=None,
            form: dict | None = None) -> dict:
    """One HTTP call to a public API. Returns {status, content_type, body, url}; HTTP errors come back as data (status ≥ 400)."""
    method = (method or "GET").upper()
    if method not in ("GET", "POST"):
        raise ToolError("Solo se admiten GET y POST")
    if params:
        sep = "&" if urlsplit(url).query else "?"
        url = url + sep + urlencode({k: v if not isinstance(v, (list, tuple)) else ",".join(map(str, v)) for k, v in params.items()}, doseq=True)
    hdrs = clean_headers(headers)
    content = None
    if method == "POST":
        if json_body is not None:
            content = json.dumps(json_body, ensure_ascii=False).encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif form:
            content = urlencode({k: str(v) for k, v in form.items()}).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if content is not None and len(content) > MAX_BODY_SEND:
            raise ToolError("El cuerpo de la petición supera 200 KB")
    hdrs.setdefault("Accept", "application/json, text/csv, text/plain, */*;q=0.5")
    try:
        r = web.fetch(url, max_bytes=MAX_API_BYTES, accept=hdrs["Accept"], profile="bot", retries=1, method=method,
                      content=content, extra_headers=hdrs)
    except web.WebError as e:
        raise ToolError(str(e)) from e
    return r


def render_body(body: bytes, content_type: str, limit: int) -> str:
    """Readable form of an API answer: JSON pretty-printed (long lists summarised), text as is."""
    ct = (content_type or "").split(";")[0].lower()
    text = body.decode("utf-8", errors="replace")
    if "json" in ct or text.lstrip()[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            return text[:limit]
        pretty = json.dumps(data, ensure_ascii=False, indent=1)
        if len(pretty) <= limit:
            return pretty
        return pretty[:limit] + f"\n[... recortado: {len(pretty):,} caracteres en total; guarda la respuesta con save_to para procesarla con un script]".replace(",", ".")
    return text[:limit] + ("\n[... recortado]" if len(text) > limit else "")


# ------------------------------------------------------------------ crawling

def links_of(html: bytes, base: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if not h or h.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        u = urldefrag(urljoin(base, h))[0]
        if urlsplit(u).scheme in ("http", "https") and u not in seen and not SKIP_EXT.search(u):
            seen.add(u)
            out.append(u)
    return out


def _robots(start: str) -> RobotFileParser | None:
    u = urlsplit(start)
    try:
        r = web.fetch(f"{u.scheme}://{u.netloc}/robots.txt", max_bytes=256 * 1024, profile="bot", retries=0)
    except web.WebError:
        return None
    if r["status"] != 200:
        return None
    rp = RobotFileParser()
    rp.parse(r["body"].decode("utf-8", errors="replace").splitlines())
    return rp


def sitemap_urls(start: str, limit: int = 200) -> list[str]:
    """URLs listed in the site's sitemap (robots.txt Sitemap: lines, else /sitemap.xml); sitemap indexes are followed one level."""
    u = urlsplit(start)
    base = f"{u.scheme}://{u.netloc}"
    cands = []
    try:
        r = web.fetch(base + "/robots.txt", max_bytes=256 * 1024, profile="bot", retries=0)
        if r["status"] == 200:
            cands = re.findall(r"(?im)^sitemap:\s*(\S+)", r["body"].decode("utf-8", errors="replace"))
    except web.WebError:
        pass
    cands = cands[:3] or [base + "/sitemap.xml"]
    urls: list[str] = []
    for sm in cands:
        try:
            r = web.fetch(sm, max_bytes=3 * 1024 * 1024, profile="bot", retries=0)
        except web.WebError:
            continue
        if r["status"] != 200:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r["body"].decode("utf-8", errors="replace"))
        if locs and all(re.search(r"sitemap.*\.xml(\.gz)?$", x, re.I) for x in locs[:5]):  # an index: read its first sitemaps
            for sub in locs[:3]:
                try:
                    rr = web.fetch(sub, max_bytes=3 * 1024 * 1024, profile="bot", retries=0)
                    urls += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rr["body"].decode("utf-8", errors="replace")) if rr["status"] == 200 else []
                except web.WebError:
                    continue
        else:
            urls += locs
        if len(urls) >= limit:
            break
    return urls[:limit]


def crawl(start: str, max_pages: int = 15, depth: int = 2, same_host: bool = True, pattern: str | None = None,
          use_sitemap: bool = False, on_page=None, should_stop=None) -> dict:
    """Breadth-first read of a site: {pages: [{url, title, text}], skipped: {...}, seconds}. Honours robots.txt, waits between
    requests to the same host and stops after CRAWL_SECONDS. `pattern` is a regular expression the URL must match."""
    max_pages = max(1, min(int(max_pages or 15), 40))
    depth = max(0, min(int(depth if depth is not None else 2), 3))
    try:
        web._check_url(start)
    except web.WebError as e:
        raise ToolError(str(e)) from e
    rx = re.compile(pattern, re.I) if pattern else None
    host = (urlsplit(start).hostname or "").lower()
    robots = _robots(start)
    queue: list[tuple[str, int]] = [(start, 0)]
    if use_sitemap:
        queue += [(u, 1) for u in sitemap_urls(start) if u != start]
    seen = {urldefrag(start)[0]}
    pages: list[dict] = []
    skipped = {"robots.txt": 0, "otro dominio": 0, "no coincide": 0, "error": 0}
    t0 = time.monotonic()
    last = 0.0
    while queue and len(pages) < max_pages and time.monotonic() - t0 < CRAWL_SECONDS:
        if should_stop and should_stop():
            break
        url, d = queue.pop(0)
        if robots is not None and not robots.can_fetch(web.BOT_UA, url):
            skipped["robots.txt"] += 1
            continue
        wait = CRAWL_DELAY - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        try:
            r = web.fetch(url, profile="chrome", retries=1)
        except web.WebError:
            skipped["error"] += 1
            continue
        if r["status"] >= 400:
            skipped["error"] += 1
            continue
        try:
            page = web._to_page(r)
        except web.WebError:
            skipped["error"] += 1
            continue
        if page["text"] and (rx is None or rx.search(url) or url == start):
            pages.append({"url": r["url"], "title": page["title"] or r["url"], "text": page["text"], "depth": d})
            if on_page:
                on_page(len(pages), max_pages, r["url"])
        if d < depth and "html" in (r["content_type"] or "").lower():
            for link in links_of(r["body"], r["url"]):
                key = urldefrag(link)[0]
                if key in seen:
                    continue
                if same_host and (urlsplit(link).hostname or "").lower() != host:
                    skipped["otro dominio"] += 1
                    seen.add(key)
                    continue
                if rx is not None and not rx.search(link):
                    skipped["no coincide"] += 1
                    seen.add(key)
                    continue
                seen.add(key)
                queue.append((link, d + 1))
    return {"pages": pages, "skipped": {k: v for k, v in skipped.items() if v}, "queued": len(queue),
            "seconds": round(time.monotonic() - t0, 1)}


def slug_for(url: str, title: str = "") -> str:
    u = urlsplit(url)
    base = (u.path.strip("/").replace("/", "-") or "index") + (("-" + u.query[:30]) if u.query else "")
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")[:80]
    return s or "pagina"
