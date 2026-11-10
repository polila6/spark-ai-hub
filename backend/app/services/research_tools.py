"""Scholarly and page-picture tools for the assistant: papers (OpenAlex, Crossref), BibTeX from a DOI, and the pictures of a
web page with their captions. Public addresses only, through services/web.py (size limits, no redirects into private networks)."""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode, urljoin, urlsplit

from . import web

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
MAX_DOIS = 12
SKIP_IMG = re.compile(r"(?:logo|icon|sprite|avatar|pixel|tracking|badge|button|banner|spacer|blank|loading|emoji|favicon|"
                      r"\.svg(?:\?|$)|\.gif(?:\?|$)|/static/images/(?:footer|icons)|1x1)", re.I)


class ToolError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def _get_json(url: str, what: str) -> dict:
    try:
        r = web.fetch(url, max_bytes=4 * 1024 * 1024, accept="application/json", profile="bot", retries=1)
    except web.WebError as e:
        raise ToolError(f"{what}: {e}") from e
    if r["status"] == 429:
        raise ToolError(f"{what} limita las consultas ahora mismo (429)", "Espera un momento o usa source=crossref/openalex")
    if r["status"] != 200:
        raise ToolError(f"{what} respondió {r['status']}")
    try:
        return json.loads(r["body"].decode("utf-8", "replace"))
    except ValueError as e:
        raise ToolError(f"Respuesta no válida de {what}") from e


# ------------------------------------------------------------------ papers

def _abstract(inv: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}."""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for w, ps in inv.items():
        for p in ps:
            pos[p] = w
    return " ".join(pos[i] for i in sorted(pos))


def _doi(v: str | None) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (v or "").strip(), flags=re.I)


def openalex(query: str, limit: int, year_from: int | None, year_to: int | None, open_access: bool) -> list[dict]:
    flt = []
    if year_from:
        flt.append(f"from_publication_date:{int(year_from)}-01-01")
    if year_to:
        flt.append(f"to_publication_date:{int(year_to)}-12-31")
    if open_access:
        flt.append("is_oa:true")
    q = {"search": query, "per-page": limit}
    if flt:
        q["filter"] = ",".join(flt)
    data = _get_json("https://api.openalex.org/works?" + urlencode(q), "OpenAlex")
    out = []
    for w in data.get("results") or []:
        auth = [(a.get("author") or {}).get("display_name") for a in w.get("authorships") or []]
        auth = [a for a in auth if a]
        loc = w.get("primary_location") or {}
        oa = w.get("open_access") or {}
        out.append({"title": (w.get("title") or "").strip(), "authors": auth[:4] + (["et al."] if len(auth) > 4 else []),
                    "year": w.get("publication_year"), "venue": ((loc.get("source") or {}).get("display_name") or ""),
                    "doi": _doi(w.get("doi")), "cited_by": w.get("cited_by_count"), "type": w.get("type"),
                    "abstract": _abstract(w.get("abstract_inverted_index"))[:700], "oa_url": oa.get("oa_url") or "",
                    "source": "OpenAlex"})
    return out


def crossref(query: str, limit: int, year_from: int | None, year_to: int | None) -> list[dict]:
    q = {"query.bibliographic": query, "rows": limit, "select": "DOI,title,author,issued,container-title,type,is-referenced-by-count"}
    flt = []
    if year_from:
        flt.append(f"from-pub-date:{int(year_from)}")
    if year_to:
        flt.append(f"until-pub-date:{int(year_to)}")
    if flt:
        q["filter"] = ",".join(flt)
    data = _get_json("https://api.crossref.org/works?" + urlencode(q), "Crossref")
    out = []
    for w in (data.get("message") or {}).get("items") or []:
        auth = [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in w.get("author") or []]
        yr = ((w.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        out.append({"title": ((w.get("title") or [""])[0]).strip(), "authors": auth[:4] + (["et al."] if len(auth) > 4 else []),
                    "year": yr, "venue": (w.get("container-title") or [""])[0], "doi": w.get("DOI") or "",
                    "cited_by": w.get("is-referenced-by-count"), "type": w.get("type"), "abstract": "", "oa_url": "",
                    "source": "Crossref"})
    return out


def search_papers(query: str, limit: int = 8, year_from: int | None = None, year_to: int | None = None,
                  source: str = "auto", open_access: bool = False) -> list[dict]:
    query = (query or "").strip()[:300]
    if not query:
        raise ToolError("Falta la búsqueda")
    limit = max(1, min(int(limit or 8), 20))
    errors: list[str] = []
    for s in (["openalex", "crossref"] if source in ("auto", "") else [source]):
        try:
            res = openalex(query, limit, year_from, year_to, open_access) if s == "openalex" else \
                crossref(query, limit, year_from, year_to)
        except ToolError as e:
            errors.append(e.message)
            continue
        res = [r for r in res if r["title"]]
        if res:
            return res
    if errors:
        raise ToolError("No se pudo buscar artículos: " + "; ".join(errors))
    return []


def format_paper(i: int, p: dict) -> str:
    cite = f" · citado {p['cited_by']} veces" if p.get("cited_by") else ""
    bits = [f"{i}. {p['title']} — {', '.join(p['authors']) or 'autores desconocidos'} ({p.get('year') or 's. f.'})",
            f"   {p.get('venue') or 'sin revista'}{cite} · DOI: {p['doi'] or 'sin DOI'}" + (f" · acceso abierto: {p['oa_url']}" if p.get("oa_url") else "")]
    if p.get("abstract"):
        bits.append("   Resumen: " + p["abstract"])
    return "\n".join(bits)


# ------------------------------------------------------------------ BibTeX

def bibtex(doi: str) -> str:
    doi = _doi(doi)
    if not DOI_RE.match(doi):
        raise ToolError(f"«{doi[:60]}» no es un DOI válido (empieza por 10.xxxx/)")
    try:
        r = web.fetch("https://api.crossref.org/works/" + quote(doi, safe="/") + "/transform/application/x-bibtex",
                      max_bytes=200 * 1024, accept="application/x-bibtex", profile="bot", retries=1)
    except web.WebError as e:
        raise ToolError(f"{doi}: {e}") from e
    if r["status"] == 404:
        raise ToolError(f"{doi}: Crossref no conoce este DOI (¿está mal escrito? búscalo con search_papers)")
    if r["status"] != 200:
        raise ToolError(f"{doi}: Crossref respondió {r['status']}")
    text = r["body"].decode("utf-8", "replace").strip()
    if not text.startswith("@"):
        raise ToolError(f"{doi}: respuesta sin BibTeX")
    return text.replace("\r", "")


def bib_key(entry: str) -> str:
    m = re.match(r"@\w+\s*\{\s*([^,\s]+)\s*,", entry)
    return m.group(1) if m else ""


def merge_bib(existing: str, entries: list[str]) -> tuple[str, list[str], list[str]]:
    """Append entries whose key is not in `existing`: (new text, added keys, skipped keys)."""
    have = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", existing))
    added, skipped, out = [], [], existing.rstrip()
    for e in entries:
        k = bib_key(e)
        if k and k in have:
            skipped.append(k)
            continue
        have.add(k)
        added.append(k)
        out += "\n\n" + e.strip()
    return out + "\n", added, skipped


# ------------------------------------------------------------------ pictures of a page

def _wikimedia_original(u: str) -> str:
    """upload.wikimedia.org/.../thumb/a/ab/File.jpg/800px-File.jpg → the file itself (the thumbnail is fine, but the original
    is sharper; the hub shrinks it anyway)."""
    m = re.match(r"^(https://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb/(\w/\w\w/[^/]+)/[^/]+$", u)
    return f"{m.group(1)}/{m.group(2)}" if m else u


def _srcset_best(srcset: str) -> str:
    best, w = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        try:
            size = float(bits[1].rstrip("wx")) if len(bits) > 1 else 1
        except ValueError:
            size = 1
        if size > w:
            best, w = bits[0], size
    return best


def page_images(url: str, limit: int = 20, min_px: int = 200) -> dict:
    """The pictures of a web page: [{url, alt, caption, width, height, kind}] (biggest/most relevant first) and the page title."""
    from bs4 import BeautifulSoup

    r = None
    for profile in ("chrome", "bot"):  # some sites (Wikipedia) refuse a browser look-alike and welcome an honest bot
        try:
            r = web.fetch(url, max_bytes=3 * 1024 * 1024, profile=profile, retries=1)
        except web.WebError as e:
            raise ToolError(str(e)) from e
        if r["status"] not in (401, 403):
            break
    if r["status"] >= 400:
        raise ToolError(f"{urlsplit(url).hostname} respondió {r['status']}")
    if "html" not in (r["content_type"] or "").lower():
        raise ToolError("No es una página web. Si es una imagen, descárgala con download_file.")
    base = r["url"]
    soup = BeautifulSoup(r["body"], "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else base
    found: list[dict] = []
    seen: set[str] = set()

    def add(src: str | None, alt: str = "", caption: str = "", w: int | None = None, h: int | None = None, kind: str = "img"):
        if not src or src.startswith("data:"):
            return
        u = _wikimedia_original(urljoin(base, src.strip()))
        if urlsplit(u).scheme not in ("http", "https") or u in seen or SKIP_IMG.search(u):
            return
        if (w and w < min_px) or (h and h < min_px // 2):
            return
        seen.add(u)
        found.append({"url": u, "alt": re.sub(r"\s+", " ", alt or "").strip()[:200],
                      "caption": re.sub(r"\s+", " ", caption or "").strip()[:300], "width": w, "height": h, "kind": kind})

    for prop in ("og:image", "twitter:image"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            add(tag["content"], title, "imagen principal de la página (og:image)", kind="principal")

    def num(v) -> int | None:
        m = re.match(r"\d+", str(v or ""))
        return int(m.group()) if m else None

    for img in soup.find_all("img"):
        src = _srcset_best(img.get("srcset") or img.get("data-srcset") or "") or img.get("data-src") or img.get("data-original") \
            or img.get("src")
        fig = img.find_parent("figure")
        cap = fig.find("figcaption").get_text(" ", strip=True) if fig and fig.find("figcaption") else ""
        add(src, img.get("alt") or "", cap, num(img.get("width")), num(img.get("height")))
    found.sort(key=lambda x: (x["kind"] != "principal", not x["caption"], -((x["width"] or 0) * (x["height"] or 0))))
    return {"title": title, "url": base, "images": found[:max(1, min(int(limit or 20), 40))], "total": len(found)}
