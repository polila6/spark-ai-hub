"""Skills and custom agents shared with everyone (pure logic, no database).

Run: docker compose run --rm --no-deps backend pytest -q tests/test_sharing.py
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.deps import Principal
from app.models import Role, Skill, User, Visibility
from app.routers import assistant
from app.services import skills

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def row(name, owner, days=0, installed_by="user"):
    return SimpleNamespace(id=uuid.uuid4(), name=name, owner_id=owner, installed_by=installed_by,
                           created_at=T0 + timedelta(days=days), enabled=True, visibility=Visibility.public)


def principal(role=Role.researcher):
    return Principal(user=User(id=uuid.uuid4(), username="u", display_name="U", role=role), via="session")


def test_own_copy_wins_over_shared_ones():
    me, ana, luis = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mine = row("informe", me, days=5)
    picked = skills.pick_by_name([row("informe", ana), mine, row("informe", luis, days=1)], me)
    assert picked["informe"] is mine


def test_same_name_from_others_is_deterministic():
    me, ana, luis = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    older, newer = row("tabla", ana, days=0), row("tabla", luis, days=3)
    assert skills.pick_by_name([newer, older], me)["tabla"] is older
    assert skills.pick_by_name([older, newer], me)["tabla"] is older  # order of the query does not matter
    hub = row("tabla", luis, days=9, installed_by=skills.BUILTIN)
    assert skills.pick_by_name([older, hub, newer], me)["tabla"] is hub  # the hub's copy beats other users' ones


def test_listing_keeps_every_copy_with_the_used_one_first():
    me, ana, luis = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [row("b", luis, 2), row("a", ana, 1), row("b", ana, 1), row("a", me, 3)]
    out = assistant._sorted_shared(rows, me)
    assert [(r.name, r.owner_id) for r in out] == [("a", me), ("a", ana), ("b", ana), ("b", luis)]


def test_only_owner_or_manager_can_edit():
    owner = uuid.uuid4()
    p = principal()
    assert not assistant._can_edit(p, owner)
    assert assistant._can_edit(p, p.id)
    assert assistant._can_edit(principal(Role.manager), owner)
    assert assistant._can_edit(principal(Role.admin), owner)


class FakeDB:
    def __init__(self, obj):
        self.obj = obj

    def get(self, _cls, _id):
        return self.obj


def test_others_can_use_but_not_change_a_shared_skill():
    owner = uuid.uuid4()
    sk = SimpleNamespace(id=uuid.uuid4(), owner_id=owner, visibility=Visibility.public)
    with pytest.raises(HTTPException) as e:
        assistant._skill_for_write(FakeDB(sk), principal(), sk.id)
    assert e.value.status_code == 403
    assert assistant._skill_for_write(FakeDB(sk), principal(Role.manager), sk.id) is sk


def test_private_skill_of_someone_else_is_hidden():
    sk = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), visibility=Visibility.private)
    with pytest.raises(HTTPException) as e:
        assistant._skill_for_write(FakeDB(sk), principal(), sk.id)
    assert e.value.status_code == 404


def test_new_skills_and_agents_are_public_by_default():
    assert Skill.__table__.c.visibility.default.arg == Visibility.public
    from app.models import AgentProfile

    assert AgentProfile.__table__.c.visibility.default.arg == Visibility.public
    assert assistant.AgentIn(name="x", instructions="y").visibility == Visibility.public


def test_install_creates_a_public_skill(monkeypatch):
    """Covers both the Skills page and the assistant's install_skill tool (both call skills.install)."""
    monkeypatch.setattr(skills, "fetch_package", lambda src: {
        "name": "mi-skill", "description": "d", "license": None, "content": "# hola", "files": {}, "source_url": src})
    added = []
    db = SimpleNamespace(scalar=lambda q: None, add=added.append, flush=lambda: None)
    user = User(id=uuid.uuid4(), username="u")
    sk = skills.install(db, user, "https://example.org/SKILL.md", installed_by="assistant")
    assert added == [sk] and sk.visibility == Visibility.public and sk.installed_by == "assistant"


def test_reinstall_keeps_the_owner_choice(monkeypatch):
    monkeypatch.setattr(skills, "fetch_package", lambda src: {
        "name": "mi-skill", "description": "d2", "license": None, "content": "# v2", "files": {}, "source_url": src})
    existing = Skill(owner_id=uuid.uuid4(), name="mi-skill", visibility=Visibility.private)
    db = SimpleNamespace(scalar=lambda q: existing, add=lambda o: None, flush=lambda: None)
    sk = skills.install(db, User(id=existing.owner_id, username="u"), "https://example.org/SKILL.md")
    assert sk is existing and sk.visibility == Visibility.private and sk.content == "# v2"


def test_public_view_shows_owner_and_rights():
    owner = User(id=uuid.uuid4(), username="ana", display_name="Ana García")
    sk = Skill(id=uuid.uuid4(), owner_id=owner.id, name="x", description="", content="c", files={},
               installed_by="user", visibility=Visibility.public, enabled=True, use_count=0)
    viewer = uuid.uuid4()
    d = skills.public(sk, viewer, owner=owner, can_edit=False, in_use=True)
    assert d["owner"]["display_name"] == "Ana García" and not d["mine"] and not d["can_edit"] and d["in_use"]
