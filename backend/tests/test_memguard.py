"""Memory safety around big model loads (services/memguard.py and its use by the scheduler and the downloads)."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.models import ModelStatus
from app.services import memguard as G
from app.services import scheduler


def test_a_big_reservation_is_a_heavy_load():
    assert G.is_heavy(91.0, 121.7) and G.is_heavy(41.0, 500) and G.is_heavy(45.0, 121.7)
    assert not G.is_heavy(12.0, 121.7) and not G.is_heavy(35.0, 121.7)  # a 35 GB model is not (below a third and below 40 GB)


def test_the_load_needs_real_free_memory_plus_a_margin():
    ok, msg = G.headroom_verdict(110, 91, 8)
    assert ok and "110" in msg
    ok, msg = G.headroom_verdict(41, 91, 8)  # what was free in the middle of the real incident
    assert not ok and "41 GB" in msg and "99 GB" in msg and "download" in msg and "robot" in msg


def test_oom_failures_are_recognised_whatever_the_wording():
    assert G.is_oom_failure("vLLM exited during load (code 137, oom=True)")
    assert G.is_oom_failure("anything", oom_killed=True) and G.is_oom_failure("anything", exit_code=137)
    assert G.is_oom_failure("CUDA error: out of memory")
    assert not G.is_oom_failure("vLLM exited during load (code 1, oom=False)") and not G.is_oom_failure("")


def test_waiting_for_memory_ends_as_soon_as_it_is_there_or_when_time_is_up():
    free = iter([20, 30, 60, 105, 105])
    slept = []
    ok, _ = G.wait_for_headroom(91, 8, lambda: next(free), timeout_s=100, sleep=slept.append, clock=iter(range(0, 1000)).__next__)
    assert ok and len(slept) == 3
    ticks = iter(range(0, 1000, 40))
    ok, msg = G.wait_for_headroom(91, 8, lambda: 20, timeout_s=100, sleep=lambda s: None, clock=lambda: next(ticks))
    assert not ok and "20 GB" in msg


def test_downloads_wait_after_a_restart_and_during_a_heavy_load(monkeypatch):
    monkeypatch.setattr(G, "_STARTED", G.time.monotonic())
    assert "restart" in G.downloads_should_wait()
    monkeypatch.setattr(G, "_STARTED", G.time.monotonic() - 3600)
    monkeypatch.setattr(G, "heavy_load_in_progress", lambda: True)
    assert "large model" in G.downloads_should_wait()
    monkeypatch.setattr(G, "heavy_load_in_progress", lambda: False)
    assert G.downloads_should_wait() is None
    monkeypatch.setattr(G, "heavy_load_in_progress", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert G.downloads_should_wait() is None  # never block a download over a monitoring failure


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


def test_a_model_that_ran_out_of_memory_is_flagged_until_it_loads_again(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: r)
    assert not G.oom_flagged("m1")
    G.flag_oom("m1")
    assert G.oom_flagged("m1") and not G.oom_flagged("m2")
    G.clear_oom("m1")
    assert not G.oom_flagged("m1")


# ------------------------------------------------------------------ inside the scheduler


class FakeDb:
    def __init__(self, model):
        self.model = model

    def get(self, cls, key):
        return self.model

    def scalar(self, q):
        return None

    def add(self, x):
        pass

    def delete(self, x):
        pass

    def commit(self):
        pass


def _scheduler(monkeypatch, available_gb):
    model = SimpleNamespace(id="m1", alias="big", backend="vllm", status=ModelStatus.installed, runtime_config=None, last_used_at=None)
    calls = SimpleNamespace(prepared=0, finished=0, released=[], loaded=0, statuses=[])

    @contextmanager
    def session():
        yield FakeDb(model)

    monkeypatch.setattr(scheduler, "SessionLocal", session)
    monkeypatch.setattr(scheduler, "model_memory_gb", lambda db, m, p=None: 91.0)
    monkeypatch.setattr(scheduler, "mem_total_gb", lambda: 121.7)
    monkeypatch.setattr(scheduler, "mem_available_gb", lambda: available_gb)
    monkeypatch.setattr(scheduler, "try_reserve", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(scheduler, "release", lambda i: calls.released.append(i))
    monkeypatch.setattr(scheduler, "_set_status", lambda db, m, st, msg, **k: calls.statuses.append(k.get("event") or st))
    monkeypatch.setattr(scheduler, "_do_load", lambda *a, **k: calls.__setattr__("loaded", calls.loaded + 1) or "READY")
    monkeypatch.setattr(G, "prepare_heavy_load", lambda: calls.__setattr__("prepared", calls.prepared + 1) or {})
    monkeypatch.setattr(G, "finish_heavy_load", lambda: calls.__setattr__("finished", calls.finished + 1) or 0)
    monkeypatch.setattr(G, "margin_gb", lambda db: 8.0)
    real = G.wait_for_headroom
    monkeypatch.setattr(G, "wait_for_headroom", lambda need, margin, probe: real(need, margin, probe, timeout_s=0, sleep=lambda s: None))
    return calls


def test_a_heavy_load_is_refused_when_the_machine_is_not_really_free(monkeypatch):
    calls = _scheduler(monkeypatch, available_gb=41)  # the real incident: something else held the memory
    with pytest.raises(scheduler.SchedulingError) as e:
        scheduler.ensure_loaded("m1", wait_s=5)
    assert e.value.code == "insufficient_resources" and "41 GB" in e.value.message and "download" in e.value.message
    assert calls.loaded == 0 and calls.prepared == 1 and calls.finished == 1  # downloads paused, then resumed; nothing was loaded
    assert calls.released and "load.refused" in calls.statuses


def test_a_heavy_load_goes_ahead_with_room_and_still_resumes_the_downloads(monkeypatch):
    calls = _scheduler(monkeypatch, available_gb=108)
    assert scheduler.ensure_loaded("m1", wait_s=5) == "READY"
    assert calls.loaded == 1 and calls.prepared == 1 and calls.finished == 1


def test_a_small_model_is_not_slowed_down_by_any_of_this(monkeypatch):
    calls = _scheduler(monkeypatch, available_gb=5)
    monkeypatch.setattr(scheduler, "model_memory_gb", lambda db, m, p=None: 9.0)
    assert scheduler.ensure_loaded("m1", wait_s=5) == "READY"
    assert calls.prepared == 0 and calls.finished == 0


def test_the_resident_loop_leaves_a_model_that_ran_out_of_memory_alone(monkeypatch):
    model = SimpleNamespace(id="m1", alias="big", status=ModelStatus.installed)
    answers = iter([model, None])

    class Db:
        def scalar(self, q):
            return next(answers)

    @contextmanager
    def session():
        yield Db()

    monkeypatch.setattr(scheduler, "SessionLocal", session)
    monkeypatch.setattr(scheduler, "get_setting", lambda db, k: {"scheduler_mode": "hybrid", "preferred_resident_model": "big"}.get(k))
    monkeypatch.setattr(G, "oom_flagged", lambda mid: True)
    started = []
    monkeypatch.setattr(scheduler, "get_redis", lambda: started.append("redis") or FakeRedis())
    assert scheduler.ensure_resident() is None and started == []  # not even an attempt


def test_the_hint_for_a_memory_kill_is_about_memory_not_about_remote_code():
    from app.engines.vllm import _remediation

    logs = "... trust_remote_code=True ... Killed"
    assert "ran out of memory" in _remediation(logs, oom=True) and "trust_remote_code" not in _remediation(logs, oom=True)
    assert "trust_remote_code" in _remediation(logs)  # unchanged without the kill
