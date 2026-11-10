"""Find and download pictures and files from the public internet for documents (the sandbox has no network, so the
hub does the fetching: public addresses only, size and type limits, images checked and normalised).

search_images(): Wikimedia Commons / Wikipedia lead pictures / Openverse, each result with its author and licence so the
document can credit it. fetch_image(): a picture normalised to JPEG/PNG (LaTeX, Word and PowerPoint can use it)."""
from __future__ import annotations

import io
import json
import re
from urllib.parse import urlencode, urlsplit

from . import web

MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_FILE_BYTES = 40 * 1024 * 1024
FILE_TYPES = {  # content-type or extension → extension to keep
    "application/pdf": ".pdf", "text/csv": ".csv", "text/tab-separated-values": ".tsv", "application/json": ".json",
    "text/plain": ".txt", "application/xml": ".xml", "text/xml": ".xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/zip": ".zip", "application/geo+json": ".geojson"}
TEXT_EXT = {".csv", ".tsv", ".json", ".txt", ".xml", ".geojson", ".md", ".tex", ".bib"}
BINARY_EXT = {".pdf", ".xlsx", ".docx", ".zip"}


class MediaError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _json(url: str) -> dict:
    try:
        r = web.fetch(url, max_bytes=2 * 1024 * 1024, accept="application/json")
    except web.WebError as e:
        raise MediaError(str(e)) from e
    if r["status"] != 200:
        raise MediaError(f"{urlsplit(url).hostname} respondió {r['status']}")
    try:
        return json.loads(r["body"].decode("utf-8", "replace"))
    except ValueError as e:
        raise MediaError("Respuesta no válida de " + (urlsplit(url).hostname or "el servidor")) from e


def _plain(html: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _from_commons(pages: dict, limit: int) -> list[dict]:
    out = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [None])[0]
        if not ii or not str(ii.get("mime", "")).startswith("image/") or ii.get("mime") in ("image/svg+xml", "image/vnd.djvu"):
            continue
        meta = ii.get("extmetadata") or {}

        def m(k):
            return _plain((meta.get(k) or {}).get("value"))
        out.append({"title": p.get("title", "").removeprefix("File:"), "url": ii.get("thumburl") or ii.get("url"),
                    "original_url": ii.get("url"), "page_url": ii.get("descriptionurl"), "width": ii.get("thumbwidth") or ii.get("width"),
                    "height": ii.get("thumbheight") or ii.get("height"), "full_width": ii.get("width"), "full_height": ii.get("height"),
                    "license": m("LicenseShortName"), "license_url": m("LicenseUrl"), "author": m("Artist")[:120],
                    "description": m("ImageDescription")[:160], "source": "Wikimedia Commons"})
        if len(out) >= limit:
            break
    return out


_IINFO = {"prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
          "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription"}


def commons_search(query: str, limit: int = 6) -> list[dict]:
    q = urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": min(limit * 2, 20),
                   "gsrsearch": f"{query} filetype:bitmap", **_IINFO})
    data = _json("https://commons.wikimedia.org/w/api.php?" + q)
    return _from_commons((data.get("query") or {}).get("pages") or {}, limit)


def commons_files(titles: list[str]) -> list[dict]:
    if not titles:
        return []
    q = urlencode({"action": "query", "format": "json", "titles": "|".join("File:" + t.removeprefix("File:") for t in titles[:10]), **_IINFO})
    data = _json("https://commons.wikimedia.org/w/api.php?" + q)
    return _from_commons((data.get("query") or {}).get("pages") or {}, 10)


def wikipedia_lead(query: str, lang: str = "es", limit: int = 3) -> list[dict]:
    """The main picture of the best-matching Wikipedia articles (the portrait of a person, the photo of a place)."""
    q = urlencode({"action": "query", "format": "json", "generator": "search", "gsrsearch": query, "gsrlimit": limit,
                   "prop": "pageimages", "piprop": "name", "redirects": 1})
    data = _json(f"https://{lang}.wikipedia.org/w/api.php?" + q)
    pages = sorted(((data.get("query") or {}).get("pages") or {}).values(), key=lambda x: x.get("index", 0))
    names = [p["pageimage"] for p in pages if p.get("pageimage")]
    found = commons_files(names)
    by = {f["title"]: f for f in found}
    out = []
    for p in pages:
        f = by.get((p.get("pageimage") or "").replace("_", " ")) or by.get(p.get("pageimage") or "")
        if f:
            out.append({**f, "source": f"Wikipedia ({lang}): {p.get('title')}"})
    return out


def openverse(query: str, limit: int = 6) -> list[dict]:
    q = urlencode({"q": query, "page_size": min(limit, 20), "mature": "false"})
    data = _json("https://api.openverse.org/v1/images/?" + q)
    out = []
    for r in data.get("results") or []:
        if not r.get("url"):
            continue
        out.append({"title": (r.get("title") or "")[:120], "url": r["url"], "original_url": r["url"], "page_url": r.get("foreign_landing_url"),
                    "width": r.get("width"), "height": r.get("height"), "full_width": r.get("width"), "full_height": r.get("height"),
                    "license": f"{(r.get('license') or '').upper()} {r.get('license_version') or ''}".strip(),
                    "license_url": r.get("license_url"), "author": (r.get("creator") or "")[:120], "description": "",
                    "source": f"Openverse ({r.get('source') or ''})"})
    return out[:limit]


def search_images(query: str, source: str = "auto", limit: int = 6, lang: str = "es") -> list[dict]:
    """Candidates with licence and author. source: auto | wikipedia | commons | openverse."""
    query = query.strip()[:200]
    if not query:
        raise MediaError("Falta la búsqueda")
    results: list[dict] = []
    errors: list[str] = []
    order = {"auto": ["wikipedia", "commons", "openverse"], "wikipedia": ["wikipedia"], "commons": ["commons"],
             "openverse": ["openverse"]}.get(source, ["wikipedia", "commons", "openverse"])
    for s in order:
        try:
            if s == "wikipedia":
                results += wikipedia_lead(query, lang, 2)
                if lang != "en" and not results:
                    results += wikipedia_lead(query, "en", 2)
            elif s == "commons":
                results += commons_search(query, limit)
            else:
                results += openverse(query, limit)
        except MediaError as e:
            errors.append(f"{s}: {e.message}")
        if len(results) >= limit:
            break
    seen, uniq = set(), []
    for r in results:
        if r["url"] and r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)
    if not uniq and errors:
        raise MediaError("No se pudo buscar imágenes (" + "; ".join(errors) + ")")
    for r in uniq:
        r["match"], r["hits"] = relevance(query, r)
    uniq.sort(key=lambda r: -r["hits"])  # (stable: equal matches keep the source order Wikipedia → Commons → Openverse)
    return uniq[:limit]


def _fold(text: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def relevance(query: str, r: dict) -> tuple[str, int]:
    """How well a candidate's title/description/source page match the words of the query: ("alta"|"media"|"baja", hits)."""
    words = {w for w in re.findall(r"[a-z0-9]{3,}", _fold(query))}
    if not words:
        return "media", 0
    hay = _fold(" ".join(str(r.get(k) or "") for k in ("title", "description", "source", "page_url")))
    hits = sum(1 for w in words if w in hay)
    share = hits / len(words)
    return ("alta" if share >= 0.6 else "media" if share >= 0.3 else "baja"), hits


def process_image(body: bytes, ct: str, url: str = "", max_px: int = 2000, fmt: str | None = None) -> dict:
    """Validate a downloaded picture and return {data, ext, width, height, original_size}: rotated upright, at most `max_px`
    on its long side, as JPEG (photos) or PNG (transparency or when `fmt`="png")."""
    from PIL import Image, ImageOps

    if ct == "image/svg+xml":
        raise MediaError("Es un SVG (vectorial): busca una versión en JPG/PNG.")
    try:
        im = Image.open(io.BytesIO(body))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"El archivo descargado no es una imagen válida ({type(e).__name__})") from e
    orig = im.size
    if min(orig) < 48:
        raise MediaError(f"Imagen demasiado pequeña ({orig[0]}×{orig[1]})")
    im = ImageOps.exif_transpose(im)
    alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if max(im.size) > max_px:
        im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    if alpha or fmt == "png":
        im.convert("RGBA" if alpha else "RGB").save(buf, "PNG", optimize=True)
        ext = ".png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
        ext = ".jpg"
    return {"kind": "image", "data": buf.getvalue(), "ext": ext, "width": im.size[0], "height": im.size[1],
            "original_size": orig, "final_url": url}


def _is_image(ct: str, body: bytes) -> bool:
    return ct.startswith("image/") or body[:3] == b"\xff\xd8\xff" or body[:4] == b"\x89PNG" or body[:6] in (b"GIF87a", b"GIF89a") \
        or body[8:12] == b"WEBP"


def fetch_any(url: str, want_ext: str = "", max_px: int = 2000, fmt: str | None = None) -> dict:
    """Download a picture (normalised to JPEG/PNG) or a document/data file (PDF, CSV, JSON, text, xlsx, docx, zip).
    HTML pages are refused: use fetch_url to read them. Returns {kind: "image"|"file", data, ext, final_url, ...}."""
    r = _get(url, MAX_FILE_BYTES, "image/jpeg,image/png,image/webp,image/*;q=0.9,*/*;q=0.5")
    ct = r["content_type"].split(";")[0].strip().lower()
    body = r["body"]
    if _is_image(ct, body):
        if len(body) > MAX_IMAGE_BYTES:
            raise MediaError("La imagen supera 30 MB")
        return process_image(body, ct, r["url"], max_px, fmt)
    if ct in ("text/html", "application/xhtml+xml") or body[:15].lower().lstrip().startswith((b"<!doctype html", b"<html")):
        raise MediaError("Es una página web, no un archivo ni una imagen. Para leerla usa fetch_url; si buscas una imagen, "
                         "usa search_images y pasa la URL directa del archivo.")
    want_ext = want_ext.lower()
    ext = ".pdf" if body[:5] == b"%PDF-" else want_ext if want_ext in TEXT_EXT | BINARY_EXT else FILE_TYPES.get(ct, "")
    if ext not in TEXT_EXT | BINARY_EXT:
        raise MediaError(f"Tipo de archivo no permitido ({ct or 'desconocido'}). Se admiten imágenes, PDF, CSV, JSON, texto, xlsx, docx y zip.")
    return {"kind": "file", "data": body, "ext": ext, "content_type": ct, "final_url": r["url"]}


def _get(url: str, max_bytes: int, accept: str) -> dict:
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise MediaError("La URL debe empezar por http:// o https://")
    try:
        r = web.fetch(url.strip(), max_bytes=max_bytes, accept=accept)
    except web.WebError as e:
        raise MediaError(str(e)) from e
    if r["status"] >= 400:
        raise MediaError(f"{u.hostname} respondió {r['status']}" + (" (acceso denegado: prueba otra fuente)" if r["status"] in (401, 403) else ""))
    return r


def slug(text: str, default: str = "imagen") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", re.sub(r"[áàä]", "a", re.sub(r"[éèë]", "e", re.sub(r"[íìï]", "i", re.sub(r"[óòö]", "o", re.sub(r"[úùü]", "u", text.lower())))))).strip("-")
    return (s or default)[:60]


def credit_line(meta: dict) -> str:
    bits = [b for b in (meta.get("author"), meta.get("license"), meta.get("source")) if b]
    return " · ".join(bits) + (f" · {meta['page_url']}" if meta.get("page_url") else "")


__all__ = ["MediaError", "search_images", "fetch_any", "process_image", "slug", "credit_line"]
