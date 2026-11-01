"""Projects (workspaces with instructions, documents and chats), knowledge collections and memory."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher
from ..models import (Conversation, KbCollection, KbDocument, Memory, Project, SchedClass, SharedResource, User,
                      UserFile, Visibility)
from ..services import kb
from ..services.jobs import JobError, create_job
from ..services.notify import notify

router = APIRouter(prefix="/api/v1", tags=["personal-cloud"])


# ------------------------------------------------------------------ projects


def _grant(db: Session, rtype: str, rid: uuid.UUID, uid: uuid.UUID) -> SharedResource | None:
    return db.scalar(select(SharedResource).where(SharedResource.resource_type == rtype, SharedResource.resource_id == rid,
                                                  SharedResource.grantee_id == uid))


def project_for_read(db: Session, p: Principal, pid: uuid.UUID) -> Project:
    pr = db.get(Project, pid)
    if not pr:
        raise not_found("project")
    if pr.owner_id == p.id or pr.visibility == Visibility.public or _grant(db, "project", pr.id, p.id):
        return pr
    raise not_found("project")


def project_for_write(db: Session, p: Principal, pid: uuid.UUID) -> Project:
    pr = project_for_read(db, p, pid)
    if pr.owner_id == p.id:
        return pr
    g = _grant(db, "project", pr.id, p.id)
    if g and g.permission == "write":
        return pr
    raise HTTPException(403, detail={"code": "read_only", "message": "You have read-only access to this project"})


def _project_pub(db: Session, pr: Project, p: Principal) -> dict:
    owner = db.get(User, pr.owner_id)
    g = None if pr.owner_id == p.id else _grant(db, "project", pr.id, p.id)
    shares = [{"user_id": str(s.grantee_id), "username": (db.get(User, s.grantee_id) or User(username="?")).username,
               "permission": s.permission}
              for s in db.scalars(select(SharedResource).where(SharedResource.resource_type == "project",
                                                               SharedResource.resource_id == pr.id))] if pr.owner_id == p.id else []
    return {"id": str(pr.id), "name": pr.name, "description": pr.description, "instructions": pr.instructions,
            "collection_id": str(pr.collection_id) if pr.collection_id else None, "default_model": pr.default_model,
            "visibility": pr.visibility.value, "archived": pr.archived, "owner": owner.username if owner else None,
            "is_owner": pr.owner_id == p.id, "permission": "owner" if pr.owner_id == p.id else (g.permission if g else "read"),
            "shares": shares,
            "files": db.scalar(select(func.count()).select_from(UserFile).where(UserFile.project_id == pr.id)) or 0,
            "conversations": db.scalar(select(func.count()).select_from(Conversation).where(
                Conversation.project_id == pr.id, Conversation.owner_id == p.id)) or 0,
            "created_at": pr.created_at, "updated_at": pr.updated_at}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    instructions: str = Field(default="", max_length=50_000)
    default_model: str | None = Field(default=None, max_length=128)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    instructions: str | None = Field(default=None, max_length=50_000)
    default_model: str | None = Field(default=None, max_length=128)
    archived: bool | None = None


@router.get("/projects")
def list_projects(p: Principal = Depends(current_principal), db: Session = Depends(get_db), archived: bool = False):
    shared = select(SharedResource.resource_id).where(SharedResource.resource_type == "project",
                                                      SharedResource.grantee_id == p.id)
    rows = db.scalars(select(Project).where(or_(Project.owner_id == p.id, Project.id.in_(shared)),
                                            Project.archived.is_(archived)).order_by(Project.updated_at.desc())).all()
    return [_project_pub(db, r, p) for r in rows]


@router.post("/projects")
def create_project(body: ProjectIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    col = KbCollection(owner_id=p.id, name=f"Proyecto: {body.name}"[:255], description="Documentos del proyecto")
    db.add(col)
    db.flush()
    pr = Project(owner_id=p.id, collection_id=col.id, **body.model_dump())
    db.add(pr)
    db.commit()
    return _project_pub(db, pr, p)


@router.get("/projects/{pid}")
def get_project(pid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    pr = project_for_read(db, p, pid)
    convs = db.scalars(select(Conversation).where(Conversation.project_id == pr.id, Conversation.owner_id == p.id)
                       .order_by(Conversation.updated_at.desc()).limit(200))
    return {**_project_pub(db, pr, p),
            "conversations_list": [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at} for c in convs]}


@router.patch("/projects/{pid}")
def patch_project(pid: uuid.UUID, body: ProjectPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    pr = project_for_write(db, p, pid)
    data = body.model_dump(exclude_unset=True)
    if "archived" in data and pr.owner_id != p.id:
        raise HTTPException(403, detail={"code": "owner_only", "message": "Only the owner can archive a project"})
    for k, v in data.items():
        setattr(pr, k, v)
    db.commit()
    return _project_pub(db, pr, p)


@router.delete("/projects/{pid}")
def delete_project(pid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                   delete_files: bool = False):
    pr = db.get(Project, pid)
    if not pr or pr.owner_id != p.id:
        raise not_found("project")
    if delete_files:
        from ..services import files as files_svc

        for f in db.scalars(select(UserFile).where(UserFile.project_id == pr.id, UserFile.owner_id == p.id)):
            for path in (files_svc.abs_path(f), files_svc.text_path(f), files_svc.pages_path(f)):
                path.unlink(missing_ok=True)
            db.delete(f)
    db.execute(delete(SharedResource).where(SharedResource.resource_type == "project", SharedResource.resource_id == pr.id))
    if pr.collection_id:
        col = db.get(KbCollection, pr.collection_id)
        if col:
            db.delete(col)
    db.delete(pr)
    db.commit()
    return {"ok": True}


class ShareIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    permission: str = Field(default="read", pattern="^(read|write)$")


@router.post("/projects/{pid}/shares")
def share_project(pid: uuid.UUID, body: ShareIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    pr = db.get(Project, pid)
    if not pr or pr.owner_id != p.id:
        raise not_found("project")
    u = db.scalar(select(User).where(User.username == body.username.strip().lower(), User.is_active.is_(True)))
    if not u or u.id == p.id:
        raise HTTPException(404, detail={"code": "user_not_found", "message": "User not found"})
    g = _grant(db, "project", pr.id, u.id)
    if g:
        g.permission = body.permission
    else:
        db.add(SharedResource(resource_type="project", resource_id=pr.id, grantee_id=u.id, granted_by=p.id,
                              permission=body.permission))
        pr.visibility = Visibility.shared
        notify(db, u.id, "share", f"{p.user.display_name or p.user.username} ha compartido un proyecto contigo",
               f"«{pr.name}» ({'lectura y escritura' if body.permission == 'write' else 'solo lectura'})",
               link=f"/projects/{pr.id}")
    db.commit()
    return _project_pub(db, pr, p)


@router.delete("/projects/{pid}/shares/{uid}")
def unshare_project(pid: uuid.UUID, uid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    pr = db.get(Project, pid)
    if not pr or (pr.owner_id != p.id and uid != p.id):  # owners revoke; grantees may leave
        raise not_found("project")
    db.execute(delete(SharedResource).where(SharedResource.resource_type == "project", SharedResource.resource_id == pr.id,
                                            SharedResource.grantee_id == uid))
    if not db.scalar(select(SharedResource.id).where(SharedResource.resource_type == "project",
                                                     SharedResource.resource_id == pr.id)):
        pr.visibility = Visibility.private
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ knowledge collections


def _col_pub(db: Session, c: KbCollection, p: Principal) -> dict:
    stats = db.execute(select(func.count(), func.coalesce(func.sum(KbDocument.chunks), 0))
                       .where(KbDocument.collection_id == c.id)).one()
    proj = db.scalar(select(Project).where(Project.collection_id == c.id))
    return {"id": str(c.id), "name": c.name, "description": c.description, "visibility": c.visibility.value,
            "is_owner": c.owner_id == p.id, "documents": stats[0], "chunks": int(stats[1]),
            "project_id": str(proj.id) if proj else None, "created_at": c.created_at, "updated_at": c.updated_at}


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)


@router.get("/knowledge/collections")
def list_collections(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    kb.default_collection(db, p.user)
    db.commit()
    ids = kb.readable_collection_ids(db, p.user)
    rows = db.scalars(select(KbCollection).where(KbCollection.id.in_(ids)).order_by(KbCollection.created_at)).all()
    em = kb.embedding_model(db)
    return {"embedding_model": em.alias if em else None, "collections": [_col_pub(db, c, p) for c in rows]}


@router.post("/knowledge/collections")
def create_collection(body: CollectionIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = KbCollection(owner_id=p.id, **body.model_dump())
    db.add(c)
    db.commit()
    return _col_pub(db, c, p)


@router.patch("/knowledge/collections/{cid}")
def patch_collection(cid: uuid.UUID, body: CollectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = db.get(KbCollection, cid)
    if not c or c.owner_id != p.id:
        raise not_found("collection")
    c.name, c.description = body.name, body.description
    db.commit()
    return _col_pub(db, c, p)


@router.delete("/knowledge/collections/{cid}")
def delete_collection(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = db.get(KbCollection, cid)
    if not c or c.owner_id != p.id or db.scalar(select(Project.id).where(Project.collection_id == c.id)):
        raise not_found("collection")
    db.delete(c)  # documents and chunks cascade; the files stay in the drive
    db.commit()
    return {"ok": True}


@router.get("/knowledge/collections/{cid}/documents")
def list_documents(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if cid not in kb.readable_collection_ids(db, p.user):
        raise not_found("collection")
    out = []
    for d in db.scalars(select(KbDocument).where(KbDocument.collection_id == cid).order_by(KbDocument.created_at.desc())):
        f = db.get(UserFile, d.file_id)
        out.append({"id": str(d.id), "file_id": str(d.file_id), "filename": f.filename if f else "?",
                    "size_bytes": f.size_bytes if f else 0, "status": d.status, "chunks": d.chunks,
                    "embedding_model": d.embedding_model, "error": d.error, "created_at": d.created_at})
    return out


class AddDocsIn(BaseModel):
    file_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@router.post("/knowledge/collections/{cid}/documents")
def add_documents(cid: uuid.UUID, body: AddDocsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    try:
        col = kb.writable_collection(db, p.user, cid)
    except kb.KbError:
        raise not_found("collection")
    new = []
    for fid in body.file_ids:
        f = db.get(UserFile, fid)
        if not f or f.owner_id != p.id or f.kind not in ("document", "data"):
            raise HTTPException(422, detail={"code": "invalid_file", "message": f"File {fid} cannot be indexed"})
        if db.scalar(select(KbDocument.id).where(KbDocument.collection_id == col.id, KbDocument.file_id == f.id)):
            continue
        d = KbDocument(collection_id=col.id, file_id=f.id, owner_id=p.id, status="pending")
        db.add(d)
        db.flush()
        new.append((d, f))
    db.commit()
    jobs = []
    try:
        ready = [str(d.id) for d, f in new if f.text_status == "ready"]
        if ready:
            jobs.append(str(create_job(db, p.user, "kb_index", {"document_ids": ready}, title=f"Indexar en {col.name}",
                                       sched_class=SchedClass.interactive).id))
        for d, f in new:
            if f.text_status != "ready":
                jobs.append(str(create_job(db, p.user, "file_process", {"file_id": str(f.id)},
                                           title=f"Procesar {f.filename}", sched_class=SchedClass.interactive).id))
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"added": len(new), "jobs": jobs}


@router.delete("/knowledge/documents/{did}")
def remove_document(did: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    d = db.get(KbDocument, did)
    if not d:
        raise not_found("document")
    try:
        kb.writable_collection(db, p.user, d.collection_id)
    except kb.KbError:
        raise not_found("document")
    db.delete(d)
    db.commit()
    return {"ok": True}


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection_ids: list[uuid.UUID] | None = None
    k: int = Field(default=8, ge=1, le=30)


@router.post("/knowledge/search")
def search_knowledge(body: SearchIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return kb.search(db, p.user, body.query, body.collection_ids, body.k)


# ------------------------------------------------------------------ memory


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


def _mem_pub(m: Memory) -> dict:
    return {"id": str(m.id), "content": m.content, "source": m.source,
            "conversation_id": str(m.conversation_id) if m.conversation_id else None,
            "created_at": m.created_at, "updated_at": m.updated_at}


@router.get("/memories")
def list_memories(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"enabled": (p.user.preferences or {}).get("memory_enabled", True),
            "items": [_mem_pub(m) for m in db.scalars(select(Memory).where(Memory.owner_id == p.id)
                                                       .order_by(Memory.created_at.desc()))]}


@router.post("/memories")
def add_memory(body: MemoryIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    m = Memory(owner_id=p.id, content=body.content.strip(), source="user")
    db.add(m)
    db.commit()
    return _mem_pub(m)


@router.patch("/memories/{mid}")
def edit_memory(mid: uuid.UUID, body: MemoryIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    m = db.get(Memory, mid)
    if not m or m.owner_id != p.id:
        raise not_found("memory")
    m.content = body.content.strip()
    db.commit()
    return _mem_pub(m)


@router.delete("/memories/{mid}")
def delete_memory(mid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    m = db.get(Memory, mid)
    if not m or m.owner_id != p.id:
        raise not_found("memory")
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.delete("/memories")
def clear_memories(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    n = db.execute(delete(Memory).where(Memory.owner_id == p.id)).rowcount
    db.commit()
    return {"deleted": n}
