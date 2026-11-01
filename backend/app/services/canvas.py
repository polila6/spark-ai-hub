"""Canvas: a document or code file next to the chat, written by the assistant and edited by the user, with versions
and export (Markdown, Word, PDF, HTML, source file). Conversions run in the code sandbox (pandoc + LibreOffice)."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CanvasDoc, User, utcnow
from . import sandbox

MAX_CHARS = 400_000
KEEP_VERSIONS = 30
CODE_EXT = {"python": "py", "javascript": "js", "typescript": "ts", "bash": "sh", "shell": "sh", "sql": "sql", "html": "html",
            "css": "css", "json": "json", "yaml": "yaml", "c": "c", "cpp": "cpp", "java": "java", "go": "go", "rust": "rs",
            "r": "r", "matlab": "m", "latex": "tex", "markdown": "md", "julia": "jl", "csharp": "cs", "kotlin": "kt"}


class CanvasError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


def public(d: CanvasDoc, with_content: bool = True) -> dict:
    out = {"id": str(d.id), "title": d.title, "kind": d.kind, "language": d.language, "version": d.version,
           "conversation_id": str(d.conversation_id) if d.conversation_id else None, "updated_at": d.updated_at,
           "versions": [{"version": h["version"], "author": h.get("author"), "at": h.get("at")} for h in (d.history or [])]}
    if with_content:
        out["content"] = d.content
    return out


def for_conversation(db: Session, owner_id, conv_id) -> list[CanvasDoc]:
    return list(db.scalars(select(CanvasDoc).where(CanvasDoc.owner_id == owner_id, CanvasDoc.conversation_id == conv_id)
                           .order_by(CanvasDoc.updated_at.desc())))


def _snapshot(d: CanvasDoc, author: str) -> None:
    hist = list(d.history or [])
    hist.append({"version": d.version, "content": d.content, "author": author, "at": utcnow().isoformat()})
    d.history = hist[-KEEP_VERSIONS:]


def create(db: Session, user: User, *, title: str, content: str, kind: str = "document", language: str | None = None,
           conversation_id=None, author: str = "assistant") -> CanvasDoc:
    if len(content) > MAX_CHARS:
        raise CanvasError("El documento supera 400.000 caracteres")
    d = CanvasDoc(owner_id=user.id, conversation_id=conversation_id, title=(title or "Documento")[:255],
                  kind="code" if kind == "code" else "document", language=(language or None) and language[:32].lower(),
                  content=content, version=1, history=[])
    _snapshot(d, author)
    db.add(d)
    db.flush()
    return d


def update(db: Session, d: CanvasDoc, *, content: str, author: str, title: str | None = None,
           base_version: int | None = None) -> CanvasDoc:
    if base_version is not None and base_version != d.version:
        raise CanvasError(f"El lienzo cambió mientras editabas (ahora es la versión {d.version}). Recarga y vuelve a aplicar tu cambio.", 409)
    if len(content) > MAX_CHARS:
        raise CanvasError("El documento supera 400.000 caracteres")
    if content == d.content and (title is None or title == d.title):
        return d
    d.content = content
    if title:
        d.title = title[:255]
    d.version += 1
    _snapshot(d, author)
    return d


def apply_edits(d: CanvasDoc, edits: list[dict]) -> str:
    """Targeted find/replace edits (each `find` must occur exactly once, or `all` must be true)."""
    text = d.content
    for i, e in enumerate(edits[:50]):
        find, repl = str(e.get("find") or ""), str(e.get("replace") or "")
        if not find:
            raise CanvasError(f"Edición {i + 1}: falta el texto a buscar")
        n = text.count(find)
        if n == 0:
            raise CanvasError(f"Edición {i + 1}: no encuentro «{find[:80]}» en el lienzo (cópialo exactamente)")
        if n > 1 and not e.get("all"):
            raise CanvasError(f"Edición {i + 1}: «{find[:80]}» aparece {n} veces; amplía el texto o usa all=true")
        text = text.replace(find, repl) if e.get("all") else text.replace(find, repl, 1)
    return text


def restore(db: Session, d: CanvasDoc, version: int, author: str) -> CanvasDoc:
    h = next((x for x in (d.history or []) if x["version"] == version), None)
    if not h:
        raise CanvasError("Esa versión ya no está guardada", 404)
    return update(db, d, content=h["content"], author=author)


def _slug(title: str) -> str:
    return (re.sub(r"[^A-Za-z0-9áéíóúñÁÉÍÓÚÑ_-]+", "_", title).strip("_") or "documento")[:80]


def export(db: Session, user: User, d: CanvasDoc, fmt: str, save: bool = False) -> tuple[bytes, str, str, dict | None]:
    """Return (bytes, filename, mime, saved_file_or_None)."""
    base = _slug(d.title)
    if d.kind == "code" or fmt in ("md", "txt", "code"):
        ext = CODE_EXT.get((d.language or "").lower(), "txt") if d.kind == "code" else ("md" if fmt != "txt" else "txt")
        data, name = d.content.encode(), f"{base}.{ext}"
        mime = "text/markdown" if ext == "md" else "text/plain"
        saved = None
        if save:
            saved = sandbox.store_bytes(db, user, name, data, folder="Lienzos")
        return data, name, mime, saved
    if fmt not in ("docx", "pdf", "html", "odt"):
        raise CanvasError("Formato no soportado")
    script = {
        "docx": "pandoc in/doc.md -f gfm+tex_math_dollars -o out/{n}.docx",
        "odt": "pandoc in/doc.md -f gfm+tex_math_dollars -o out/{n}.odt",
        "html": "pandoc in/doc.md -f gfm+tex_math_dollars -s --mathml --metadata title='{t}' -o out/{n}.html",
        "pdf": "pandoc in/doc.md -f gfm+tex_math_dollars -o /tmp/{n}.docx && "
               "soffice --headless --convert-to pdf --outdir out /tmp/{n}.docx >/dev/null 2>&1",
    }[fmt].format(n=base, t=d.title.replace("'", " "))
    res = sandbox.run_raw(db, user, script=f"set -e\nmkdir -p out\n{script}\nls out", files={"doc.md": d.content.encode()},
                          timeout_s=120)
    out = res["outputs"].get(f"out/{base}.{fmt}")
    if res["exit_code"] != 0 or out is None:
        raise CanvasError(f"La conversión falló: {res['stderr'][-400:] or res['stdout'][-400:]}")
    mime = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf",
            "html": "text/html", "odt": "application/vnd.oasis.opendocument.text"}[fmt]
    saved = sandbox.store_bytes(db, user, f"{base}.{fmt}", out, folder="Lienzos") if save else None
    return out, f"{base}.{fmt}", mime, saved


_ = uuid
