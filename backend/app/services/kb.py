"""Knowledge base: chunking, embeddings (local model through the hub scheduler) and hybrid retrieval.

Retrieval = pgvector cosine search + PostgreSQL full-text search, fused with reciprocal-rank fusion. If no embedding
model is installed the knowledge base still works with full-text search only (results say which mode was used).
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from ..engines import EngineError
from ..models import (KbChunk, KbCollection, KbDocument, ModelStatus, Project, RegisteredModel, SharedResource, User,
                      UserFile, Visibility)
from ..settings_store import get_setting
from . import files as files_svc
from . import llm, scheduler

CHUNK_CHARS = 1400
OVERLAP = 200
EMBED_BATCH = 32


class KbError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ------------------------------------------------------------------ access


def readable_collection_ids(db: Session, user: User) -> list[uuid.UUID]:
    shared_cols = select(SharedResource.resource_id).where(SharedResource.resource_type == "collection",
                                                           SharedResource.grantee_id == user.id)
    shared_projects = select(SharedResource.resource_id).where(SharedResource.resource_type == "project",
                                                               SharedResource.grantee_id == user.id)
    proj_cols = select(Project.collection_id).where(Project.collection_id.is_not(None), or_(
        Project.owner_id == user.id, Project.id.in_(shared_projects), Project.visibility == Visibility.public))
    q = select(KbCollection.id).where(or_(KbCollection.owner_id == user.id, KbCollection.id.in_(shared_cols),
                                          KbCollection.visibility == Visibility.public, KbCollection.id.in_(proj_cols)))
    return list(db.scalars(q))


def writable_collection(db: Session, user: User, cid: uuid.UUID) -> KbCollection:
    c = db.get(KbCollection, cid)
    if not c:
        raise KbError("not_found", "collection not found")
    if c.owner_id == user.id:
        return c
    proj = db.scalar(select(Project).where(Project.collection_id == c.id))
    if proj and (proj.owner_id == user.id or db.scalar(select(SharedResource.id).where(
            SharedResource.resource_type == "project", SharedResource.resource_id == proj.id,
            SharedResource.grantee_id == user.id, SharedResource.permission == "write"))):
        return c
    if db.scalar(select(SharedResource.id).where(SharedResource.resource_type == "collection",
                                                 SharedResource.resource_id == c.id, SharedResource.grantee_id == user.id,
                                                 SharedResource.permission == "write")):
        return c
    raise KbError("not_found", "collection not found")


def default_collection(db: Session, user: User) -> KbCollection:
    c = db.scalar(select(KbCollection).where(KbCollection.owner_id == user.id, KbCollection.name == "Mis documentos"))
    if c is None:
        c = KbCollection(owner_id=user.id, name="Mis documentos", description="Colección personal por defecto")
        db.add(c)
        db.flush()
    return c


# ------------------------------------------------------------------ embeddings


def embedding_model(db: Session) -> RegisteredModel | None:
    alias = get_setting(db, "embedding_model")
    q = select(RegisteredModel).where(RegisteredModel.status.in_([ModelStatus.installed, ModelStatus.ready]))
    if alias:
        m = db.scalar(q.where(RegisteredModel.alias == alias))
        if m:
            return m
    for m in db.scalars(q.order_by(RegisteredModel.created_at)):
        caps = set(m.capabilities or [])
        if "embeddings" in caps and "chat" not in caps:
            return m
    return None


def embed(model_id: uuid.UUID, texts: list[str]) -> list[list[float]]:
    inst = scheduler.ensure_loaded(model_id, wait_s=900)
    scheduler.begin_request(inst.id)
    try:
        from ..db import SessionLocal

        with SessionLocal() as db:
            m = db.get(RegisteredModel, model_id)
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            data = llm.embeddings(m, {"input": texts[i:i + EMBED_BATCH]})
            out += [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
        return out
    finally:
        scheduler.end_request(inst.id)


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


# ------------------------------------------------------------------ chunking / indexing


def chunk_pages(pages: list[tuple[int | None, str]], size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> list[tuple[int | None, str]]:
    """Split on paragraph boundaries into ~size-char chunks with overlap; keeps the page number of each chunk."""
    out: list[tuple[int | None, str]] = []
    for page, txt in pages:
        paras = [p.strip() for p in re.split(r"\n\s*\n", txt or "") if p.strip()]
        buf = ""
        for para in paras:
            while len(para) > size:  # very long paragraph: hard split
                head, para = para[:size], para[size - overlap:]
                if buf:
                    out.append((page, buf))
                    buf = ""
                out.append((page, head))
            if len(buf) + len(para) + 2 > size and buf:
                out.append((page, buf))
                buf = buf[-overlap:] + "\n\n" + para if overlap else para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf.strip():
            out.append((page, buf))
    return out


def index_document(db: Session, doc: KbDocument, progress=None) -> int:
    f = db.get(UserFile, doc.file_id)
    if f is None:
        raise KbError("file_missing", "file no longer exists")
    if f.text_status not in ("ready",):
        raise KbError("no_text", "The file has no extractable text (scanned PDF or unsupported format)")
    chunks = chunk_pages(files_svc.read_pages(f))
    em = embedding_model(db)
    vectors: list[list[float]] | None = None
    if em is not None and chunks:
        vectors = []
        for i in range(0, len(chunks), 256):
            vectors += embed(em.id, [c[1] for c in chunks[i:i + 256]])
            if progress:
                progress(min(0.95, (i + 256) / len(chunks)))
    model_name = em.alias if (em and vectors) else "fts-only"
    db.execute(text("DELETE FROM kb_chunks WHERE document_id = :d"), {"d": doc.id})
    for i, (page, txt) in enumerate(chunks):
        db.execute(text("INSERT INTO kb_chunks (document_id, collection_id, owner_id, idx, page, text, model, embedding) "
                        "VALUES (:d, :c, :o, :i, :p, :t, :m, CAST(:e AS vector))"),
                   {"d": doc.id, "c": doc.collection_id, "o": doc.owner_id, "i": i, "p": page, "t": txt,
                    "m": model_name, "e": _vec(vectors[i]) if vectors else None})
    doc.chunks, doc.embedding_model, doc.status, doc.error = len(chunks), model_name, "ready", None
    db.commit()
    return len(chunks)


# ------------------------------------------------------------------ retrieval


def search(db: Session, user: User, query: str, collection_ids: list[uuid.UUID] | None = None, k: int = 8) -> dict:
    allowed = set(readable_collection_ids(db, user))
    cids = [c for c in (collection_ids or list(allowed)) if c in allowed]
    if not cids or not query.strip():
        return {"mode": "none", "results": []}
    ranks: dict[int, float] = {}
    mode = "fts"
    em = embedding_model(db)
    if em is not None:
        try:
            qv = embed(em.id, [query])[0]
            rows = db.execute(text(
                "SELECT id FROM kb_chunks WHERE collection_id = ANY(:c) AND model = :m AND embedding IS NOT NULL "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 40"), {"c": cids, "m": em.alias, "q": _vec(qv)}).all()
            for r, (cid,) in enumerate(rows):
                ranks[cid] = ranks.get(cid, 0) + 1 / (60 + r)
            mode = "hybrid"
        except (EngineError, scheduler.SchedulingError):
            mode = "fts (embedding model unavailable)"
    rows = db.execute(text(
        "SELECT id FROM kb_chunks WHERE collection_id = ANY(:c) AND tsv @@ websearch_to_tsquery('simple', :t) "
        "ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('simple', :t)) DESC LIMIT 40"), {"c": cids, "t": query}).all()
    if not rows:  # OR of the words when the AND query finds nothing
        words = [w for w in re.findall(r"\w{3,}", query.lower())][:12]
        if words:
            rows = db.execute(text(
                "SELECT id FROM kb_chunks WHERE collection_id = ANY(:c) AND tsv @@ to_tsquery('simple', :t) "
                "ORDER BY ts_rank_cd(tsv, to_tsquery('simple', :t)) DESC LIMIT 40"),
                {"c": cids, "t": " | ".join(words)}).all()
    for r, (cid,) in enumerate(rows):
        ranks[cid] = ranks.get(cid, 0) + 1 / (60 + r)
    top = sorted(ranks.items(), key=lambda x: -x[1])[:k]
    if not top:
        return {"mode": mode, "results": []}
    chunks = {c.id: c for c in db.scalars(select(KbChunk).where(KbChunk.id.in_([i for i, _ in top])))}
    docs = {d.id: d for d in db.scalars(select(KbDocument).where(KbDocument.id.in_({c.document_id for c in chunks.values()})))}
    fids = {d.file_id for d in docs.values()}
    fmap = {f.id: f for f in db.scalars(select(UserFile).where(UserFile.id.in_(fids)))}
    cols = {c.id: c.name for c in db.scalars(select(KbCollection).where(KbCollection.id.in_(cids)))}
    results = []
    for cid, score in top:
        c = chunks.get(cid)
        if not c:
            continue
        d = docs.get(c.document_id)
        f = fmap.get(d.file_id) if d else None
        results.append({"chunk_id": c.id, "score": round(score, 5), "text": c.text, "page": c.page,
                        "file_id": str(f.id) if f else None, "filename": f.filename if f else "?",
                        "collection": cols.get(c.collection_id), "collection_id": str(c.collection_id)})
    return {"mode": mode, "results": results}
