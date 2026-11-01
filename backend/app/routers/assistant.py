"""Chat assistant extensions: skills (install from GitHub / URL, catalog) and custom sub-agents."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher
from ..models import AgentProfile, Skill, Visibility
from ..services import skills as skills_svc
from ..services import web
from ..services.agent import BUILTIN_AGENTS, SUB_AGENT_TOOLS
from ..settings_store import get_setting

router = APIRouter(prefix="/api/v1", tags=["assistant"])

TOOL_NAMES = {"web_search": "Búsqueda web", "fetch_url": "Leer páginas web", "search_knowledge": "Buscar en documentos",
              "read_document": "Leer documentos", "describe_data": "Explorar datos", "query_data": "Consultas SQL",
              "use_skill": "Usar skills", "read_skill_file": "Archivos de skills"}


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


def _skill_for_write(db: Session, p: Principal, sid: uuid.UUID) -> Skill:
    sk = db.get(Skill, sid)
    if not sk or sk.owner_id != p.id:
        raise not_found("skill")
    return sk


@router.get("/skills")
def list_skills(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(Skill).where(or_(Skill.owner_id == p.id, Skill.visibility == Visibility.public))
                      .order_by(Skill.name)).all()
    return {"skills": [skills_svc.public(s, p.id) for s in rows],
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
    return {**skills_svc.public(sk, p.id), "content": sk.content, "file_contents": sk.files or {}}


@router.post("/skills/install")
def install_skill(body: InstallIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    try:
        sk = skills_svc.install(db, p.user, body.source, installed_by="user")
    except (skills_svc.SkillError, web.WebError) as e:
        raise _err(e)
    db.commit()
    return skills_svc.public(sk, p.id)


@router.post("/skills")
def create_skill(body: SkillIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    """Write a skill by hand (or paste a SKILL.md: its frontmatter fills name and description)."""
    info = skills_svc.parse_skill_md(body.content)
    name = skills_svc.slug(body.name or info["name"])
    if db.scalar(select(Skill.id).where(Skill.owner_id == p.id, Skill.name == name)):
        raise HTTPException(409, detail={"code": "exists", "message": f"Ya tienes una skill llamada {name}"})
    sk = Skill(owner_id=p.id, name=name, description=body.description or info["description"], content=info["content"],
               files={}, installed_by="user")
    db.add(sk)
    db.commit()
    return skills_svc.public(sk, p.id)


@router.patch("/skills/{sid}")
def patch_skill(sid: uuid.UUID, body: SkillPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    sk = _skill_for_write(db, p, sid)
    data = body.model_dump(exclude_unset=True)
    if data.get("visibility") == Visibility.public and not p.is_manager:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Only admins/managers can publish skills for everyone"})
    for k, v in data.items():
        setattr(sk, k, v)
    db.commit()
    return skills_svc.public(sk, p.id)


@router.post("/skills/{sid}/update")
def update_skill(sid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Re-download the skill from its source."""
    sk = _skill_for_write(db, p, sid)
    if not sk.source_url:
        raise _err(Exception("Esta skill no tiene origen (se escribió a mano)"))
    try:
        sk = skills_svc.install(db, p.user, sk.source_url, installed_by=sk.installed_by)
    except (skills_svc.SkillError, web.WebError) as e:
        raise _err(e)
    db.commit()
    return skills_svc.public(sk, p.id)


@router.delete("/skills/{sid}")
def delete_skill(sid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    db.delete(_skill_for_write(db, p, sid))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ agents


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=1000)
    instructions: str = Field(min_length=1, max_length=30_000)
    tools: list[str] = Field(default=[], max_length=12)
    skills: list[str] = Field(default=[], max_length=3)
    visibility: Visibility = Visibility.private
    enabled: bool = True


def _agent_pub(a: AgentProfile, uid) -> dict:
    return {"id": str(a.id), "name": a.name, "description": a.description, "instructions": a.instructions,
            "tools": a.tools, "skills": a.skills, "visibility": a.visibility.value, "enabled": a.enabled,
            "builtin": False, "mine": a.owner_id == uid, "updated_at": a.updated_at}


def _check_agent(body: AgentIn, p: Principal) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", body.name.strip().lower()).strip("-")[:64]
    if not name or name in BUILTIN_AGENTS:
        raise HTTPException(422, detail={"code": "invalid", "message": f"Nombre no válido o reservado: {body.name}"})
    bad = [t for t in body.tools if t not in SUB_AGENT_TOOLS]
    if bad:
        raise HTTPException(422, detail={"code": "invalid", "message": f"Herramientas no válidas: {', '.join(bad)}"})
    if body.visibility == Visibility.public and not p.is_manager:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Only admins/managers can publish agents"})
    return name


@router.get("/agents")
def list_agents(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentProfile).where(or_(AgentProfile.owner_id == p.id, AgentProfile.visibility == Visibility.public))
                      .order_by(AgentProfile.name)).all()
    builtin = [{"id": f"builtin:{k}", "name": k, "description": v["description"], "instructions": v["instructions"],
                "tools": v["tools"], "skills": [], "builtin": True, "enabled": True, "visibility": "public", "mine": False}
               for k, v in BUILTIN_AGENTS.items()]
    return {"agents": builtin + [_agent_pub(a, p.id) for a in rows],
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
    return _agent_pub(a, p.id)


@router.put("/agents/{aid}")
def update_agent(aid: uuid.UUID, body: AgentIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = db.get(AgentProfile, aid)
    if not a or a.owner_id != p.id:
        raise not_found("agent")
    name = _check_agent(body, p)
    for k, v in {**body.model_dump(), "name": name}.items():
        setattr(a, k, v)
    db.commit()
    return _agent_pub(a, p.id)


@router.delete("/agents/{aid}")
def delete_agent(aid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = db.get(AgentProfile, aid)
    if not a or a.owner_id != p.id:
        raise not_found("agent")
    db.delete(a)
    db.commit()
    return {"ok": True}
