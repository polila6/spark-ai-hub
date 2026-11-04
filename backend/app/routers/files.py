"""Personal drive ("Mis archivos"): upload, browse, preview, move and delete the user's files."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher
from ..models import KbDocument, Project, SchedClass, SharedResource, UserFile
from ..services import files as files_svc
from ..services import preview as preview_svc
from ..services import kb, storage
from ..services.jobs import JobError, create_job
from ..services.uploads import UploadError, safe_name, save_stream

router = APIRouter(prefix="/api/v1/files", tags=["files"])

INLINE_EXTRACT_MAX = 25 * 1024 * 1024  # extract small documents during the upload so chats can use them at once


def readable_file(db: Session, p: Principal, fid: uuid.UUID) -> UserFile:
    f = db.get(UserFile, fid)
    if not f:
        raise not_found("file")
    if f.owner_id == p.id:
        return f
    if f.project_id:  # files of a project shared with the user
        proj = db.get(Project, f.project_id)
        if proj and db.scalar(select(SharedResource.id).where(SharedResource.resource_type == "project",
                                                              SharedResource.resource_id == proj.id,
                                                              SharedResource.grantee_id == p.id)):
            return f
    if db.scalar(select(KbDocument.id).where(KbDocument.file_id == f.id,
                                             KbDocument.collection_id.in_(kb.readable_collection_ids(db, p.user)))):
        return f
    raise not_found("file")


def own_file(db: Session, p: Principal, fid: uuid.UUID) -> UserFile:
    f = db.get(UserFile, fid)
    if not f or f.owner_id != p.id:
        raise not_found("file")
    return f


def _clean_folder(folder: str) -> str:
    parts = [safe_name(x) for x in folder.replace("\\", "/").split("/") if x.strip() and x not in (".", "..")]
    return "/".join(parts)[:255]


async def store_upload(db: Session, p: Principal, file: UploadFile, *, folder: str = "", project_id: uuid.UUID | None = None,
                       collection_ids: list[uuid.UUID] | None = None, source: str = "upload", index: bool = True) -> UserFile:
    """Save an upload into the drive; extract text (inline for small documents) and queue indexing."""
    fid = uuid.uuid4()
    tmp = files_svc.user_dir(p.id) / f"{fid}.upload"
    try:
        storage.ensure_user_quota(db, p.user, 0)
        size, sha = await save_stream(file, tmp, max_bytes=500 * 1024 * 1024)
        with open(tmp, "rb") as fh:
            head = fh.read(64)
        kind, ext = files_svc.classify(file.filename or "file", head)
        storage.ensure_user_quota(db, p.user, size)
    except (UploadError, files_svc.FileError) as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, detail={"code": e.code, "message": e.message})
    except storage.StorageError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    final = tmp.with_name(f"{fid}{ext}")
    tmp.rename(final)
    from ..config import get_settings

    f = UserFile(id=fid, owner_id=p.id, project_id=project_id, folder=_clean_folder(folder),
                 filename=safe_name(Path(file.filename or "file").stem) + ext, mime=files_svc.guess_mime(ext), kind=kind,
                 size_bytes=size, sha256=sha, path=str(final.relative_to(get_settings().data_root)),
                 text_status="none", source=source)
    if kind == "image":
        try:
            from PIL import Image

            with Image.open(final) as im:
                f.meta = {"width": im.width, "height": im.height}
        except Exception:  # noqa: BLE001
            pass
    db.add(f)
    db.flush()
    targets: list[uuid.UUID] = []
    if index and kind in ("document", "data") and ext not in (".sqlite", ".db", ".parquet"):
        if project_id:
            proj = db.get(Project, project_id)
            if proj and proj.collection_id:
                targets.append(proj.collection_id)
        for cid in collection_ids or []:
            try:
                targets.append(kb.writable_collection(db, p.user, cid).id)
            except kb.KbError:
                raise HTTPException(404, detail={"code": "not_found", "message": "collection not found"})
        for cid in dict.fromkeys(targets):
            db.add(KbDocument(collection_id=cid, file_id=f.id, owner_id=p.id, status="pending"))
    db.commit()
    if kind in ("document", "data"):
        if size <= INLINE_EXTRACT_MAX and not targets:
            try:
                res = await asyncio.to_thread(files_svc.process, f)
                f.text_status = {"ready": "ready", "unsupported": "unsupported"}.get(res["status"], "failed")
                f.text_chars = res["chars"]
                f.meta = {**(f.meta or {}), "pages": res["pages"]}
            except Exception as e:  # noqa: BLE001
                f.text_status, f.meta = "failed", {**(f.meta or {}), "extract_error": str(e)[:300]}
            db.commit()
        else:
            try:
                create_job(db, p.user, "file_process", {"file_id": str(f.id)}, title=f"Procesar {f.filename}",
                           sched_class=SchedClass.interactive)
                f.text_status = "pending"
                db.commit()
            except JobError:
                pass
    return f


@router.post("")
async def upload_file(file: UploadFile = File(...), folder: str = Form(default=""), project_id: str | None = Form(default=None),
                      collection_id: str | None = Form(default=None), add_to_knowledge: bool = Form(default=True),
                      p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    pid = uuid.UUID(project_id) if project_id else None
    if pid:
        from .projects import project_for_write

        project_for_write(db, p, pid)
    cids: list[uuid.UUID] = []
    if collection_id:
        cids.append(uuid.UUID(collection_id))
    elif add_to_knowledge and not pid:
        cids.append(kb.default_collection(db, p.user).id)
        db.commit()
    f = await store_upload(db, p, file, folder=folder, project_id=pid, collection_ids=cids, index=add_to_knowledge)
    return files_svc.public(f)


@router.get("")
def list_files(p: Principal = Depends(current_principal), db: Session = Depends(get_db), folder: str | None = None,
               kind: str | None = None, q: str | None = None, project_id: uuid.UUID | None = None, limit: int = 200,
               offset: int = 0):
    query = select(UserFile).order_by(UserFile.created_at.desc())
    if project_id:
        from .projects import project_for_read

        project_for_read(db, p, project_id)
        query = query.where(UserFile.project_id == project_id)
    else:
        query = query.where(UserFile.owner_id == p.id)
    if folder is not None:
        query = query.where(UserFile.folder == folder)
    if kind:
        query = query.where(UserFile.kind.in_(kind.split(",")))
    if q:
        query = query.where(UserFile.filename.ilike(f"%{q.replace('%', '')}%"))
    rows = db.scalars(query.limit(min(limit, 500)).offset(offset)).all()
    docs = {}
    if rows:
        for d in db.scalars(select(KbDocument).where(KbDocument.file_id.in_([r.id for r in rows]))):
            docs.setdefault(d.file_id, []).append({"collection_id": str(d.collection_id), "status": d.status,
                                                   "chunks": d.chunks, "error": d.error})
    return [{**files_svc.public(f), "knowledge": docs.get(f.id, [])} for f in rows]


@router.get("/folders")
def list_folders(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(UserFile.folder, func.count(), func.sum(UserFile.size_bytes))
                      .where(UserFile.owner_id == p.id).group_by(UserFile.folder)).all()
    return [{"folder": f, "files": n, "size_bytes": int(s or 0)} for f, n, s in rows]


@router.get("/{fid}")
def get_file(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = readable_file(db, p, fid)
    return {**files_svc.public(f), "preview": files_svc.read_text(f, 4000) if f.text_status == "ready" else None}


@router.get("/{fid}/content")
def file_content(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                 download: bool = False):
    f = readable_file(db, p, fid)
    path = files_svc.abs_path(f)
    if not path.exists():
        raise HTTPException(410, detail={"code": "file_missing", "message": "File no longer exists"})
    video = preview_svc.VIDEO_MIME.get(Path(f.filename).suffix.lower())
    inline_ok = f.kind in ("image", "audio") or f.mime == "application/pdf" or bool(video)
    headers = {"X-Content-Type-Options": "nosniff"}
    if f.mime != "application/pdf":  # the browser PDF viewer refuses to run inside a CSP sandbox
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(path, media_type=(video or f.mime) if inline_ok else "application/octet-stream",
                        filename=f.filename if (download or not inline_ok) else None, headers=headers)


def _viewer_error(e: preview_svc.PreviewError) -> HTTPException:
    return HTTPException(e.status, detail={"code": "preview", "message": e.message})


@router.get("/{fid}/preview.pdf")
def file_preview_pdf(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """A Word/PowerPoint/OpenDocument file rendered as PDF for the document viewer."""
    f = readable_file(db, p, fid)
    path = files_svc.abs_path(f)
    if not path.exists():
        raise HTTPException(410, detail={"code": "file_missing", "message": "File no longer exists"})
    try:
        pdf = preview_svc.office_pdf(db, p.user, path, f.filename, f.sha256)
    except preview_svc.PreviewError as e:
        raise _viewer_error(e)
    return FileResponse(pdf, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff"})


@router.get("/{fid}/table")
def file_table(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = readable_file(db, p, fid)
    try:
        return preview_svc.table(files_svc.abs_path(f), f.filename)
    except preview_svc.PreviewError as e:
        raise _viewer_error(e)
    except Exception as e:  # noqa: BLE001  (corrupt spreadsheets)
        raise HTTPException(422, detail={"code": "unreadable", "message": f"No se pudo leer la tabla: {type(e).__name__}"})


@router.get("/{fid}/plain")
def file_plain(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The file's own text (code, Markdown, logs), never interpreted by the browser."""
    f = readable_file(db, p, fid)
    try:
        return PlainTextResponse(preview_svc.plain_text(files_svc.abs_path(f)), headers={
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})
    except preview_svc.PreviewError as e:
        raise _viewer_error(e)


@router.get("/{fid}/text")
def file_text(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = readable_file(db, p, fid)
    return PlainTextResponse(files_svc.read_text(f, 2_000_000))


class FilePatch(BaseModel):
    filename: str | None = Field(default=None, max_length=255)
    folder: str | None = Field(default=None, max_length=255)
    project_id: uuid.UUID | None = None


@router.patch("/{fid}")
def patch_file(fid: uuid.UUID, body: FilePatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = own_file(db, p, fid)
    data = body.model_dump(exclude_unset=True)
    if "filename" in data and data["filename"]:
        stem = safe_name(Path(data["filename"]).stem)
        f.filename = stem + Path(f.filename).suffix
    if "folder" in data and data["folder"] is not None:
        f.folder = _clean_folder(data["folder"])
    if "project_id" in data:
        if data["project_id"]:
            from .projects import project_for_write

            project_for_write(db, p, data["project_id"])
        f.project_id = data["project_id"]
    db.commit()
    return files_svc.public(f)


@router.post("/{fid}/reprocess")
def reprocess(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = own_file(db, p, fid)
    try:
        j = create_job(db, p.user, "file_process", {"file_id": str(f.id)}, title=f"Procesar {f.filename}",
                       sched_class=SchedClass.interactive)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"job_id": str(j.id)}


@router.delete("/{fid}")
def delete_file(fid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = own_file(db, p, fid)
    for path in (files_svc.abs_path(f), files_svc.text_path(f), files_svc.pages_path(f)):
        path.unlink(missing_ok=True)
    db.delete(f)
    db.commit()
    return {"ok": True}


_ = or_
