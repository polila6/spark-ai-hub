"""Chat assistant extensions: skills (install from GitHub / URL, catalog) and custom sub-agents."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher
from ..models import (AgentProfile, CanvasDoc, ConnectorItem, Conversation, KbConnector, ScheduledTask, SchedClass, Skill, User,
                      Visibility)
from ..services import canvas as canvas_svc
from ..services import connectors as conn_svc
from ..services import kb
from ..services import sandbox as sandbox_svc
from ..services import skills as skills_svc
from ..services import tasks as tasks_svc
from ..services import voice as voice_svc
from ..services import web
from ..services.agent import BUILTIN_AGENTS, SUB_AGENT_TOOLS
from ..settings_store import get_setting

router = APIRouter(prefix="/api/v1", tags=["assistant"])

TOOL_NAMES = {"web_search": "Búsqueda web", "fetch_url": "Leer páginas web", "search_images": "Buscar imágenes", "download_file": "Descargar archivos e imágenes", "view_image": "Mirar imágenes y páginas (visión)", "search_knowledge": "Buscar en documentos",
              "read_document": "Leer documentos", "describe_data": "Explorar datos", "query_data": "Consultas SQL",
              "use_skill": "Usar skills", "read_skill_file": "Archivos de skills", "run_code": "Ejecutar código",
              "ws_list": "Workspace: ver archivos", "ws_read": "Workspace: leer", "ws_write": "Workspace: escribir",
              "ws_append": "Workspace: añadir", "ws_edit": "Workspace: editar", "ws_run": "Workspace: ejecutar"}


def _err(e: Exception, code: str = "skill_error"):
    return HTTPException(422, detail={"code": code, "message": str(e)})


# ------------------------------------------------------------------ skills


class InstallIn(BaseModel):
    source: str = Field(min_length=3, max_length=1024)


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=1500)
    content: str = Field(min_length=1, max_length=200_000)


class SkillPatch(BaseModel):
    enabled: bool | None = None
    visibility: Visibility | None = None
    description: str | None = Field(default=None, max_length=1500)
    content: str | None = Field(default=None, max_length=200_000)


def _can_edit(p: Principal, owner_id) -> bool:
    """Shared skills/agents: everyone may use them; only the owner or an admin/manager may change or delete them."""
    return owner_id == p.id or p.is_manager


def _owners(db: Session, rows) -> dict:
    ids = {r.owner_id for r in rows}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}


def _sorted_shared(rows, uid) -> list:
    """Same-named rows next to each other, the one this user gets first (deterministic for everyone)."""
    return sorted(rows, key=lambda r: (r.name, skills_svc.share_rank(r, uid)))


def _skill_for_write(db: Session, p: Principal, sid: uuid.UUID) -> Skill:
    sk = db.get(Skill, sid)
    if not sk or (sk.owner_id != p.id and sk.visibility != Visibility.public):
        raise not_found("skill")
    if not _can_edit(p, sk.owner_id):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Solo quien la compartió (o un administrador) puede cambiarla"})
    return sk


def _skill_pub(sk: Skill, p: Principal, owners: dict | None = None, in_use: bool | None = None) -> dict:
    return skills_svc.public(sk, p.id, owner=(owners or {}).get(sk.owner_id), can_edit=_can_edit(p, sk.owner_id), in_use=in_use)


@router.get("/skills")
def list_skills(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = _sorted_shared(db.scalars(select(Skill).where(or_(Skill.owner_id == p.id, Skill.visibility == Visibility.public))).all(), p.id)
    owners = _owners(db, rows)
    used = {s.id for s in skills_svc.pick_by_name([r for r in rows if r.enabled], p.id).values()}
    return {"skills": [_skill_pub(s, p, owners, s.id in used) for s in rows],
            "settings": {"enabled": bool(get_setting(db, "skills_enabled")),
                         "auto_install": bool(get_setting(db, "skills_auto_install")),
                         "sources": get_setting(db, "skill_sources") or []}}


@router.get("/skills/catalog")
def skill_catalog(refresh: bool = False, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    have = {s.name for s in skills_svc.visible(db, p.user, enabled_only=False)}
    items = skills_svc.catalog(db, refresh=refresh and p.is_manager)
    return [{**c, "installed": c.get("name") in have} for c in items]


@router.get("/skills/{sid}")
def get_skill(sid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    sk = db.get(Skill, sid)
    if not sk or (sk.owner_id != p.id and sk.visibility != Visibility.public):
        raise not_found("skill")
    return {**_skill_pub(sk, p, _owners(db, [sk])), "content": sk.content, "file_contents": sk.files or {}}


@router.post("/skills/install")
def install_skill(body: InstallIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        sk = skills_svc.install(db, p.user, body.source, installed_by="user")
    except (skills_svc.SkillError, web.WebError) as e:
        raise _err(e)
    db.commit()
    return _skill_pub(sk, p, _owners(db, [sk]))


@router.post("/skills")
def create_skill(body: SkillIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    """Write a skill by hand (or paste a SKILL.md: its frontmatter fills name and description)."""
    info = skills_svc.parse_skill_md(body.content)
    name = skills_svc.slug(body.name or info["name"])
    if db.scalar(select(Skill.id).where(Skill.owner_id == p.id, Skill.name == name)):
        raise HTTPException(409, detail={"code": "exists", "message": f"Ya tienes una skill llamada {name}"})
    sk = Skill(owner_id=p.id, name=name, description=body.description or info["description"], content=info["content"],
               files={}, installed_by="user", visibility=Visibility.public)  # shared with everyone by default
    db.add(sk)
    db.commit()
    return _skill_pub(sk, p, _owners(db, [sk]))


@router.patch("/skills/{sid}")
def patch_skill(sid: uuid.UUID, body: SkillPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    sk = _skill_for_write(db, p, sid)
    data = body.model_dump(exclude_unset=True)
    if data.get("visibility") == Visibility.shared:  # skills are either for everyone or private
        data["visibility"] = Visibility.public
    for k, v in data.items():
        setattr(sk, k, v)
    db.commit()
    return _skill_pub(sk, p, _owners(db, [sk]))


@router.post("/skills/{sid}/update")
def update_skill(sid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Re-download the skill from its source."""
    sk = _skill_for_write(db, p, sid)
    if sk.installed_by == skills_svc.BUILTIN:  # shipped with the hub: restore the original content
        try:
            skills_svc.restore_builtin(db, sk)
        except skills_svc.SkillError as e:
            raise _err(e)
        db.commit()
        return _skill_pub(sk, p, _owners(db, [sk]))
    if not sk.source_url:
        raise _err(Exception("Esta skill no tiene origen (se escribió a mano)"))
    try:  # re-download into the owner's row (an admin/manager may refresh someone else's shared skill)
        sk = skills_svc.install(db, db.get(User, sk.owner_id), sk.source_url, installed_by=sk.installed_by)
    except (skills_svc.SkillError, web.WebError) as e:
        raise _err(e)
    db.commit()
    return _skill_pub(sk, p, _owners(db, [sk]))


@router.delete("/skills/{sid}")
def delete_skill(sid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    sk = _skill_for_write(db, p, sid)
    if sk.installed_by == skills_svc.BUILTIN:  # would come back at the next start-up: switch it off instead
        sk.enabled = False
        db.commit()
        return {"ok": True, "disabled": True}
    db.delete(sk)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ agents


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=1000)
    instructions: str = Field(min_length=1, max_length=30_000)
    tools: list[str] = Field(default=[], max_length=12)
    skills: list[str] = Field(default=[], max_length=3)
    visibility: Visibility = Visibility.public  # shared with everyone by default (the owner can make it private)
    enabled: bool = True


def _agent_pub(a: AgentProfile, p: Principal, owners: dict | None = None, in_use: bool | None = None) -> dict:
    return {"id": str(a.id), "name": a.name, "description": a.description, "instructions": a.instructions,
            "tools": a.tools, "skills": a.skills, "visibility": a.visibility.value, "enabled": a.enabled,
            "builtin": False, "mine": a.owner_id == p.id, "can_edit": _can_edit(p, a.owner_id),
            "owner": skills_svc.owner_info((owners or {}).get(a.owner_id)), "in_use": in_use, "updated_at": a.updated_at}


def _agent_for_write(db: Session, p: Principal, aid: uuid.UUID) -> AgentProfile:
    a = db.get(AgentProfile, aid)
    if not a or (a.owner_id != p.id and a.visibility != Visibility.public):
        raise not_found("agent")
    if not _can_edit(p, a.owner_id):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Solo quien lo compartió (o un administrador) puede cambiarlo"})
    return a


def _check_agent(body: AgentIn, p: Principal) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", body.name.strip().lower()).strip("-")[:64]
    if not name or name in BUILTIN_AGENTS:
        raise HTTPException(422, detail={"code": "invalid", "message": f"Nombre no válido o reservado: {body.name}"})
    bad = [t for t in body.tools if t not in SUB_AGENT_TOOLS]
    if bad:
        raise HTTPException(422, detail={"code": "invalid", "message": f"Herramientas no válidas: {', '.join(bad)}"})
    if body.visibility == Visibility.shared:  # agents are either for everyone or private
        body.visibility = Visibility.public
    return name


@router.get("/agents")
def list_agents(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = _sorted_shared(db.scalars(select(AgentProfile).where(
        or_(AgentProfile.owner_id == p.id, AgentProfile.visibility == Visibility.public))).all(), p.id)
    owners = _owners(db, rows)
    used = {a.id for a in skills_svc.pick_by_name([r for r in rows if r.enabled], p.id).values()}
    builtin = [{"id": f"builtin:{k}", "name": k, "description": v["description"], "instructions": v["instructions"],
                "tools": v["tools"], "skills": v.get("skills", []), "builtin": True, "enabled": True, "visibility": "public", "mine": False}
               for k, v in BUILTIN_AGENTS.items()]
    return {"agents": builtin + [_agent_pub(a, p, owners, a.id in used) for a in rows],
            "tools": [{"name": t, "label": TOOL_NAMES.get(t, t)} for t in sorted(SUB_AGENT_TOOLS)],
            "settings": {"enabled": bool(get_setting(db, "agents_enabled")),
                         "max_parallel": int(get_setting(db, "max_parallel_agents") or 3)}}


@router.post("/agents")
def create_agent(body: AgentIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    name = _check_agent(body, p)
    if db.scalar(select(AgentProfile.id).where(AgentProfile.owner_id == p.id, AgentProfile.name == name)):
        raise HTTPException(409, detail={"code": "exists", "message": f"Ya tienes un agente llamado {name}"})
    a = AgentProfile(owner_id=p.id, **{**body.model_dump(), "name": name})
    db.add(a)
    db.commit()
    return _agent_pub(a, p, _owners(db, [a]))


@router.put("/agents/{aid}")
def update_agent(aid: uuid.UUID, body: AgentIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = _agent_for_write(db, p, aid)
    name = _check_agent(body, p)
    if name != a.name and db.scalar(select(AgentProfile.id).where(AgentProfile.owner_id == a.owner_id, AgentProfile.name == name)):
        raise HTTPException(409, detail={"code": "exists", "message": f"Ya existe un agente llamado {name}"})
    for k, v in {**body.model_dump(), "name": name}.items():
        setattr(a, k, v)
    db.commit()
    return _agent_pub(a, p, _owners(db, [a]))


@router.delete("/agents/{aid}")
def delete_agent(aid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = _agent_for_write(db, p, aid)
    db.delete(a)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ voice


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    lang: str | None = Field(default=None, max_length=8)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)


def _voice_err(e: voice_svc.VoiceError):
    return HTTPException(422, detail={"code": "voice_error", "message": e.message, "remediation": e.remediation})


@router.get("/voice")
def voice_status(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return voice_svc.status(db)


@router.post("/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...), language: str | None = Form(default=None),
                           p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    data = await audio.read(voice_svc.MAX_AUDIO_BYTES + 1)
    from starlette.concurrency import run_in_threadpool

    try:
        return await run_in_threadpool(voice_svc.transcribe, db, data, audio.filename or "audio.webm", language)
    except voice_svc.VoiceError as e:
        raise _voice_err(e)
    except Exception as e:  # noqa: BLE001  (engine start-up, memory)
        raise HTTPException(503, detail={"code": "voice_unavailable", "message": str(getattr(e, "message", e))[:300]})


@router.post("/voice/speak")
def voice_speak(body: SpeakIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        audio, mime = voice_svc.speak(db, body.text, body.lang, body.rate)
    except voice_svc.VoiceError as e:
        raise _voice_err(e)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, detail={"code": "voice_unavailable", "message": str(getattr(e, "message", e))[:300]})
    return Response(audio, media_type=mime, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ canvas


class CanvasIn(BaseModel):
    title: str = Field(default="Documento", max_length=255)
    content: str = Field(default="", max_length=canvas_svc.MAX_CHARS)
    kind: str = Field(default="document", pattern="^(document|code)$")
    language: str | None = Field(default=None, max_length=32)
    conversation_id: uuid.UUID | None = None


class CanvasPut(BaseModel):
    content: str = Field(max_length=canvas_svc.MAX_CHARS)
    title: str | None = Field(default=None, max_length=255)
    base_version: int | None = None


class ExportIn(BaseModel):
    format: str = Field(default="docx", pattern="^(md|txt|docx|pdf|html|odt|code)$")
    save: bool = False


def _canvas(db: Session, p: Principal, did: uuid.UUID) -> CanvasDoc:
    d = db.get(CanvasDoc, did)
    if not d or d.owner_id != p.id:
        raise not_found("canvas")
    return d


def _canvas_err(e: canvas_svc.CanvasError):
    return HTTPException(e.status, detail={"code": "canvas_error", "message": e.message})


@router.get("/canvas")
def list_canvas(conversation_id: uuid.UUID | None = None, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    q = select(CanvasDoc).where(CanvasDoc.owner_id == p.id)
    if conversation_id:
        q = q.where(CanvasDoc.conversation_id == conversation_id)
    return [canvas_svc.public(d, with_content=False) for d in db.scalars(q.order_by(CanvasDoc.updated_at.desc()).limit(200))]


@router.get("/canvas/{did}")
def get_canvas(did: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return canvas_svc.public(_canvas(db, p, did))


@router.post("/canvas")
def create_canvas(body: CanvasIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if body.conversation_id:
        c = db.get(Conversation, body.conversation_id)
        if not c or c.owner_id != p.id:
            raise not_found("conversation")
    d = canvas_svc.create(db, p.user, title=body.title, content=body.content, kind=body.kind, language=body.language,
                          conversation_id=body.conversation_id, author="user")
    db.commit()
    return canvas_svc.public(d)


@router.put("/canvas/{did}")
def save_canvas(did: uuid.UUID, body: CanvasPut, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    d = _canvas(db, p, did)
    try:
        canvas_svc.update(db, d, content=body.content, author="user", title=body.title, base_version=body.base_version)
    except canvas_svc.CanvasError as e:
        raise _canvas_err(e)
    db.commit()
    return canvas_svc.public(d)


@router.post("/canvas/{did}/restore/{version}")
def restore_canvas(did: uuid.UUID, version: int, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    d = _canvas(db, p, did)
    try:
        canvas_svc.restore(db, d, version, "user")
    except canvas_svc.CanvasError as e:
        raise _canvas_err(e)
    db.commit()
    return canvas_svc.public(d)


@router.delete("/canvas/{did}")
def delete_canvas(did: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    db.delete(_canvas(db, p, did))
    db.commit()
    return {"ok": True}


@router.post("/canvas/{did}/export")
def export_canvas(did: uuid.UUID, body: ExportIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    d = _canvas(db, p, did)
    try:
        data, name, mime, saved = canvas_svc.export(db, p.user, d, body.format, save=body.save)
    except canvas_svc.CanvasError as e:
        raise _canvas_err(e)
    except sandbox_svc.SandboxError as e:
        raise HTTPException(503, detail={"code": "sandbox", "message": e.message, "remediation": e.remediation})
    if body.save:
        return {"ok": True, "file": saved}
    from urllib.parse import quote

    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ------------------------------------------------------------------ scheduled tasks


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=20000)
    schedule: dict
    timezone: str = Field(default="Europe/Madrid", max_length=64)
    model_alias: str | None = Field(default=None, max_length=128)
    options: dict = {}
    project_id: uuid.UUID | None = None
    enabled: bool = True


def _lang(p: Principal) -> str:
    return (p.user.preferences or {}).get("language") or "es"


def _task(db: Session, p: Principal, tid: uuid.UUID) -> ScheduledTask:
    t = db.get(ScheduledTask, tid)
    if not t or t.owner_id != p.id:
        raise not_found("task")
    return t


def _apply_task(t: ScheduledTask, body: TaskIn) -> None:
    try:
        sched = tasks_svc.validate(body.schedule, body.timezone)
        t.next_run_at = tasks_svc.next_run(sched, body.timezone) if body.enabled else None
    except tasks_svc.TaskError as e:
        raise HTTPException(422, detail={"code": "invalid_schedule", "message": e.message})
    t.name, t.prompt, t.schedule, t.timezone = body.name, body.prompt, sched, body.timezone
    t.model_alias, t.project_id, t.enabled = body.model_alias, body.project_id, body.enabled
    t.options = {k: bool(v) for k, v in body.options.items() if k in ("web", "code", "email", "research")}


@router.get("/tasks")
def list_tasks(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(ScheduledTask).where(ScheduledTask.owner_id == p.id).order_by(ScheduledTask.created_at)).all()
    return {"tasks": [tasks_svc.public(t, _lang(p)) for t in rows], "mail_enabled": bool(get_setting(db, "mail_enabled"))}


@router.post("/tasks")
def create_task(body: TaskIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(ScheduledTask).where(ScheduledTask.owner_id == p.id)) >= 50:
        raise HTTPException(409, detail={"code": "too_many", "message": "Máximo 50 tareas por usuario"})
    t = ScheduledTask(owner_id=p.id)
    _apply_task(t, body)
    db.add(t)
    db.commit()
    return tasks_svc.public(t, _lang(p))


@router.put("/tasks/{tid}")
def update_task(tid: uuid.UUID, body: TaskIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    t = _task(db, p, tid)
    _apply_task(t, body)
    db.commit()
    return tasks_svc.public(t, _lang(p))


@router.post("/tasks/{tid}/run")
def run_task_now(tid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    t = _task(db, p, tid)
    if t.last_status == "running":
        raise HTTPException(409, detail={"code": "running", "message": "La tarea ya se está ejecutando"})
    import threading

    threading.Thread(target=tasks_svc.run_task, args=(t.id, True), daemon=True).start()
    return {"ok": True, "conversation_id": str(t.conversation_id) if t.conversation_id else None}


@router.delete("/tasks/{tid}")
def delete_task(tid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    db.delete(_task(db, p, tid))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ knowledge connectors


class ConnectorIn(BaseModel):
    kind: str = Field(pattern="^(webdav|github|zotero|smb)$")
    name: str = Field(min_length=1, max_length=120)
    config: dict = {}
    secret: str | None = Field(default=None, max_length=4000)  # None = keep the stored one, "" = remove
    collection_id: uuid.UUID | None = None
    schedule: str = Field(default="daily", pattern="^(manual|hourly|daily)$")
    enabled: bool = True


def _connector(db: Session, p: Principal, cid: uuid.UUID) -> KbConnector:
    c = db.get(KbConnector, cid)
    if not c or c.owner_id != p.id:
        raise not_found("connector")
    return c


def _apply_connector(db: Session, p: Principal, c: KbConnector, body: ConnectorIn) -> None:
    try:
        c.config = conn_svc.validate(body.kind, body.config)
    except conn_svc.ConnectorError as e:
        raise HTTPException(422, detail={"code": "invalid", "message": e.message})
    if body.collection_id:
        try:
            c.collection_id = kb.writable_collection(db, p.user, body.collection_id).id
        except kb.KbError:
            raise not_found("collection")
    c.kind, c.name, c.schedule, c.enabled = body.kind, body.name, body.schedule, body.enabled
    conn_svc.set_secret(c, body.secret)


@router.get("/connectors")
def list_connectors(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(KbConnector).where(KbConnector.owner_id == p.id).order_by(KbConnector.created_at)).all()
    return {"connectors": [conn_svc.public(c) for c in rows],
            "private_hosts": get_setting(db, "connector_private_hosts") or []}


@router.post("/connectors")
def create_connector(body: ConnectorIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(KbConnector).where(KbConnector.owner_id == p.id)) >= 20:
        raise HTTPException(409, detail={"code": "too_many", "message": "Máximo 20 conectores por usuario"})
    c = KbConnector(owner_id=p.id)
    _apply_connector(db, p, c, body)
    if not c.collection_id:
        c.collection_id = kb.default_collection(db, p.user).id
    db.add(c)
    db.commit()
    return conn_svc.public(c)


@router.put("/connectors/{cid}")
def update_connector(cid: uuid.UUID, body: ConnectorIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _connector(db, p, cid)
    _apply_connector(db, p, c, body)
    db.commit()
    return conn_svc.public(c)


@router.post("/connectors/{cid}/test")
def test_connector(cid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    c = _connector(db, p, cid)
    try:
        return {"ok": True, **conn_svc.test(db, c)}
    except conn_svc.ConnectorError as e:
        return {"ok": False, "message": e.message}


@router.post("/connectors/{cid}/sync")
def sync_connector(cid: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    from ..services.jobs import JobError, create_job

    c = _connector(db, p, cid)
    if c.status == "syncing":
        raise HTTPException(409, detail={"code": "syncing", "message": "Ya se está sincronizando"})
    try:
        j = create_job(db, p.user, "connector_sync", {"connector_id": str(c.id)}, title=f"Sincronizar {c.name}",
                       sched_class=SchedClass.interactive)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"ok": True, "job_id": str(j.id)}


@router.delete("/connectors/{cid}")
def delete_connector(cid: uuid.UUID, keep_files: bool = False, p: Principal = Depends(require_researcher),
                     db: Session = Depends(get_db)):
    c = _connector(db, p, cid)
    if not keep_files:
        for it in db.scalars(select(ConnectorItem).where(ConnectorItem.connector_id == c.id)):
            conn_svc._drop_file(db, it.file_id)
    db.delete(c)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ usage panel


@router.get("/usage")
def usage(days: int = 30, scope: str = "me", user_id: uuid.UUID | None = None, p: Principal = Depends(current_principal),
          db: Session = Depends(get_db)):
    from ..services import usage as usage_svc

    days = max(1, min(365, days))
    if scope == "all" or user_id:
        if not p.is_manager:
            raise HTTPException(403, detail={"code": "forbidden", "message": "Only administrators/managers see everyone's usage"})
        return usage_svc.report(db, [user_id] if user_id else None, days)
    return usage_svc.report(db, [p.id], days)
