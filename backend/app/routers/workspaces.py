"""Workspaces API: persistent remote folders with files, a sandboxed terminal and the assistant."""
from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher
from ..models import Conversation, Workspace
from ..services import sandbox as sandbox_svc
from ..services import workspace as ws_svc
from ..settings_store import get_setting

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


class WsIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class FileIn(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=5_000_000)


class PathIn(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


class MoveIn(BaseModel):
    src: str = Field(min_length=1, max_length=2000)
    dst: str = Field(min_length=1, max_length=2000)


class RunIn(BaseModel):
    command: str = Field(min_length=1, max_length=100_000)
    timeout_s: int | None = Field(default=None, ge=5, le=900)
    background: bool = False  # True: return at once; follow it with GET /runs (survives leaving the page)


class ImportIn(BaseModel):
    file_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    folder: str = Field(default="", max_length=2000)


def _ws(db: Session, p: Principal, wid: uuid.UUID) -> Workspace:
    ws = db.get(Workspace, wid)
    if not ws or ws.owner_id != p.id:
        raise not_found("workspace")
    return ws


def _err(e: Exception):
    if isinstance(e, ws_svc.WorkspaceError):
        return HTTPException(e.status, detail={"code": "workspace_error", "message": e.message})
    if isinstance(e, sandbox_svc.SandboxError):
        return HTTPException(503, detail={"code": "sandbox", "message": e.message, "remediation": e.remediation})
    raise e


@router.get("")
def list_workspaces(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = ws_svc.get_or_default(db, p.user)
    for w in rows:  # keep the shown size current (uploads and edits do not go through the sandbox)
        w.size_bytes = ws_svc.size_of(w)
    db.commit()
    return {"workspaces": [ws_svc.public(w) for w in rows],
            "limits": {"timeout_s": int(get_setting(db, "workspace_timeout_s") or 600),
                       "max_gb": float(get_setting(db, "workspace_max_gb") or 10),
                       "code_enabled": bool(get_setting(db, "code_exec_enabled")),
                       "packages": bool(get_setting(db, "workspace_packages_enabled"))}}


@router.post("")
def create_workspace(body: WsIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(Workspace).where(Workspace.owner_id == p.id)) >= 20:
        raise HTTPException(409, detail={"code": "too_many", "message": "Máximo 20 workspaces por usuario"})
    if db.scalar(select(Workspace.id).where(Workspace.owner_id == p.id, Workspace.name == body.name.strip())):
        raise HTTPException(409, detail={"code": "exists", "message": "Ya tienes un workspace con ese nombre"})
    ws = Workspace(owner_id=p.id, name=body.name.strip(), description=body.description)
    db.add(ws)
    db.commit()
    ws_svc.ensure(ws)
    return ws_svc.public(ws)


@router.patch("/{wid}")
def rename_workspace(wid: uuid.UUID, body: WsIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    ws = _ws(db, p, wid)
    name = body.name.strip()
    if db.scalar(select(Workspace.id).where(Workspace.owner_id == p.id, Workspace.name == name, Workspace.id != ws.id)):
        raise HTTPException(409, detail={"code": "exists", "message": "Ya tienes un workspace con ese nombre"})
    ws.name, ws.description = name, body.description
    conv = db.get(Conversation, ws.conversation_id) if ws.conversation_id else None
    if conv is not None and (conv.title or "").startswith("🧰 "):  # the assistant's chat keeps the workspace name
        conv.title = f"🧰 {name}"[:255]
    db.commit()
    return ws_svc.public(ws)


@router.delete("/{wid}")
def delete_workspace(wid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    import shutil

    from ..services import turns

    ws = _ws(db, p, wid)
    for c in db.scalars(select(Conversation).where(Conversation.owner_id == p.id,
                                                   Conversation.settings["workspace_id"].astext == str(ws.id))):
        turns.forget(c.id, p.id)  # stop a reply that is still working in this workspace
        db.delete(c)
    ws_svc.clear_runs(ws)
    shutil.rmtree(ws_svc.root(ws), ignore_errors=True)
    db.delete(ws)
    db.commit()
    return {"ok": True}


@router.get("/{wid}/tree")
def tree(wid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"entries": ws_svc.tree(_ws(db, p, wid))}


@router.get("/{wid}/file")
def read_file(wid: uuid.UUID, path: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    try:
        return ws_svc.read(_ws(db, p, wid), path)
    except ws_svc.WorkspaceError as e:
        raise _err(e)


@router.get("/{wid}/raw")
def raw_file(wid: uuid.UUID, path: str, download: bool = False, p: Principal = Depends(current_principal),
             db: Session = Depends(get_db)):
    """The file itself (previews and downloads). HTML/SVG are served inside a CSP sandbox: scripts cannot reach the hub."""
    try:
        fp, mime = ws_svc.raw(_ws(db, p, wid), path)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    if mime != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox allow-scripts; default-src 'self' data: blob: 'unsafe-inline'"
    inline = not download and (mime.startswith(("image/", "text/", "audio/", "video/")) or mime in ("application/pdf", "application/json"))
    return FileResponse(fp, media_type=mime if inline else "application/octet-stream", headers=headers,
                        filename=None if inline else fp.name)


@router.get("/{wid}/table")
def table(wid: uuid.UUID, path: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    try:
        return ws_svc.table_preview(_ws(db, p, wid), path)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    except Exception as e:  # noqa: BLE001  (corrupt spreadsheets)
        raise HTTPException(422, detail={"code": "unreadable", "message": f"No se pudo leer la tabla: {type(e).__name__}"})


@router.put("/{wid}/file")
def write_file(wid: uuid.UUID, body: FileIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    ws = _ws(db, p, wid)
    try:
        r = ws_svc.write(db, ws, body.path, body.content.encode())
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    db.commit()
    return r


@router.post("/{wid}/upload")
async def upload(wid: uuid.UUID, file: UploadFile = File(...), folder: str = Form(default=""),
                 p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    from starlette.concurrency import run_in_threadpool

    from ..services.uploads import safe_name

    ws = _ws(db, p, wid)
    target = f"{folder.strip('/')}/{safe_name(file.filename or 'archivo')}".lstrip("/")
    try:
        r = await run_in_threadpool(ws_svc.write_stream, db, ws, target, file.file, 500 * 1024 * 1024)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    db.commit()
    return r


@router.post("/{wid}/import")
def import_files(wid: uuid.UUID, body: ImportIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        out = ws_svc.import_files(db, _ws(db, p, wid), p.user, body.file_ids, body.folder)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    db.commit()
    return {"imported": out}


@router.post("/{wid}/mkdir")
def mkdir(wid: uuid.UUID, body: PathIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        ws_svc.mkdir(_ws(db, p, wid), body.path)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    return {"ok": True}


@router.post("/{wid}/move")
def move(wid: uuid.UUID, body: MoveIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        ws_svc.move(_ws(db, p, wid), body.src, body.dst)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    return {"ok": True}


@router.delete("/{wid}/file")
def delete_file(wid: uuid.UUID, path: str, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        ws_svc.delete(_ws(db, p, wid), path)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    return {"ok": True}


@router.post("/{wid}/run")
def run(wid: uuid.UUID, body: RunIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    ws = _ws(db, p, wid)
    if body.background:
        if not get_setting(db, "code_exec_enabled"):
            raise HTTPException(503, detail={"code": "sandbox", "message": "La ejecución de código está desactivada."})
        if sum(1 for r in ws_svc.list_runs(ws) if r["status"] == "running") >= 3:
            raise HTTPException(409, detail={"code": "busy", "message": "Ya hay 3 comandos en marcha en este workspace"})
        return ws_svc.start_background_run(ws, body.command, body.timeout_s)
    try:
        return ws_svc.run(db, ws, body.command, timeout_s=body.timeout_s)
    except (ws_svc.WorkspaceError, sandbox_svc.SandboxError) as e:
        raise _err(e)


@router.get("/{wid}/runs")
def runs(wid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return {"runs": ws_svc.list_runs(_ws(db, p, wid))}


@router.delete("/{wid}/runs")
def clear_runs(wid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    ws_svc.clear_runs(_ws(db, p, wid), keep_running=True)
    return {"ok": True}


@router.post("/{wid}/runs/{rid}/stop")
def stop_run(wid: uuid.UUID, rid: str, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        return ws_svc.stop_run(_ws(db, p, wid), rid)
    except ws_svc.WorkspaceError as e:
        raise _err(e)


@router.get("/{wid}/zip")
def zip_download(wid: uuid.UUID, path: str = "", p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    ws = _ws(db, p, wid)
    try:
        data = ws_svc.zip_bytes(ws, path)
    except ws_svc.WorkspaceError as e:
        raise _err(e)
    name = f"{(path.strip('/').split('/')[-1] if path else ws.name) or 'workspace'}.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/{wid}/export")
def export(wid: uuid.UUID, body: PathIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        return {"file": ws_svc.export_file(db, _ws(db, p, wid), p.user, body.path)}
    except ws_svc.WorkspaceError as e:
        raise _err(e)


@router.post("/{wid}/conversation")
def conversation(wid: uuid.UUID, new: bool = False, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    """The workspace assistant's chat (created on first use, or a fresh one with new=true)."""
    ws = _ws(db, p, wid)
    conv = db.get(Conversation, ws.conversation_id) if ws.conversation_id and not new else None
    if conv is None:
        model = get_setting(db, "chat_default_model") or get_setting(db, "preferred_resident_model")
        conv = Conversation(owner_id=p.id, title=f"🧰 {ws.name}"[:255], model_alias=model,
                            settings={"workspace_id": str(ws.id), "web": True})
        db.add(conv)
        db.flush()
        ws.conversation_id = conv.id
        db.commit()
    return {"conversation_id": str(conv.id), "model_alias": conv.model_alias}
