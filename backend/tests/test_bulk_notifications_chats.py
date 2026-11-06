"""Bulk actions on notifications and chats: one request, every item checked on its own, nothing leaks about other people's ids."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import Conversation, Notification
from app.routers import account, playground

ME = uuid.uuid4()
OTHER = uuid.uuid4()


def principal(uid=ME):
    return SimpleNamespace(id=uid, user=SimpleNamespace(id=uid, username="ana"))


class Rows(list):
    def all(self):
        return list(self)


class Db:
    """Just enough of a session: records deletes/commits, serves rows by key, answers queries with a canned result."""

    def __init__(self, rows=None, scalar=0, scalars=(), rowcount=0, fail_on=()):
        self.rows, self.deleted, self.commits, self.rollbacks, self.queries = rows or {}, [], 0, 0, []
        self._scalar, self._scalars, self._rowcount, self.fail_on = scalar, Rows(scalars), rowcount, set(fail_on)

    def get(self, cls, key):
        return self.rows.get(key)

    def delete(self, obj):
        if getattr(obj, "id", None) in self.fail_on:
            raise RuntimeError("boom")
        self.deleted.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def scalars(self, q):
        self.queries.append(q)
        return self._scalars

    def scalar(self, q):
        self.queries.append(q)
        return self._scalar

    def execute(self, q):
        self.queries.append(q)
        return SimpleNamespace(rowcount=self._rowcount)


@pytest.fixture
def audits(monkeypatch):
    seen = []
    fake = lambda db, action, **kw: seen.append((action, kw))  # noqa: E731
    monkeypatch.setattr(account, "audit", fake)
    monkeypatch.setattr("app.audit.audit", fake)
    return seen


def note(i, read=False, uid=ME):
    return Notification(id=i, user_id=uid, kind="x", title=f"n{i}", body="", read_at=datetime.now(timezone.utc) if read else None)


# ------------------------------------------------------------------ notifications


def run_bulk(monkeypatch, action, ids, owned, db=None):
    db = db or Db()
    monkeypatch.setattr(account, "_owned_notifications", lambda d, uid, wanted: {n.id: n for n in owned if n.id in wanted})
    out = account.bulk_notifications(account.NotificationBulkIn(ids=ids, action=action), None, principal(), db)
    return out, db


def test_marking_read_and_unread_flips_only_what_was_asked(monkeypatch, audits):
    a, b, c = note(1), note(2), note(3, read=True)
    out, _ = run_bulk(monkeypatch, "read", [1, 2], [a, b, c])
    assert out == {"done": 2, "skipped": []} and a.read_at and b.read_at
    out, _ = run_bulk(monkeypatch, "unread", [1, 3], [a, b, c])
    assert out["done"] == 2 and a.read_at is None and c.read_at is None and b.read_at is not None  # b was not in the request


def test_deleting_notifications_removes_them_and_reports_the_ones_that_are_not_yours(monkeypatch, audits):
    mine = [note(1), note(2)]
    out, db = run_bulk(monkeypatch, "delete", [1, 2, 99, 1], mine)  # 99 is someone else's or unknown; 1 is repeated
    assert out["done"] == 2 and db.deleted == mine
    assert out["skipped"] == [{"id": "99", "name": None, "reason": "no encontrada"}]
    assert db.commits == 1


def test_one_audit_event_per_bulk_call_without_content(monkeypatch, audits):
    run_bulk(monkeypatch, "delete", [1, 2], [note(1), note(2)])
    assert len(audits) == 1 and audits[0][0] == "notifications.bulk"
    d = audits[0][1]["details"]
    assert d == {"action": "delete", "done": 2, "skipped": 0, "ids": [1, 2]}


def test_the_request_is_validated_before_anything_runs():
    with pytest.raises(ValidationError):
        account.NotificationBulkIn(ids=list(range(201)), action="delete")  # more than 200
    with pytest.raises(ValidationError):
        account.NotificationBulkIn(ids=[], action="delete")
    with pytest.raises(ValidationError):
        account.NotificationBulkIn(ids=[1], action="explode")
    assert len(account.NotificationBulkIn(ids=list(range(200)), action="read").ids) == 200


def test_the_list_pages_and_filters_and_the_bell_still_works_with_its_old_call(monkeypatch):
    db = Db(scalar=7, scalars=[note(5), note(4, read=True)])
    out = account.list_notifications(principal(), db, limit=30)  # exactly what the bell menu sends
    assert out["total"] == 7 and out["unread"] == 7 and [i["id"] for i in out["items"]] == [5, 4] and out["items"][1]["read"] is True
    sql = str(db.queries[0])
    assert "read_at IS NULL" not in sql and "ORDER BY" in sql
    db = Db(scalar=3, scalars=[])
    account.list_notifications(principal(), db, limit=999, offset=40, filter="unread")
    q = db.queries[0]
    assert "notifications.read_at IS NULL" in str(q)
    assert q.compile().params["param_1"] == 200 and q.compile().params["param_2"] == 40  # limit capped at 200, offset kept
    with pytest.raises(HTTPException) as e:
        account.list_notifications(principal(), Db(), filter="sideways")
    assert e.value.status_code == 422


def test_delete_read_reports_how_many_and_is_audited(monkeypatch, audits):
    db = Db(rowcount=12)
    out = account.delete_read_notifications(None, principal(), db)
    assert out == {"done": 12, "skipped": []} and db.commits == 1
    assert audits[0][0] == "notifications.delete_read" and audits[0][1]["details"] == {"done": 12}
    assert "read_at IS NOT NULL" in str(db.queries[0]) and "user_id" in str(db.queries[0])  # only the person's own, only the read ones


# ------------------------------------------------------------------ chats


def conv(owner=ME, title="chat"):
    c = Conversation(id=uuid.uuid4(), owner_id=owner, title=title)
    return c


@pytest.fixture
def chat_env(monkeypatch, audits):
    log = SimpleNamespace(forgot=[], files=[], running=set())
    monkeypatch.setattr(playground.turns, "active", lambda cid: {"status": "running"} if cid in log.running else None)
    monkeypatch.setattr(playground.turns, "forget", lambda cid, uid: log.forgot.append(cid))
    monkeypatch.setattr(playground.sandbox, "forget_conversation_files", lambda cid: log.files.append(cid))
    return log


def bulk_chats(ids, rows, **kw):
    db = Db(rows={c.id: c for c in rows}, **kw)
    out = playground.bulk_conversations(playground.ConvBulkIn(ids=ids, action="delete"), None, principal(), db)
    return out, db


def test_own_chats_are_deleted_with_the_same_cleanup_as_the_single_delete(chat_env):
    a, b = conv(title="uno"), conv(title="dos")
    out, db = bulk_chats([a.id, b.id], [a, b])
    assert out == {"done": 2, "skipped": []} and db.deleted == [a, b]
    assert chat_env.forgot == [a.id, b.id] and chat_env.files == [a.id, b.id]  # turn state and sandbox files removed for each


def test_a_chat_that_is_answering_right_now_is_left_alone(chat_env):
    busy, idle = conv(title="trabajando"), conv(title="quieto")
    chat_env.running.add(busy.id)
    out, db = bulk_chats([busy.id, idle.id], [busy, idle])
    assert out["done"] == 1 and db.deleted == [idle]
    assert out["skipped"] == [{"id": str(busy.id), "name": "trabajando", "reason": "responde ahora"}]
    assert busy.id not in chat_env.forgot and busy.id not in chat_env.files


def test_other_peoples_chats_look_exactly_like_chats_that_do_not_exist(chat_env):
    theirs, ghost = conv(owner=OTHER, title="secreto"), uuid.uuid4()
    out, db = bulk_chats([theirs.id, ghost], [theirs])
    assert out["done"] == 0 and db.deleted == []
    assert out["skipped"] == [{"id": str(theirs.id), "name": None, "reason": "no encontrado"},
                              {"id": str(ghost), "name": None, "reason": "no encontrado"}]  # no title, no hint that it exists


def test_one_chat_that_fails_never_stops_the_rest(chat_env):
    a, bad, c = conv(title="a"), conv(title="roto"), conv(title="c")
    out, db = bulk_chats([a.id, bad.id, c.id], [a, bad, c], fail_on=[bad.id])
    assert out["done"] == 2 and db.deleted == [a, c] and db.rollbacks == 1
    assert out["skipped"] == [{"id": str(bad.id), "name": "roto", "reason": "error al borrar"}]
    assert bad.id not in chat_env.files  # its files are only cleaned for the chats that were really deleted


def test_one_audit_event_per_call_with_ids_and_counts_only(chat_env, audits):
    a, theirs = conv(), conv(owner=OTHER)
    bulk_chats([a.id, theirs.id], [a, theirs])
    assert len(audits) == 1 and audits[0][0] == "playground.bulk_delete"
    d = audits[0][1]["details"]
    assert d["done"] == 1 and d["skipped"] == 1 and d["ids"] == [str(a.id), str(theirs.id)] and "title" not in str(d)


def test_chat_requests_are_validated_and_repeated_ids_count_once(chat_env):
    with pytest.raises(ValidationError):
        playground.ConvBulkIn(ids=[uuid.uuid4() for _ in range(201)], action="delete")
    with pytest.raises(ValidationError):
        playground.ConvBulkIn(ids=[], action="delete")
    with pytest.raises(ValidationError):
        playground.ConvBulkIn(ids=[uuid.uuid4()], action="archive")
    a = conv()
    out, db = bulk_chats([a.id, a.id, a.id], [a])
    assert out["done"] == 1 and db.deleted == [a]
