"""Interrupting a model load: the loader notices, the container stops, the model returns to «installed» and is not retried by itself."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.engines.base import LoadCancelled
from app.models import ModelStatus
from app.services import memguard as G
from app.services import scheduler


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def get(self, k):
        return self.kv.get(k)

    def delete(self, k):
        self.kv.pop(k, None)


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: r)
    return r


def test_the_cancel_request_and_the_do_not_retry_flag_are_separate(redis):
    assert not G.cancel_requested("m1") and not G.cancelled_flagged("m1")
    G.request_cancel("m1")
    assert G.cancel_requested("m1") and not G.cancelled_flagged("m1")
    G.finish_cancel("m1")
    assert not G.cancel_requested("m1") and G.cancelled_flagged("m1")  # over, but nobody restarts it by itself
    G.clear_oom("m1")  # a successful load clears both marks
    assert not G.cancelled_flagged("m1") and not G.oom_flagged("m1")


class Db:
    def __init__(self, model, inst):
        self.model, self.inst, self.deleted = model, inst, []

    def get(self, cls, key):
        return self.model if cls.__name__ == "RegisteredModel" else self.inst

    def scalar(self, q):
        return self.inst

    def delete(self, x):
        self.deleted.append(x)

    def commit(self):
        pass


def _setup(monkeypatch, backend="vllm", loading=True):
    model = SimpleNamespace(id="m1", alias="big", backend=backend, status=ModelStatus.loading)
    inst = SimpleNamespace(id="i1", state="loading") if loading else None
    db = Db(model, inst)
    stopped = []

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(scheduler, "SessionLocal", session)
    monkeypatch.setattr(scheduler, "get_engine", lambda b: SimpleNamespace(unload=lambda m: stopped.append(m.alias), load=None))
    return model, db, stopped


def test_cancelling_a_vllm_load_stops_the_container_and_asks_the_loader_to_stop(monkeypatch, redis):
    model, db, stopped = _setup(monkeypatch)
    out = scheduler.cancel_load("m1", actor_id="u1")
    assert out == {"cancelled": True, "alias": "big"} and stopped == ["big"] and G.cancel_requested("m1")


def test_there_is_nothing_to_cancel_when_it_is_not_loading_or_is_not_vllm(monkeypatch, redis):
    _setup(monkeypatch, loading=False)
    with pytest.raises(scheduler.SchedulingError) as e:
        scheduler.cancel_load("m1")
    assert e.value.code == "not_loading" and not G.cancel_requested("m1")
    _setup(monkeypatch, backend="ollama")
    with pytest.raises(scheduler.SchedulingError) as e:
        scheduler.cancel_load("m1")
    assert e.value.code == "not_cancellable"


def test_a_cancelled_load_is_not_a_failure(monkeypatch, redis):
    """The loader sees the request: the instance is dropped, the model is «installed» again (not «failed») and the flag is set."""
    model, db, stopped = _setup(monkeypatch)
    statuses, released = [], []
    monkeypatch.setattr(scheduler, "_set_status", lambda d, m, st, msg, **k: statuses.append((st, k.get("event"))))
    monkeypatch.setattr(scheduler, "release", lambda i: released.append(i))
    monkeypatch.setattr(scheduler, "mem_available_gb", lambda: 50.0)
    G.request_cancel("m1")
    with pytest.raises(LoadCancelled):
        scheduler._do_load("m1", "i1", {}, "u1")
    assert statuses == [(ModelStatus.installed, "load.cancelled")] and released == ["i1"] and db.deleted
    assert not G.cancel_requested("m1") and G.cancelled_flagged("m1") and not G.oom_flagged("m1")
    assert stopped == ["big"]  # no half-started container is left holding memory


def test_the_resident_loop_does_not_restart_a_load_a_person_stopped(monkeypatch, redis):
    model = SimpleNamespace(id="m1", alias="big", status=ModelStatus.installed)

    class D:
        def execute(self, q):
            return []

    @contextmanager
    def session():
        yield D()

    monkeypatch.setattr(scheduler, "SessionLocal", session)
    monkeypatch.setattr(scheduler, "resident_targets", lambda db: [model])
    monkeypatch.setattr(G, "oom_flagged", lambda mid: False)
    G.finish_cancel("m1")
    assert scheduler.ensure_resident() is None  # a person stopped it: it waits for a person


def test_the_endpoint_refuses_the_general_model_to_non_managers_and_reports_nothing_to_cancel(monkeypatch):
    from fastapi import HTTPException

    from app.routers import models as R

    m = SimpleNamespace(id="m1", pinned=True)
    monkeypatch.setattr(R, "_get_visible", lambda db, p, mid: m)
    with pytest.raises(HTTPException) as e:
        R.cancel_model_load("m1", None, SimpleNamespace(is_manager=False), None)
    assert e.value.status_code == 403
    m.pinned = False

    def boom(mid, actor_id=None):
        raise scheduler.SchedulingError("not_loading", "not loading")

    monkeypatch.setattr(R.scheduler, "cancel_load", boom)
    with pytest.raises(HTTPException) as e:
        R.cancel_model_load("m1", None, SimpleNamespace(is_manager=True, id="u1"), None)
    assert e.value.status_code == 409 and e.value.detail["code"] == "not_loading"
