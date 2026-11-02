"""Internet access for the chat agent: search through the private SearXNG instance and SSRF-safe page fetching.

Only public addresses are fetched: every hop (redirects included) is resolved and checked, and the connection is
pinned to the checked IP so DNS rebinding cannot reach the Spark, the tailnet or the Docker networks.

Many sites refuse automated clients (403/405/429, Cloudflare/Akamai challenges, Wikimedia's robot policy...). The page
reader therefore sends realistic browser headers, keeps cookies across redirects, retries 429/5xx with backoff, tries a
second header profile when blocked and finally falls back to scholarly metadata (OpenAlex / Europe PMC, for paper
pages) or to the Internet Archive copy, always telling the model where the text came from. Every fallback request goes
through the same SSRF checks.
"""
from __future__ import annotations

import codecs
import email.utils
import ipaddress
import random
import re
import socket
import threading
import time
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx

from .. import __version__
from ..settings_store import get_setting

MAX_BYTES = 6 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024  # papers and reports are often bigger than an HTML page
MAX_ERROR_BYTES = 256 * 1024      # enough to recognise a challenge page; the rest of an error body is discarded
MAX_REDIRECTS = 6
ALLOWED_PORTS = {80, 443, 8080, 8443}
TIMEOUT = httpx.Timeout(15, read=20)
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
BLOCK_STATUSES = {401, 403, 405, 406, 429, 451, 999}  # 999: LinkedIn
MAX_RETRY_WAIT = 8.0  # longer Retry-After values are not waited for: the model should try another source
PDF_PAGES = 60
ARCHIVE_PAUSE = 60  # seconds without asking the Wayback Machine after it refused us
ARCHIVE = "https://web.archive.org/web/2id_/"  # id_: original bytes, without the Wayback toolbar or rewritten links
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json",
              "application/xml", "text/xml", "application/rss+xml", "application/atom+xml", "text/csv")
TRY_ANOTHER = ("Prueba con otro resultado de la búsqueda u otra fuente sobre lo mismo (para artículos científicos: "
               "la versión en arXiv, PubMed/Europe PMC o el enlace DOI).")

# Engines per category (see infrastructure/searxng/settings.yml). Measured from the university network (Nov 2026):
# google cse, yahoo, yandex, bing, duckduckgo news, bing news, arxiv, openalex, crossref, semantic scholar, europepmc
# answer; brave (429), google (403), google news (403), qwant (403) and duckduckgo web (CAPTCHA) are blocked most of
# the time; pubmed is rate limited (NCBI) and yahoo news / startpage / mojeek are inactive in this SearXNG version.
ENGINES = {
    "web": "google cse,bing,yahoo,yandex,wikipedia,duckduckgo",
    "science": "arxiv,google scholar,semantic scholar,openalex,crossref,europepmc,pubmed",
    "news": "bing news,duckduckgo news,google news,wikinews",
    "code": "github,stackoverflow,mdn,google cse",
}

CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:157.0) Gecko/20100101 Firefox/157.0"
BOT_UA = f"SparkAIHub/{__version__} (private research assistant; +https://github.com/polila6/spark-ai-hub) httpx"
ACCEPT_ENCODING = "gzip, deflate"  # what httpx decodes here (no brotli/zstandard installed)
PROFILES = {
    "chrome": {"User-Agent": CHROME_UA,
               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
               "Accept-Language": "es-ES,es;q=0.9,en;q=0.8", "Accept-Encoding": ACCEPT_ENCODING,
               "Sec-Ch-Ua": '"Chromium";v="154", "Google Chrome";v="154", "Not.A/Brand";v="99"',
               "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"', "Upgrade-Insecure-Requests": "1",
               "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-User": "?1", "Sec-Fetch-Dest": "document"},
    "firefox": {"User-Agent": FIREFOX_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3", "Accept-Encoding": ACCEPT_ENCODING,
                "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"},
    # Honest identification: required by Wikimedia (it blocks browser-looking clients) and accepted by sites whose
    # bot protection only targets fake browsers (e.g. Springer).
    "bot": {"User-Agent": BOT_UA, "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Language": "es,en;q=0.8", "Accept-Encoding": ACCEPT_ENCODING},
}
NAV_HEADERS = ("Upgrade-Insecure-Requests", "Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-User", "Sec-Fetch-Dest")
BOT_FIRST = ("wikipedia.org", "wikimedia.org", "wiktionary.org", "wikidata.org", "wikisource.org", "wikibooks.org",
             "wikiquote.org", "wikinews.org", "wikiversity.org", "wikivoyage.org", "mediawiki.org",
             "api.openalex.org", "api.crossref.org", "ebi.ac.uk", "web.archive.org", "archive.org")

CHALLENGE_TITLE = re.compile(
    rb"<title[^>]*>\s*(?:just a moment|un momento|attention required|client challenge|checking your browser|"
    rb"comprobando (?:tu|su) navegador|access denied|acceso denegado|are you a (?:robot|human)|robot check|"
    rb"pardon our interruption|please verify|verify you are (?:a )?human|security check|ddos-guard|"
    rb"human verification|captcha|bot verification|request rejected|you have been blocked)", re.I)
CHALLENGE_MARKERS = (b"cf-chl-", b"cf_chl_opt", b"_Incapsula_Resource", b"awswaf", b"AwsWafIntegration",
                     b"captcha-delivery.com", b"px-captcha", b"perimeterx", b"Request unsuccessful. Incapsula",
                     b"g-recaptcha", b"hcaptcha.com/1/api.js", b"challenges.cloudflare.com")


class WebError(Exception):
    pass


class URLRefused(WebError):
    """The URL itself is not allowed (scheme, credentials, port, private address): never retried nor archived."""


# ------------------------------------------------------------------ search


def _searx(base: str, params: dict) -> dict:
    last: Exception | None = None
    for attempt in range(2):
        try:
            r = httpx.get(f"{base}/search", params=params, timeout=25, headers={"X-Real-IP": "127.0.0.1"})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            last = e
            if attempt == 0:
                time.sleep(1)
    raise WebError(f"El buscador no responde ({type(last).__name__}). Prueba de nuevo en un momento o abre "
                   "directamente con fetch_url una web que conozcas.") from last


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
    data = _searx(base, params)
    failed = {e[0]: e[1] for e in data.get("unresponsive_engines") or [] if isinstance(e, (list, tuple)) and len(e) > 1}
    if not data.get("results") and params["engines"] != ENGINES["web"]:
        # the specialised engines gave nothing (blocked or no match): the general ones usually still answer
        data = _searx(base, {**params, "engines": ENGINES["web"]})
        failed.update({e[0]: e[1] for e in data.get("unresponsive_engines") or [] if isinstance(e, (list, tuple)) and len(e) > 1})
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
    if not out and failed:
        raise WebError("Sin resultados: varios buscadores no han respondido ahora mismo ("
                       + ", ".join(f"{k}: {v}" for k, v in list(failed.items())[:6])
                       + "). Prueba otra consulta u otra categoría en un minuto, o abre con fetch_url una web conocida.")
    return out


# ------------------------------------------------------------------ fetching


def _public_ip(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise WebError(f"No se puede resolver {host} (el dominio no existe o el DNS no responde)") from e
    addrs = {i[4][0] for i in infos}
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise URLRefused(f"Dirección no permitida: {host} es una dirección privada o interna")
    return sorted(addrs, key=lambda a: ":" in a)[0]  # prefer IPv4


def _check_url(url: str) -> tuple[str, str, int]:
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise URLRefused("Solo se pueden abrir URLs http(s)")
    if u.username or u.password:
        raise URLRefused("URLs con credenciales no permitidas")
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError as e:
        raise URLRefused("Puerto no válido") from e
    if port not in ALLOWED_PORTS:
        raise URLRefused(f"Puerto {port} no permitido")
    return u.hostname, _public_ip(u.hostname, port), port


def _host_in(host: str, domains) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def profiles_for(host: str) -> tuple[str, ...]:
    """Header profiles to try, in order."""
    return ("bot", "chrome") if _host_in(host, BOT_FIRST) else ("chrome", "bot")


def build_headers(profile: str, host: str, port: int | None = None, accept: str | None = None,
                  cookie: str | None = None) -> dict:
    h = dict(PROFILES.get(profile) or PROFILES["chrome"])
    h["Host"] = host if not port else f"{host}:{port}"
    if accept:  # API / raw file request, not a page navigation
        h["Accept"] = accept
        for k in NAV_HEADERS:
            h.pop(k, None)
        if "Sec-Fetch-Dest" in PROFILES.get(profile, {}):
            h.update({"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
    if cookie:
        h["Cookie"] = cookie
    return h


class CookieJar:
    """Minimal per-host cookie store (the connection is pinned to an IP, so httpx's own jar cannot be used)."""

    def __init__(self):
        self._c: dict[tuple[str, str], str] = {}

    def store(self, host: str, set_cookie: list[str]) -> None:
        host = host.lower()
        for raw in set_cookie:
            sc = SimpleCookie()
            try:
                sc.load(raw)
            except CookieError:
                continue
            for name, m in sc.items():
                dom = (m["domain"] or "").lower().lstrip(".")
                if dom and not (host == dom or host.endswith("." + dom)):
                    continue  # a site may not set cookies for another domain
                key = (dom or host, name)
                if m["max-age"] == "0" or m.value == "" or "1970" in (m["expires"] or ""):
                    self._c.pop(key, None)
                else:
                    self._c[key] = m.value

    def header(self, host: str) -> str | None:
        host = host.lower()
        pairs = [f"{n}={v}" for (d, n), v in self._c.items() if host == d or host.endswith("." + d)]
        return "; ".join(pairs) or None


def retry_delay(status: int, headers, attempt: int) -> float | None:
    """Seconds to wait before retrying a response, or None when it should not be retried."""
    if status not in RETRY_STATUSES:
        return None
    ra = (headers or {}).get("retry-after") if hasattr(headers, "get") else None
    if ra:
        ra = ra.strip()
        wait: float | None = None
        if ra.isdigit():
            wait = float(ra)
        else:
            try:
                dt = email.utils.parsedate_to_datetime(ra)
                wait = max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                wait = None
        if wait is not None:
            return wait if wait <= MAX_RETRY_WAIT else None
    return min(MAX_RETRY_WAIT, 0.8 * 2 ** attempt + random.uniform(0, 0.4))


def _net_reason(e: Exception) -> str:
    s = str(e)
    if isinstance(e, httpx.TimeoutException):
        return "el servidor tardó demasiado en responder"
    if "CERTIFICATE" in s.upper() or "SSL" in s.upper():
        return "su certificado TLS no es válido"
    if isinstance(e, httpx.ConnectError):
        return "no acepta conexiones ahora mismo (caído o bloqueando esta red)"
    if isinstance(e, httpx.RemoteProtocolError):
        return "cortó la conexión sin responder"
    return f"error de red ({type(e).__name__})"


def _charset(ct: str) -> str | None:
    m = re.search(r"charset=[\"']?([\w.:-]+)", ct or "", re.I)
    if not m:
        return None
    try:
        return codecs.lookup(m.group(1)).name
    except LookupError:
        return None


def fetch(url: str, max_bytes: int = MAX_BYTES, accept: str | None = None, profile: str | None = None,
          jar: CookieJar | None = None, retries: int = 2, timeout: httpx.Timeout = TIMEOUT) -> dict:
    """GET a public URL. Returns {url, status, content_type, headers, body (bytes)}; raises WebError.

    4xx/5xx answers are returned (not raised) after retrying 429/5xx with backoff; error bodies are truncated."""
    jar = jar if jar is not None else CookieJar()
    attempt = 0
    while True:
        try:
            r = _fetch_once(url, max_bytes, accept, profile, jar, timeout)
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
            if attempt < min(retries, 1):
                attempt += 1
                time.sleep(1)
                continue
            raise WebError(f"No se pudo abrir {urlsplit(url).hostname}: {_net_reason(e)}") from e
        except httpx.HTTPError as e:
            raise WebError(f"No se pudo abrir {urlsplit(url).hostname}: {_net_reason(e)}") from e
        wait = retry_delay(r["status"], r["headers"], attempt) if attempt < retries else None
        if wait is None or is_challenge(r["body"]):
            return r
        attempt += 1
        time.sleep(wait)


def _fetch_once(url: str, max_bytes: int, accept: str | None, profile: str | None, jar: CookieJar,
                timeout: httpx.Timeout) -> dict:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host, ip, port = _check_url(current)
        u = urlsplit(current)
        netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{u.port}" if u.port else "")
        pinned = urlunsplit((u.scheme, netloc, u.path or "/", u.query, ""))
        headers = build_headers(profile or profiles_for(host)[0], host, u.port, accept, jar.header(host))
        with httpx.Client(timeout=timeout, follow_redirects=False) as c:
            with c.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as r:
                jar.store(host, r.headers.get_list("set-cookie"))
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                ct = r.headers.get("content-type", "")
                limit = max_bytes
                if r.status_code >= 400:
                    limit = min(max_bytes, MAX_ERROR_BYTES)
                elif max_bytes == MAX_BYTES and "pdf" in ct.lower():
                    limit = MAX_PDF_BYTES
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > limit:
                        if r.status_code >= 400:
                            del body[limit:]
                            break
                        raise WebError(f"La página supera {limit // (1024 * 1024)} MB")
                return {"url": current, "status": r.status_code, "content_type": ct,
                        "headers": {k.lower(): v for k, v in r.headers.items() if k.lower() != "set-cookie"},
                        "body": bytes(body)}
    raise WebError("Demasiadas redirecciones")


def is_challenge(body: bytes) -> bool:
    """Bot-protection interstitial (Cloudflare, Akamai, AWS WAF, DataDome, reCAPTCHA...) instead of the page."""
    if len(body) > 120_000:  # interstitials are small; a big page titled "CAPTCHA - Wikipedia" is an article
        return False
    if CHALLENGE_TITLE.search(body):
        return True
    return len(body) < 40_000 and any(m in body for m in CHALLENGE_MARKERS) and b"<article" not in body


def blocked_reason(r: dict) -> str | None:
    """Why a response is not the page (None when it is usable). The texts are shown to the model."""
    s = r["status"]
    challenge = s != 404 and is_challenge(r["body"])
    if s == 429:
        return "el sitio limita las peticiones (HTTP 429 Too Many Requests)"
    if challenge:
        return f"el sitio muestra una verificación anti-bots en lugar de la página (HTTP {s})"
    if s == 401:
        return "la página requiere iniciar sesión (HTTP 401)"
    if s == 403:
        return "el sitio bloquea el acceso automatizado (HTTP 403 Forbidden)"
    if s == 405:
        return "el servidor rechazó la petición (HTTP 405 Method Not Allowed; suele ser un cortafuegos anti-bots)"
    if s == 406:
        return "el servidor rechazó la petición (HTTP 406 Not Acceptable)"
    if s == 451:
        return "contenido no disponible por motivos legales en esta región (HTTP 451)"
    if s == 999:
        return "el sitio exige iniciar sesión para verlo (HTTP 999, LinkedIn)"
    if s in (404, 410):
        return f"la página no existe o se ha retirado (HTTP {s})"
    if s >= 500:
        return f"el servidor del sitio falla o está saturado (HTTP {s})"
    if s >= 400:
        return f"la página respondió HTTP {s}"
    return None


# ------------------------------------------------------------------ page text


def html_to_text(html: bytes | str, base_url: str = "", encoding: str | None = None) -> tuple[str, str]:
    """Readable text of a web page (title, markdown-ish text). Scripts, menus and footers are dropped."""
    from bs4 import BeautifulSoup

    if isinstance(html, bytes) and encoding:
        if encoding in ("latin-1", "iso8859-1", "cp1252", "ascii"):  # common server default; trust real UTF-8 bodies
            try:
                html.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                pass
        soup = BeautifulSoup(html, "lxml", from_encoding=encoding)
    else:
        soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
    desc = ""
    for attrs in ({"name": "citation_abstract"}, {"name": "dc.description"}, {"property": "og:description"},
                  {"name": "description"}, {"name": "twitter:description"}):
        m = soup.find("meta", attrs=attrs)
        if m and len(m.get("content") or "") > len(desc):
            desc = re.sub(r"\s+", " ", m["content"]).strip()
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "aside", "button",
                   "template", "canvas"]):
        t.decompose()
    for t in soup.select("[role=navigation], [aria-hidden=true], .cookie, .cookies, #cookie-banner, .advert, .ads"):
        t.decompose()
    body = soup.body or soup
    articles = soup.find_all("article")
    # a home page has many <article> teasers: only a single one is "the" content
    root = soup.find("main") or soup.find(attrs={"role": "main"}) or (articles[0] if len(articles) == 1 else None) or body
    text = _extract(root)
    if root is not body and len(text) < 500:
        whole = _extract(body)
        if len(whole) > 2 * len(text):
            text = whole
    if len(text) < 200 and len(desc) > len(text):
        text = (text + "\n\n" if text else "") + desc
    return title, text


def _extract(root) -> str:
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
    return text


_PDFIUM = threading.Lock()  # pdfium is not thread-safe: concurrent fetch_url calls failed with "Failed to load page"


def pdf_text(body: bytes) -> tuple[str, int]:
    import pypdfium2 as pdfium

    with _PDFIUM:
        try:
            pdf = pdfium.PdfDocument(body)
            try:
                n = len(pdf)
                pages = []
                for i in range(min(n, PDF_PAGES)):
                    page = pdf[i]
                    tp = page.get_textpage()
                    pages.append(tp.get_text_range())
                    tp.close()
                    page.close()
            finally:
                pdf.close()
        except Exception as e:  # noqa: BLE001
            raise WebError(f"El PDF no se puede leer ({e}). {TRY_ANOTHER}") from e
    text = "\n\n".join(f"--- página {i + 1} ---\n{t.strip()}" for i, t in enumerate(pages) if t.strip())
    if n > PDF_PAGES:
        text += f"\n\n[… el PDF tiene {n} páginas; solo se han leído las {PDF_PAGES} primeras]"
    return text, n


def _to_page(r: dict) -> dict:
    """Convert a successful response to {url, title, text, content_type}; raises WebError for unreadable content."""
    ct = r["content_type"].split(";")[0].strip().lower()
    body = r["body"]
    if ct == "application/pdf" or body[:5] == b"%PDF-":
        text, _ = pdf_text(body)
        if not text:
            raise WebError("El PDF no contiene texto extraíble (probablemente es un escaneo sin OCR). " + TRY_ANOTHER)
        return {"url": r["url"], "title": unquote(urlsplit(r["url"]).path.rsplit("/", 1)[-1]), "text": text,
                "content_type": "application/pdf"}
    if not ct or any(ct.startswith(t) for t in TEXT_TYPES):
        if ct in ("text/html", "application/xhtml+xml") or (not ct and b"<html" in body[:2000].lower()):
            title, text = html_to_text(body, r["url"], _charset(r["content_type"]))
        else:
            title, text = "", body.decode(_charset(r["content_type"]) or "utf-8", errors="replace")
        return {"url": r["url"], "title": title, "text": text.strip(), "content_type": ct or "text/html"}
    raise WebError(f"Tipo de contenido no legible: {ct}. " + TRY_ANOTHER)


# ------------------------------------------------------------------ fallbacks: scholarly metadata, Internet Archive

DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s?#\"'<>]+)")


def paper_ids(url: str, body: bytes = b"") -> dict:
    """Identifiers of a scholarly article from its URL (or its citation_* meta tags): doi, pmid, pmcid, pii."""
    u = urlsplit(url)
    host, path = (u.hostname or "").lower(), unquote(u.path)
    ids: dict = {}
    if host == "pubmed.ncbi.nlm.nih.gov" and (m := re.match(r"/(\d{5,9})/?$", path)):
        ids["pmid"] = m.group(1)
    elif host.endswith("ncbi.nlm.nih.gov") and (m := re.match(r"/pubmed/(\d{5,9})", path)):
        ids["pmid"] = m.group(1)
    if host.endswith("ncbi.nlm.nih.gov") and (m := re.search(r"/(PMC\d{4,10})", path, re.I)):
        ids["pmcid"] = m.group(1).upper()
    if host.endswith("europepmc.org") and (m := re.search(r"/(?:article|abstract)/(MED|PMC)/(\w+)", path, re.I)):
        if m.group(1).upper() == "MED":
            ids["pmid"] = m.group(2)
        else:
            ids["pmcid"] = "PMC" + re.sub(r"^PMC", "", m.group(2), flags=re.I)
    # Elsevier PII: sciencedirect.com/science/article/pii/S0893608014002135, cell.com/.../fulltext/S0092-8674(20)30262-2,
    # thelancet.com/.../article/PIIS0140-6736(20)30183-5/fulltext
    if m := re.search(r"(?:/pii/|/PII|/(?:fulltext|abstract)/)(S?[0-9X][0-9X()-]{14,24})", path, re.I):
        if _host_in(host, ("sciencedirect.com", "cell.com", "thelancet.com", "elsevier.com", "jci.org")):
            ids["pii"] = re.sub(r"[()-]", "", m.group(1)).upper()
    if _host_in(host, ("jstor.org",)) and (m := re.match(r"/stable/(?:pdf/)?(\d{4,10})", path)):
        ids["doi"] = f"10.2307/{m.group(1)}"
    if "doi" in ids:
        pass
    elif host in ("doi.org", "dx.doi.org", "www.doi.org") and path.strip("/").startswith("10."):
        ids["doi"] = path.strip("/")
    elif m := DOI_RE.search(path):
        ids["doi"] = m.group(1)
    elif body:
        m = re.search(rb"<meta[^>]+name=[\"'](?:citation_doi|dc\.identifier|prism\.doi)[\"'][^>]+content=[\"'](?:doi:)?"
                      rb"(10\.\d{4,9}/[^\"'<>\s]+)", body[:300_000], re.I)
        if m:
            ids["doi"] = m.group(1).decode(errors="ignore")
    if "doi" in ids:
        d = re.sub(r"(?:/(?:full|abstract|pdf|epdf|html|summary|meta|references|figures|suppl\w*)|\.pdf|\.html?|[.,;)\]])+$",
                   "", ids["doi"], flags=re.I)
        ids["doi"] = d
    return ids


def _get_json(url: str):
    import json

    r = fetch(url, max_bytes=3_000_000, accept="application/json", profile="bot", retries=1,
              timeout=httpx.Timeout(10, read=15))
    if r["status"] != 200:
        return None
    try:
        return json.loads(r["body"])
    except ValueError:
        return None


def _inverted_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


def _paper_page(ids: dict) -> dict | None:
    """Bibliographic record + abstract from Europe PMC (biomedicine) or OpenAlex (any field)."""
    from bs4 import BeautifulSoup

    epmc = None
    if ids.get("pmid") or ids.get("pmcid"):
        q = f"EXT_ID:{ids['pmid']} AND SRC:MED" if ids.get("pmid") else f"PMCID:{ids['pmcid']}"
        d = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?resultType=core&format=json&pageSize=1&query="
                      + quote(q))
        hits = ((d or {}).get("resultList") or {}).get("result") or []
        epmc = hits[0] if hits else None
        if epmc and epmc.get("doi") and not ids.get("doi"):
            ids = {**ids, "doi": epmc["doi"]}
    if not ids.get("doi") and ids.get("pii"):
        d = _get_json(f"https://api.crossref.org/works?rows=1&select=DOI&filter=alternative-id:{quote(ids['pii'])}")
        items = ((d or {}).get("message") or {}).get("items") or []
        if items:
            ids = {**ids, "doi": items[0]["DOI"]}
    oa = _get_json(f"https://api.openalex.org/works/doi:{quote(ids['doi'], safe='/')}") if ids.get("doi") else None
    if not epmc and not oa:
        return None
    title = (epmc or {}).get("title") or (oa or {}).get("title") or ""
    abstract = BeautifulSoup((epmc or {}).get("abstractText") or "", "lxml").get_text(" ", strip=True) \
        or _inverted_abstract((oa or {}).get("abstract_inverted_index"))
    if oa:
        names = [a.get("author", {}).get("display_name") for a in oa.get("authorships") or []]
        venue = (((oa.get("primary_location") or {}).get("source") or {}).get("display_name")) or ""
        year = oa.get("publication_year")
    else:
        names, venue, year = (epmc.get("authorString") or "").split(", "), "", epmc.get("pubYear")
    if epmc and not venue:
        venue = ((epmc.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
    names = [n for n in names if n]
    lines = [f"# {title.rstrip('.')}"]
    if names:
        lines.append("Autores: " + ", ".join(names[:12]) + (f" y {len(names) - 12} más" if len(names) > 12 else ""))
    lines.append(" · ".join(x for x in [venue, str(year or "")] if x))
    if ids.get("doi"):
        lines.append(f"DOI: https://doi.org/{ids['doi']}")
    if epmc and epmc.get("pmid"):
        lines.append(f"PMID: {epmc['pmid']}" + (f" · {epmc['pmcid']}" if epmc.get("pmcid") else ""))
    if oa and oa.get("cited_by_count") is not None:
        lines.append(f"Citas (OpenAlex): {oa['cited_by_count']}")
    lines.append("\n## Resumen\n" + (abstract or "(la fuente no incluye el resumen)"))
    oa_urls = []
    if oa:
        best = oa.get("best_oa_location") or {}
        oa_urls = [x for x in [best.get("pdf_url"), best.get("landing_page_url"), (oa.get("open_access") or {}).get("oa_url")] if x]
    if epmc:
        oa_urls += [u.get("url") for u in ((epmc.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
                    if u.get("availabilityCode") in ("OA", "F") and u.get("url")]
    oa_urls = list(dict.fromkeys(oa_urls))[:3]
    if oa_urls:
        lines.append("\nTexto completo en acceso abierto (ábrelo con fetch_url): " + " ; ".join(oa_urls))
    return {"title": title, "text": "\n".join(lines), "via": "europepmc" if epmc else "openalex"}


_archive_pause_until = 0.0  # the Wayback Machine refuses connections when hit too often: back off for a while


def archive_url(url: str) -> str:
    return ARCHIVE + url


def _archive_page(url: str) -> tuple[dict | None, str]:
    """Latest Internet Archive copy of url: (page, '') or (None, reason)."""
    global _archive_pause_until
    if time.monotonic() < _archive_pause_until:
        return None, "el Internet Archive no está disponible ahora mismo"
    try:
        r = fetch(archive_url(url), profile="bot", retries=1, timeout=httpx.Timeout(10, read=20))
    except URLRefused:
        raise
    except WebError as e:
        if isinstance(e.__cause__, httpx.ConnectError):  # refusing connections = rate limited; a timeout is not
            _archive_pause_until = time.monotonic() + ARCHIVE_PAUSE
        return None, "el Internet Archive no responde ahora mismo"
    if r["status"] == 429:
        _archive_pause_until = time.monotonic() + ARCHIVE_PAUSE
        return None, "el Internet Archive limita las peticiones ahora mismo"
    if r["status"] >= 400 or is_challenge(r["body"]):
        return None, "tampoco hay una copia utilizable en el Internet Archive"
    try:
        page = _to_page(r)
    except WebError:
        return None, "la copia del Internet Archive no se puede leer"
    if not page["text"]:
        return None, "la copia del Internet Archive no tiene texto"
    m = re.search(r"/web/(\d{8})(\d*)id_/", r["url"])
    page["snapshot"] = r["url"].replace("id_/", "/", 1) if m else r["url"]
    page["snapshot_date"] = f"{m.group(1)[6:8]}/{m.group(1)[4:6]}/{m.group(1)[:4]}" if m else ""
    return page, ""


# ------------------------------------------------------------------ the page reader used by the agent


def read_page(url: str) -> dict:
    """Fetch and convert a page or PDF to text: {url, title, text, content_type[, via, note]}.

    via is "direct", "openalex"/"europepmc" (bibliographic record of a paper whose page is blocked) or "archive"; the
    note (also prepended to text) tells the model where the text comes from. Raises WebError with a message that says
    what failed and what to try instead."""
    host = urlsplit(url).hostname or ""
    jar = CookieJar()
    reason, body, archive = "", b"", True
    try:
        _check_url(url)
        profiles = profiles_for(host)
    except URLRefused:
        raise
    except WebError as e:  # unresolvable host: maybe a dead site that the archive still has
        reason, profiles = str(e), ()
    for profile in profiles:
        try:
            r = fetch(url, profile=profile, jar=jar)
        except URLRefused:
            raise
        except WebError as e:
            reason = str(e).split(": ", 1)[-1]
            break
        body = r["body"]
        reason = blocked_reason(r) or ""
        if not reason:
            page = _to_page(r)
            if page["text"]:
                return {**page, "via": "direct"}
            reason = "la página no tiene texto legible sin ejecutar JavaScript"
            archive = False  # the archived copy is the same empty shell
            break
        if r["status"] not in BLOCK_STATUSES and not is_challenge(body):
            break  # 404, 5xx after retries...: another User-Agent will not help
    ids = paper_ids(url, body)
    if ids:
        try:
            p = _paper_page(ids)
        except WebError:
            p = None
        if p:
            note = (f"[Nota: no se pudo leer {host}: {reason}. Esto es la ficha bibliográfica del artículo obtenida de "
                    f"{'Europe PMC' if p['via'] == 'europepmc' else 'OpenAlex'}: título, autores y resumen, no el texto "
                    "completo.]")
            return {"url": url, "title": p["title"], "text": note + "\n\n" + p["text"], "content_type": "text/markdown",
                    "via": p["via"], "note": note}
    archived, why = _archive_page(url) if archive else (None, "")
    if archived:
        note = (f"[Nota: no se pudo leer {host} directamente: {reason}. Este texto es la copia guardada en el Internet "
                f"Archive{' el ' + archived['snapshot_date'] if archived['snapshot_date'] else ''} "
                f"({archived['snapshot']}); puede no estar actualizada. Cita la URL original.]")
        return {"url": url, "title": archived["title"], "text": note + "\n\n" + archived["text"],
                "content_type": archived["content_type"], "via": "archive", "note": note,
                "archive_url": archived["snapshot"]}
    raise WebError(f"No se pudo leer {url}: {reason}{'; ' + why if why else ''}. {TRY_ANOTHER}")
