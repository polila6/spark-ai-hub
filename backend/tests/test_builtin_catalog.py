"""Built-in catalog: skills shipped in app/data/skills and the extra built-in sub-agents."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models import Role, Skill, User, Visibility
from app.services import skills
from app.services.agent_catalog import EXTRA_AGENTS

ALLOWED_TOOLS = {"web_search", "fetch_url", "search_images", "download_file", "search_knowledge", "read_document", "describe_data", "query_data",
                 "use_skill", "read_skill_file", "run_code", "ws_list", "ws_read", "ws_write", "ws_append", "ws_edit",
                 "ws_run"}
PKGS = skills.builtin_packages()


def test_builtin_skills_exist():
    assert skills.BUILTIN_DIR.is_dir()
    assert len(PKGS) >= 12


def test_builtin_skill_names_unique_and_match_folder():
    names = [p["name"] for p in PKGS]
    assert len(names) == len(set(names))
    for p in PKGS:
        assert p["name"] == p["dir"], f"front-matter name {p['name']} != folder {p['dir']}"


@pytest.mark.parametrize("pkg", PKGS, ids=[p["dir"] for p in PKGS])
def test_builtin_skill_valid(pkg):
    text = (skills.BUILTIN_DIR / pkg["dir"] / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must start with YAML front-matter"
    assert skills.NAME_RE.match(pkg["name"]) and len(pkg["name"]) <= 64
    assert 40 <= len(pkg["description"]) <= 260, "description is shown (first 260 chars) in the chat system prompt"
    assert pkg["license"] == "MIT"
    assert pkg["content"].strip() and len(pkg["content"]) <= 200_000
    assert len(text.splitlines()) <= 250
    assert len(pkg["files"]) < skills.MAX_FILES
    total = len(text.encode())
    for rel, body in pkg["files"].items():
        size = len(body.encode())
        assert size <= skills.MAX_FILE_BYTES, rel
        total += size
    assert total <= skills.MAX_TOTAL_BYTES


@pytest.mark.parametrize("pkg", PKGS, ids=[p["dir"] for p in PKGS])
def test_builtin_skill_python_files_compile(pkg):
    for rel, body in pkg["files"].items():
        if rel.endswith(".py"):
            compile(body, f"{pkg['dir']}/{rel}", "exec")


def test_skills_referenced_by_agents_exist():
    names = {p["name"] for p in PKGS}
    for name, agent in EXTRA_AGENTS.items():
        for sk in agent.get("skills", []):
            assert sk in names, f"agent {name} preloads unknown skill {sk}"
        assert len(agent.get("skills", [])) <= 3


@pytest.mark.parametrize("name", sorted(EXTRA_AGENTS))
def test_extra_agent_valid(name):
    a = EXTRA_AGENTS[name]
    assert skills.NAME_RE.match(name)
    assert isinstance(a["description"], str) and 20 <= len(a["description"]) <= 200
    assert isinstance(a["instructions"], str) and len(a["instructions"]) >= 200
    assert isinstance(a["tools"], list) and a["tools"]
    assert set(a["tools"]) <= ALLOWED_TOOLS, set(a["tools"]) - ALLOWED_TOOLS
    assert len(a["tools"]) == len(set(a["tools"]))
    assert len(a["tools"]) <= 12  # same limit as custom agents (AgentIn.tools)


def test_extra_agents_do_not_shadow_core_agents():
    assert not set(EXTRA_AGENTS) & {"investigador", "documentalista", "analista", "revisor", "general"}


# ------------------------------------------------------------------ loader (in-memory SQLite stand-in for Postgres)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    eng = create_engine("sqlite://")
    User.__table__.create(eng)
    Skill.__table__.create(eng)
    with Session(eng) as s:
        yield s


def test_sync_builtin_idempotent_and_respects_admin(db):
    assert skills.sync_builtin(db) == {"skipped": "no admin"}
    admin = User(username="admin", role=Role.admin, is_active=True)
    db.add(admin)
    db.commit()
    db.add(Skill(owner_id=admin.id, name="tfg-tfm", content="mine", installed_by="user", files={}))
    db.commit()
    st = skills.sync_builtin(db)
    n = len(skills.builtin_packages())
    assert st["created"] == n - 1 and st["skipped"] == 1
    assert skills.sync_builtin(db) == {"created": 0, "updated": 0, "removed": 0, "skipped": 1}
    sk = db.scalar(select(Skill).where(Skill.name == "informe-latex"))
    assert sk.visibility == Visibility.public and sk.installed_by == "builtin" and "plantilla-informe.tex" in sk.files
    sk.enabled = False
    sk.content = "x"
    db.commit()
    st = skills.sync_builtin(db)
    assert st["updated"] == 1
    db.refresh(sk)
    assert sk.enabled is False and sk.content.startswith("# Informes")
    db.add(Skill(owner_id=admin.id, name="obsoleta", content="x", installed_by="builtin", files={}))
    db.commit()
    assert skills.sync_builtin(db)["removed"] == 1
    assert db.scalar(select(Skill).where(Skill.name == "tfg-tfm")).content == "mine"
