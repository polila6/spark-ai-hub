"""Bulk actions on jobs, models and files: the same checks as acting on one, one answer for «missing» and «not yours»,
and one bad item never stops the rest."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import JobStatus
from app.routers import files as F
from app.routers import jobs as J
from app.routers import models as M


def uid():
    return uuid.uuid4()


ME, OTHER = uid(), uid()
user = lambda admin=False, uid_=ME: SimpleNamespace(id=uid_, is_admin=admin, user=SimpleNamespace(id=uid_))  # noqa: E731
REQ = SimpleNamespace()


class Db:
    """Just enough database: objects by id, a record of deletions and commits."""

    def __init__(self, objs=()):
        self.objs = {o.id: o for o in objs}
        self.deleted, self.commits, self.rollbacks, self.executed = [], 0, 0, 0

    def get(self, cls, key):
        return self.objs.get(key)

    def delete(self, o):
        self.deleted.append(o)

    def execute(self, q):
        self.executed += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def expire_all(self):
        pass


@pytest.fixture
def audits(monkeypatch):
    seen = []
    for mod in (J, M, F):
        monkeypatch.setattr(mod, "audit", lambda db, action, **k: seen.append((action, k.get("details"))))
    return seen


# ------------------------------------------------------------------ the request limits


@pytest.mark.parametrize("model,extra", [(J.BulkIn, {"action": "cancel"}), (M.BulkIn, {"action": "delete"}), (F.BulkIn, {"action": "delete"})])
def test_a_bulk_request_takes_between_one_and_200_ids(model, extra):
    model(ids=[uid() for _ in range(200)], **extra)
    with pytest.raises(ValidationError):
        model(ids=[uid() for _ in range(201)], **extra)
    with pytest.raises(ValidationError):
        model(ids=[], **extra)
    with pytest.raises(ValidationError):
        model(ids=[uid()], action="explode")


# ------------------------------------------------------------------ jobs


def job(status, owner=ME, title="trabajo"):
    return SimpleNamespace(id=uid(), status=status, owner_id=owner, title=title)


def test_cancelling_jobs_skips_finished_and_foreign_ones_and_audits_once(monkeypatch, audits):
    running, queued, done, foreign = job(JobStatus.running), job(JobStatus.queued), job(JobStatus.completed, title="viejo"), job(JobStatus.running, OTHER)
    db = Db([running, queued, done, foreign])
    cancelled = []
    monkeypatch.setattr(J, "request_cancel", lambda d, j: cancelled.append(j.id))
    r = J.bulk_jobs(J.BulkIn(ids=[running.id, queued.id, done.id, foreign.id, uid(), running.id], action="cancel"), REQ, user(), db)
    assert r["done"] == 2 and cancelled == [running.id, queued.id]  # the duplicate id counts once
    by = {s["id"]: s for s in r["skipped"]}
    assert by[str(done.id)]["reason"] == "ya había terminado" and by[str(done.id)]["name"] == "viejo"
    missing = next(s for s in r["skipped"] if s["id"] not in (str(done.id), str(foreign.id)))
    assert by[str(foreign.id)]["reason"] == missing["reason"] == J.NOT_YOURS and by[str(foreign.id)]["name"] is None  # no hint of what exists
    assert len(audits) == 1 and audits[0][0] == "jobs.bulk_cancel" and audits[0][1]["done"] == 2 and audits[0][1]["skipped"] == 3


def test_an_administrator_may_cancel_other_peoples_jobs(monkeypatch, audits):
    foreign = job(JobStatus.running, OTHER)
    cancelled = []
    monkeypatch.setattr(J, "request_cancel", lambda d, j: cancelled.append(j.id))
    assert J.bulk_jobs(J.BulkIn(ids=[foreign.id], action="cancel"), REQ, user(admin=True), Db([foreign]))["done"] == 1 and cancelled == [foreign.id]


def test_only_finished_jobs_can_be_deleted_and_their_scratch_folder_goes_too(monkeypatch, audits, tmp_path):
    fin = [job(s) for s in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled, JobStatus.interrupted)]
    live = [job(s) for s in (JobStatus.queued, JobStatus.preparing, JobStatus.running)]
    foreign = job(JobStatus.completed, OTHER)
    db = Db(fin + live + [foreign])
    removed = []
    monkeypatch.setattr("shutil.rmtree", lambda path, ignore_errors=False: removed.append(path.name))
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(jobs_dir=tmp_path))
    r = J.bulk_jobs(J.BulkIn(ids=[x.id for x in fin + live + [foreign]], action="delete"), REQ, user(), db)
    assert r["done"] == 4 and sorted(str(x.id) for x in db.deleted) == sorted(str(x.id) for x in fin) and db.executed == 4  # events deleted for each
    assert sorted(removed) == sorted(str(x.id) for x in fin)
    assert {s["reason"] for s in r["skipped"]} == {"sigue en marcha: cancélalo antes", J.NOT_YOURS}
    assert audits[0][0] == "jobs.bulk_delete" and len(audits[0][1]["ids"]) == 4


# ------------------------------------------------------------------ models


def model(alias, owner=ME, pinned=False):
    return SimpleNamespace(id=uid(), alias=alias, owner_id=owner, pinned=pinned)


@pytest.fixture
def models_env(monkeypatch, audits):
    env = SimpleNamespace(deps={}, unloaded=[], files=[], settings={"preferred_resident_model": "general"}, loaded=set(), fail_files=set(), hidden=set())
    all_models = {}

    def visible(db, p, mid):
        m = all_models.get(mid)
        if m is None or mid in env.hidden:
            raise HTTPException(404, detail={"code": "not_found", "message": "model not found"})
        return m

    monkeypatch.setattr(M, "_get_visible", visible)
    monkeypatch.setattr(M, "get_setting", lambda db, k: env.settings.get(k))
    monkeypatch.setattr(M.registry, "dependencies", lambda db, m: {"active_jobs": env.deps.get(m.alias, []), "loaded": m.alias in env.loaded})
    monkeypatch.setattr(M.registry, "mark", lambda *a, **k: None)

    def delete_files(db, m):
        if m.alias in env.fail_files:
            raise OSError("disk error")
        env.files.append(m.alias)
        return {"removed": [m.alias]}

    monkeypatch.setattr(M.registry, "delete_files", delete_files)
    monkeypatch.setattr(M.scheduler, "unload_model", lambda mid, actor_id=None: env.unloaded.append(mid))
    monkeypatch.setattr(M, "SharedResource", SimpleNamespace(resource_type=None, resource_id=None))
    env.add = lambda *ms: [all_models.__setitem__(m.id, m) for m in ms] and None
    return env


class ModelDb(Db):
    def query(self, *a, **k):
        return SimpleNamespace(filter=lambda *a, **k: SimpleNamespace(delete=lambda: 0))

    def get(self, cls, key):
        return self.objs.get(key)


def run_models(env, ms, p=None, **kw):
    db = ModelDb(ms)
    env.add(*ms)
    return M.bulk_models(M.BulkIn(ids=[m.id for m in ms], action="delete", **kw), REQ, p or user(), db), db


def test_models_are_deleted_with_their_files_by_default(models_env):
    a, b = model("a"), model("b")
    r, db = run_models(models_env, [a, b])
    assert r == {"done": 2, "skipped": []} and models_env.files == ["a", "b"] and db.deleted == [a, b]


def test_the_disk_files_can_be_kept(models_env):
    a = model("a")
    r, db = run_models(models_env, [a], delete_files=False)
    assert r["done"] == 1 and models_env.files == [] and db.deleted == [a]


def test_pinned_and_general_models_are_skipped_but_the_rest_are_deleted(models_env):
    pinned, general, plain = model("harness", pinned=True), model("general"), model("plain")
    r, db = run_models(models_env, [pinned, general, plain])
    assert r["done"] == 1 and db.deleted == [plain]
    assert {s["name"] for s in r["skipped"]} == {"harness", "general"} and all("quítale la fijación" in s["reason"] for s in r["skipped"])


def test_someone_elses_or_hidden_models_get_the_same_answer_and_no_name(models_env):
    mine, foreign, hidden = model("mine"), model("secret", OTHER), model("hidden", OTHER)
    models_env.hidden.add(hidden.id)
    r, db = run_models(models_env, [mine, foreign, hidden])
    assert r["done"] == 1 and db.deleted == [mine]
    assert [s["reason"] for s in r["skipped"]] == [M.NOT_ALLOWED, M.NOT_ALLOWED] and all(s["name"] is None for s in r["skipped"])
    assert run_models(models_env, [model("x", OTHER)], p=user(admin=True))[0]["done"] == 1  # an administrator may


def test_a_model_used_by_active_jobs_or_failing_to_delete_does_not_stop_the_rest(models_env):
    busy, broken, fine = model("busy"), model("broken"), model("fine")
    models_env.deps["busy"] = ["job-1"]
    models_env.fail_files.add("broken")
    r, db = run_models(models_env, [busy, broken, fine])
    assert r["done"] == 1 and db.deleted == [fine]
    reasons = {s["name"]: s["reason"] for s in r["skipped"]}
    assert reasons["busy"] == "lo usan trabajos activos" and "disk error" in reasons["broken"]


def test_a_loaded_model_is_unloaded_first_and_one_that_cannot_be_unloaded_is_skipped(models_env, monkeypatch):
    loaded, stuck = model("loaded"), model("stuck")
    models_env.loaded |= {"loaded", "stuck"}

    def unload(mid, actor_id=None):
        if mid == stuck.id:
            raise M.scheduler.SchedulingError("busy", "tiene peticiones en curso")
        models_env.unloaded.append(mid)

    monkeypatch.setattr(M.scheduler, "unload_model", unload)
    r, db = run_models(models_env, [loaded, stuck])
    assert models_env.unloaded == [loaded.id] and r["done"] == 1 and r["skipped"][0]["reason"] == "tiene peticiones en curso"


def test_the_single_delete_still_works_through_the_shared_helper_and_audits(models_env, audits):
    m = model("solo")
    models_env.add(m)
    out = M.delete_model(m.id, REQ, user(), ModelDb([m]), True)
    assert out["ok"] and audits[-1][0] == "models.delete" and audits[-1][1]["alias"] == "solo"
    with pytest.raises(HTTPException) as e:
        M.delete_model(model("zz").id, REQ, user(), ModelDb([]), True)
    assert e.value.status_code == 404


def test_one_audit_event_per_bulk_call_with_aliases_only(models_env, audits):
    a, b = model("a"), model("b", pinned=True)
    run_models(models_env, [a, b])
    assert [x[0] for x in audits] == ["models.bulk_delete"] and audits[0][1]["aliases"] == ["a"] and audits[0][1]["skipped"] == 1


# ------------------------------------------------------------------ files


def test_files_are_deleted_one_by_one_and_foreign_or_broken_ones_are_skipped(monkeypatch, audits):
    mine = [SimpleNamespace(id=uid(), filename=f"f{i}.pdf", owner_id=ME) for i in range(3)]
    foreign = SimpleNamespace(id=uid(), filename="ajeno.pdf", owner_id=OTHER)
    db = Db(mine + [foreign])

    def own(db_, p, fid):
        f = db_.objs.get(fid)
        if not f or f.owner_id != p.id:
            raise HTTPException(404, detail={"code": "not_found", "message": "file"})
        return f

    removed = []

    def remove(db_, f):
        if f.filename == "f1.pdf":
            raise OSError("busy")
        removed.append(f.filename)

    monkeypatch.setattr(F, "own_file", own)
    monkeypatch.setattr(F, "_remove_file", remove)
    r = F.bulk_files(F.BulkIn(ids=[x.id for x in mine] + [foreign.id, uid()], action="delete"), REQ, user(), db)
    assert r["done"] == 2 and removed == ["f0.pdf", "f2.pdf"] and db.rollbacks == 1
    reasons = [s["reason"] for s in r["skipped"]]
    assert reasons.count("No encontrado o sin permiso") == 2 and any("OSError" in x for x in reasons)
    assert all(s["name"] is None for s in r["skipped"] if s["reason"] == "No encontrado o sin permiso")
    assert audits[0][0] == "files.bulk_delete" and audits[0][1]["done"] == 2


def test_the_single_file_delete_uses_the_same_helper(monkeypatch):
    f = SimpleNamespace(id=uid(), filename="a.txt", owner_id=ME)
    got = []
    monkeypatch.setattr(F, "own_file", lambda db, p, fid: f)
    monkeypatch.setattr(F, "_remove_file", lambda db, ff: got.append(ff))
    assert F.delete_file(f.id, user(), Db()) == {"ok": True} and got == [f]


def test_bulk_routes_are_declared_before_the_id_routes():
    for router, bulk in ((J.router, "/api/v1/jobs/bulk"), (M.router, "/api/v1/models/bulk"), (F.router, "/api/v1/files/bulk")):
        paths = [(r.path, tuple(sorted(r.methods))) for r in router.routes if hasattr(r, "methods")]
        i = paths.index((bulk, ("POST",)))
        for path, methods in paths[:i]:
            assert not (methods == ("POST",) and path.count("/") == bulk.count("/") and path.endswith("}")), (bulk, path)
