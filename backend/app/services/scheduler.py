"""Conservative resource scheduler for the DGX Spark's unified memory.

Principles
* Every resident model and every running heavy job holds a *logical reservation* (GB of unified
  memory). Reservations are stored in Postgres and decided under an advisory lock, so the API
  process and worker processes never race.
* A reservation is granted only if (a) the sum of reservations stays within the schedulable budget
  (MemTotal − system reserve) and (b) the *live* MemAvailable confirms there is room — this
  protects against workloads outside the hub. Unknown estimates are treated pessimistically.
* When room is needed, idle, non-pinned models are unloaded (hybrid / on-demand modes). Busy
  models are never evicted and unrelated host processes are never killed.
* GPU-heavy jobs (video, large diffusion) are serialised via max_concurrent_gpu_jobs.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal, advisory_lock
from ..engines import EngineError, get_engine
from ..engines.base import LoadCancelled
from ..logging_setup import log
from ..models import (
    ModelInstallation,
    ModelStatus,
    RegisteredModel,
    ResourceReservation,
    RuntimeInstance,
)
from ..redis_client import get_redis
from ..settings_store import get_setting
from . import memguard, telemetry

GB = 1e9
LIVE_MARGIN_GB = 3.0
ENGINE_BASELINE_GB = {"comfyui": 3.0, "mlworker": 3.0}


class SchedulingError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.code, self.message, self.retry_after = code, message, retry_after


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- budget


def mem_total_gb() -> float:
    m = telemetry.meminfo()
    return (m.get("MemTotal") or 0) / GB


def mem_available_gb() -> float:
    m = telemetry.meminfo()
    return (m.get("MemAvailable") or 0) / GB


def budget(db: Session) -> dict:
    total = mem_total_gb()
    reserve = float(get_setting(db, "system_reserve_gb") or 14)
    reserved = db.scalar(select(func.coalesce(func.sum(ResourceReservation.memory_gb), 0.0))
                         .where(ResourceReservation.released_at.is_(None))) or 0.0
    heavy = db.scalar(select(func.count()).select_from(ResourceReservation).where(
        ResourceReservation.released_at.is_(None), ResourceReservation.gpu_heavy.is_(True))) or 0
    schedulable = max(0.0, total - reserve)
    return {
        "total_gb": round(total, 2),
        "system_reserve_gb": reserve,
        "schedulable_gb": round(schedulable, 2),
        "reserved_gb": round(float(reserved), 2),
        "free_logical_gb": round(schedulable - float(reserved), 2),
        "mem_available_gb": round(mem_available_gb(), 2),
        "gpu_heavy_active": int(heavy),
        "max_concurrent_gpu_jobs": int(get_setting(db, "max_concurrent_gpu_jobs") or 1),
        "mode": get_setting(db, "scheduler_mode"),
    }


def model_memory_gb(db: Session, model: RegisteredModel, params: dict | None = None) -> float:
    """Reservation size for a model. vLLM pre-allocates gpu_memory_utilization × total memory."""
    params = params if params is not None else ((model.runtime_config.params if model.runtime_config else {}) or {})
    if model.backend == "vllm":
        util = float(params.get("gpu_memory_utilization") or 0.3)
        return round(util * mem_total_gb() + 1.0, 2)
    est = model.measured_memory_gb or model.estimated_memory_gb
    if est is None and model.size_bytes:
        est = model.size_bytes / GB * 1.2 + 2.0
    if est is None:
        # Pessimistic default for unknown models.
        est = 0.5 * float(budget(db)["schedulable_gb"])
    return round(float(est), 2)


# --------------------------------------------------------------------------- active request counters (Redis)


def _active_key(instance_id) -> str:
    return f"sah:active:{instance_id}"


def active_requests(instance_id) -> int:
    try:
        return int(get_redis().get(_active_key(instance_id)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def begin_request(instance_id) -> None:
    r = get_redis()
    k = _active_key(instance_id)
    r.incr(k)
    r.expire(k, 3600)


def end_request(instance_id) -> None:
    r = get_redis()
    k = _active_key(instance_id)
    if int(r.decr(k)) < 0:
        r.set(k, 0)


# --------------------------------------------------------------------------- reservations


def _is_protected(db: Session, inst: RuntimeInstance) -> bool:
    mode = get_setting(db, "scheduler_mode")
    if mode == "persistent":
        return True
    if inst.model is not None:
        pref = get_setting(db, "preferred_resident_model")
        if inst.model.pinned or (pref and inst.model.alias == pref and mode == "hybrid"):
            return True
    return False


def _engine_in_use(db: Session, engine: str) -> bool:
    from ..models import Job, JobStatus  # local import to avoid cycles

    types = {"comfyui": ("image_generate", "video_generate"),
             "mlworker": ("audio_transcribe", "audio_tts", "sci_infer")}.get(engine, ())
    return bool(db.scalar(select(func.count()).select_from(Job).where(
        Job.type.in_(types), Job.status.in_([JobStatus.preparing, JobStatus.running]))))


def eviction_candidates(db: Session, exclude: set | None = None) -> list[RuntimeInstance]:
    exclude = exclude or set()
    rows = db.scalars(select(RuntimeInstance).where(RuntimeInstance.state == "ready")
                      .order_by(RuntimeInstance.last_used_at)).all()
    out = []
    for inst in rows:
        if inst.id in exclude or _is_protected(db, inst) or active_requests(inst.id) > 0:
            continue
        if inst.model_id is None and _engine_in_use(db, inst.engine):
            continue
        out.append(inst)
    return out


def _grant(db: Session, holder_type: str, holder_id, memory_gb: float, gpu_heavy: bool, sched_class: str,
           note: str | None) -> tuple[bool, str]:
    b = budget(db)
    if gpu_heavy and b["gpu_heavy_active"] >= b["max_concurrent_gpu_jobs"]:
        return False, f"GPU-heavy slot busy ({b['gpu_heavy_active']}/{b['max_concurrent_gpu_jobs']})"
    if memory_gb > b["schedulable_gb"]:
        return False, (f"Requires {memory_gb:.1f} GB, more than the whole schedulable budget "
                       f"({b['schedulable_gb']:.1f} GB)")
    if memory_gb > b["free_logical_gb"]:
        return False, f"Requires {memory_gb:.1f} GB; {b['free_logical_gb']:.1f} GB unreserved"
    # Live confirmation: memory actually available (catches workloads outside the hub).
    if memory_gb > 0.5 and b["mem_available_gb"] < memory_gb + LIVE_MARGIN_GB:
        return False, (f"Host reports only {b['mem_available_gb']:.1f} GB available "
                       f"(need {memory_gb:.1f} + {LIVE_MARGIN_GB} GB margin); memory may be held outside the hub")
    db.add(ResourceReservation(holder_type=holder_type, holder_id=holder_id, memory_gb=memory_gb,
                               gpu_heavy=gpu_heavy, sched_class=sched_class, note=note))
    db.flush()
    return True, "granted"


def try_reserve(holder_type: str, holder_id, memory_gb: float, *, gpu_heavy: bool = False,
                sched_class: str = "batch", note: str | None = None, allow_evict: bool = True,
                exclude_instances: set | None = None) -> tuple[bool, str]:
    """Attempt a reservation, unloading idle models if allowed. Commits on success."""
    evicted: list[str] = []
    for _attempt in range(6):
        with SessionLocal() as db:
            with advisory_lock(db):
                existing = db.scalar(select(ResourceReservation).where(
                    ResourceReservation.holder_id == holder_id, ResourceReservation.released_at.is_(None)))
                if existing:
                    db.commit()
                    return True, "already reserved"
                ok, reason = _grant(db, holder_type, holder_id, memory_gb, gpu_heavy, sched_class, note)
                if ok:
                    db.commit()
                    if evicted:
                        log.info("scheduler.evicted_for", holder=str(holder_id), evicted=evicted)
                    return True, reason
                cands = eviction_candidates(db, exclude_instances) if allow_evict else []
                if not cands or (gpu_heavy and "GPU-heavy" in reason):
                    db.rollback()
                    return False, reason
                victim = cands[0]
                victim.state = "unloading"
                vid = victim.id
                db.commit()
        unload_instance(vid, reason=f"evicted to make room for {note or holder_id}")
        evicted.append(str(vid))
    return False, "could not free enough memory"


def release(holder_id) -> None:
    with SessionLocal() as db:
        for r in db.scalars(select(ResourceReservation).where(
                ResourceReservation.holder_id == holder_id, ResourceReservation.released_at.is_(None))):
            r.released_at = utcnow()
        db.commit()


# --------------------------------------------------------------------------- model lifecycle


def _history(db: Session, model: RegisteredModel, event: str, frm, to, details=None, actor_id=None):
    db.add(ModelInstallation(model_id=model.id, event=event, from_status=str(frm) if frm else None,
                             to_status=str(to) if to else None, details=details or {}, actor_id=actor_id))


def _set_status(db: Session, model: RegisteredModel, status: ModelStatus, msg: str | None = None, event=None,
                details=None, actor_id=None):
    old = model.status.value if model.status else None
    model.status = status
    model.status_message = msg
    _history(db, model, event or f"status.{status.value}", old, status.value, details, actor_id)


def ensure_loaded(model_id: uuid.UUID, *, wait_s: float = 1800, actor_id=None, params_override: dict | None = None) -> RuntimeInstance:
    """Return a ready runtime instance for the model, loading it if necessary.

    Concurrent callers for the same model wait for the first loader. Raises SchedulingError
    when resources cannot be obtained and EngineError when the engine fails to load.
    """
    deadline = time.time() + wait_s
    while True:
        with SessionLocal() as db:
            model = db.get(RegisteredModel, model_id)
            if model is None:
                raise SchedulingError("not_found", "Model not found")
            if model.status in (ModelStatus.disabled, ModelStatus.removing, ModelStatus.downloading,
                                ModelStatus.discovered, ModelStatus.installing):
                raise SchedulingError("not_installed", f"Model is {model.status.value}; it cannot be loaded")
            inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == model_id,
                                                           RuntimeInstance.state.in_(["ready", "loading", "busy"])))
            if inst and inst.state in ("ready", "busy"):
                inst.last_used_at = utcnow()
                model.last_used_at = utcnow()
                db.commit()
                return inst
            if inst and inst.state == "loading":
                if time.time() > deadline:
                    raise SchedulingError("load_timeout", "Timed out waiting for model to load", retry_after=30)
                time.sleep(2)
                continue
            # Each vLLM model runs in its own container, so several can be resident. Start-ups are serialised:
            # vLLM profiles free memory while starting and two simultaneous start-ups would misjudge it.
            if model.backend == "vllm":
                other = db.scalar(select(RuntimeInstance).join(RegisteredModel, RuntimeInstance.model_id == RegisteredModel.id)
                                  .where(RegisteredModel.backend == "vllm", RuntimeInstance.model_id != model_id,
                                         RuntimeInstance.state == "loading"))
                if other:
                    if time.time() > deadline:
                        raise SchedulingError("engine_busy", "Another vLLM model is starting; try again shortly",
                                              retry_after=60)
                    time.sleep(3)
                    continue
            params = dict((model.runtime_config.params if model.runtime_config else {}) or {})
            params.update(params_override or {})
            mem = model_memory_gb(db, model, params)
            inst = RuntimeInstance(model_id=model.id, engine=model.backend, state="loading", reserved_memory_gb=mem)
            db.add(inst)
            prev_status = model.status
            _set_status(db, model, ModelStatus.loading, None, actor_id=actor_id)
            db.commit()
            inst_id = inst.id
        # A big vLLM load peaks far above what the model holds afterwards, and on this machine the GPU memory is the
        # system memory: nothing else may be eating into it meanwhile (see services/memguard.py).
        heavy = model.backend == "vllm" and memguard.is_heavy(mem, mem_total_gb())
        if heavy:
            memguard.prepare_heavy_load()  # pause downloads, let the robot server go
        try:
            ok, reason = try_reserve("runtime", inst_id, mem, sched_class="interactive",
                                     note=f"model {model.alias}", exclude_instances={inst_id})
            if ok and heavy:
                with SessionLocal() as db:
                    margin = memguard.margin_gb(db)
                enough, why = memguard.wait_for_headroom(mem, margin, mem_available_gb)
                if not enough:
                    release(inst_id)
                    ok, reason = False, f"not enough free memory: {why}"
            if not ok:
                with SessionLocal() as db:
                    db.delete(db.get(RuntimeInstance, inst_id))
                    m = db.get(RegisteredModel, model_id)
                    _set_status(db, m, prev_status if prev_status != ModelStatus.loading else ModelStatus.installed,
                                f"Not loaded: {reason}", event="load.refused", details={"reason": reason})
                    db.commit()
                raise SchedulingError("insufficient_resources", f"Cannot load {model.alias}: {reason}", retry_after=60)
            return _do_load(model_id, inst_id, params, actor_id)
        finally:
            if heavy:
                memguard.finish_heavy_load()  # downloads that were paused go on


def cancel_load(model_id: uuid.UUID, actor_id=None) -> dict:
    """Stop a model load that is in progress. Only big vLLM loads take long enough to need it: the container is stopped at once
    (its memory comes back), the model returns to «installed», and it is not loaded again until a person loads it."""
    with SessionLocal() as db:
        model = db.get(RegisteredModel, model_id)
        inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == model_id, RuntimeInstance.state == "loading"))
        if model is None or inst is None:
            raise SchedulingError("not_loading", "This model is not being loaded right now")
        if model.backend != "vllm":
            raise SchedulingError("not_cancellable", "Only vLLM loads can be cancelled (the others take a few seconds)")
        alias, backend = model.alias, model.backend
    memguard.request_cancel(model_id)
    try:
        get_engine(backend).unload(model)  # stop the container now; the loader notices the request and cleans up
    except Exception as e:  # noqa: BLE001 — the loader also stops it within five seconds
        log.warning("scheduler.cancel_load_stop_failed", model=alias, error=str(e)[:200])
    log.info("scheduler.load_cancel_requested", model=alias, by=str(actor_id))
    return {"cancelled": True, "alias": alias}


def _note_oom(model_id, alias: str, msg: str) -> None:
    """A load that died for lack of memory: stop automatic retries and tell the administrators once."""
    try:
        memguard.flag_oom(model_id)
        from . import notify

        with SessionLocal() as db:
            notify.notify_managers(db, "model_memory", f"{alias} se quedó sin memoria al cargar",
                                   "No se volverá a cargar sola. Cuando no haya descargas ni otros modelos pesados, cárgala a mano "
                                   "desde la Biblioteca. " + msg[:200], link=f"/models/{model_id}", email=False)
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler.oom_note_failed", error=str(e))


def _do_load(model_id, inst_id, params: dict, actor_id) -> RuntimeInstance:
    with SessionLocal() as db:
        model = db.get(RegisteredModel, model_id)
        engine = get_engine(model.backend)
        before = mem_available_gb()
        try:
            if memguard.cancel_requested(model_id):  # stopped while it was waiting for memory or for another load
                raise LoadCancelled()
            res = engine.load(model, params)
        except LoadCancelled as e:
            # Not a failure: the model goes back to «installed», nothing is retried automatically, the memory is released
            inst = db.get(RuntimeInstance, inst_id)
            if inst is not None:
                db.delete(inst)
            _set_status(db, model, ModelStatus.installed, "Load cancelled", event="load.cancelled", actor_id=actor_id)
            db.commit()
            release(inst_id)
            try:
                engine.unload(model)  # make sure no half-started container is left holding memory
            except Exception:  # noqa: BLE001
                pass
            memguard.finish_cancel(model_id)
            raise
        except Exception as e:  # noqa: BLE001
            msg = getattr(e, "message", str(e))
            rem = getattr(e, "remediation", None)
            inst = db.get(RuntimeInstance, inst_id)
            inst.state = "failed"
            inst.error = msg
            _set_status(db, model, ModelStatus.failed, msg, event="load.failed",
                        details={"error": msg, "remediation": (rem or "")[:4000]}, actor_id=actor_id)
            db.commit()
            release(inst_id)
            with SessionLocal() as db2:
                db2.delete(db2.get(RuntimeInstance, inst_id))
                db2.commit()
            if memguard.is_oom_failure(msg):
                # Do not let the resident-model loop try again by itself: it would repeat the crisis every ten minutes.
                _note_oom(model_id, model.alias, msg)
            if isinstance(e, EngineError):
                raise
            raise EngineError(msg) from e
        memguard.clear_oom(model_id)
        after = mem_available_gb()
        delta = max(0.0, before - after)
        inst = db.get(RuntimeInstance, inst_id)
        inst.state = "ready"
        inst.load_seconds = round(res.seconds, 2)
        inst.measured_memory_gb = round(res.measured_memory_gb or delta, 2) if (res.measured_memory_gb or delta) else None
        inst.last_used_at = utcnow()
        if model.backend != "vllm" and inst.measured_memory_gb:
            model.measured_memory_gb = inst.measured_memory_gb
            # Track the measurement (+10 %) in both directions: a stale, smaller estimate (e.g. after a context
            # length increase) must not leave the reservation below what the model really holds.
            measured = round(inst.measured_memory_gb * 1.1 + 0.5, 2)
            r = db.scalar(select(ResourceReservation).where(ResourceReservation.holder_id == inst_id,
                                                            ResourceReservation.released_at.is_(None)))
            if r and measured != r.memory_gb:
                r.memory_gb = measured
                inst.reserved_memory_gb = measured
        model.last_used_at = utcnow()
        _set_status(db, model, ModelStatus.ready, None, event="load.ok",
                    details={"seconds": inst.load_seconds, "measured_memory_gb": inst.measured_memory_gb,
                             "memory_basis": res.detail or "MemAvailable delta"}, actor_id=actor_id)
        db.commit()
        log.info("scheduler.loaded", model=model.alias, seconds=inst.load_seconds, mem=inst.measured_memory_gb)
        return inst


def unload_instance(instance_id, reason: str = "", actor_id=None) -> None:
    with SessionLocal() as db:
        inst = db.get(RuntimeInstance, instance_id)
        if inst is None:
            return
        model = db.get(RegisteredModel, inst.model_id) if inst.model_id else None
        inst.state = "unloading"
        if model:
            _set_status(db, model, ModelStatus.unloading, reason, actor_id=actor_id)
        db.commit()
        engine = get_engine(inst.engine)
        try:
            if model:
                engine.unload(model)
            else:
                engine.stop()  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler.unload_error", instance=str(instance_id), error=str(e))
        if model:
            _set_status(db, model, ModelStatus.installed, None, event="unload.ok", details={"reason": reason},
                        actor_id=actor_id)
        db.delete(inst)
        db.commit()
    release(instance_id)
    try:
        get_redis().delete(_active_key(instance_id))
    except Exception:  # noqa: BLE001
        pass
    log.info("scheduler.unloaded", instance=str(instance_id), reason=reason)


def unload_model(model_id: uuid.UUID, actor_id=None, force: bool = False) -> bool:
    with SessionLocal() as db:
        inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == model_id))
        if not inst:
            return False
        if not force and active_requests(inst.id) > 0:
            raise SchedulingError("busy", "Model is serving active requests; retry when idle")
        iid = inst.id
    unload_instance(iid, reason="manual unload", actor_id=actor_id)
    return True


# --------------------------------------------------------------------------- engine-level containers


def ensure_engine(engine: str, wait_s: float = 900) -> RuntimeInstance:
    """Start an on-demand engine container (ComfyUI, ML worker) with a baseline reservation."""
    deadline = time.time() + wait_s
    while True:
        with SessionLocal() as db:
            inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.engine == engine,
                                                           RuntimeInstance.model_id.is_(None)))
            if inst and inst.state == "ready":
                inst.last_used_at = utcnow()
                db.commit()
                if get_engine(engine).is_running():  # type: ignore[attr-defined]
                    return inst
                db.delete(inst)
                db.commit()
                release(inst.id)
                continue
            if inst and inst.state in ("loading", "unloading"):
                if time.time() > deadline:
                    raise SchedulingError("load_timeout", f"Timed out waiting for {engine}")
                time.sleep(2)
                continue
            inst = RuntimeInstance(engine=engine, state="loading", reserved_memory_gb=ENGINE_BASELINE_GB[engine])
            db.add(inst)
            db.commit()
            iid = inst.id
        ok, reason = try_reserve("runtime", iid, ENGINE_BASELINE_GB[engine], sched_class="batch",
                                 note=f"engine {engine}", exclude_instances={iid})
        if not ok:
            with SessionLocal() as db:
                db.delete(db.get(RuntimeInstance, iid))
                db.commit()
            raise SchedulingError("insufficient_resources", f"Cannot start {engine}: {reason}", retry_after=60)
        try:
            res = get_engine(engine).start()  # type: ignore[attr-defined]
        except Exception:
            release(iid)
            with SessionLocal() as db:
                db.delete(db.get(RuntimeInstance, iid))
                db.commit()
            raise
        with SessionLocal() as db:
            inst = db.get(RuntimeInstance, iid)
            inst.state = "ready"
            inst.load_seconds = round(res.seconds, 2)
            inst.last_used_at = utcnow()
            db.commit()
            return inst


def touch_engine(engine: str) -> None:
    with SessionLocal() as db:
        inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.engine == engine,
                                                       RuntimeInstance.model_id.is_(None)))
        if inst:
            inst.last_used_at = utcnow()
            db.commit()


# --------------------------------------------------------------------------- maintenance


def sweep_idle() -> list[str]:
    """Unload models/engines idle longer than the configured timeout (not in persistent mode)."""
    unloaded = []
    with SessionLocal() as db:
        mode = get_setting(db, "scheduler_mode")
        if mode == "persistent":
            return []
        timeout_min = float(get_setting(db, "on_demand_idle_timeout_min" if mode == "on_demand" else "model_idle_timeout_min"))
        cutoff = utcnow() - timedelta(minutes=timeout_min)
        cands = [i for i in eviction_candidates(db) if i.last_used_at < cutoff]
        ids = [i.id for i in cands]
    for iid in ids:
        unload_instance(iid, reason=f"idle > {timeout_min:g} min ({mode})")
        unloaded.append(str(iid))
    return unloaded


def sync_ollama() -> dict:
    """Periodic, Ollama-only reconciliation. Ollama loads/unloads models on its own (requests, keep_alive, a context
    change), so reservations follow the size it reports in /api/ps and instances it no longer holds are dropped.
    Transitional instances are left alone; vLLM is only reconciled at worker start (a busy server can miss a probe)."""
    from ..engines import get_engine as ge

    try:
        sizes = {m.get("name"): (m.get("size") or 0) / GB for m in ge("ollama").loaded_models()}
    except Exception:  # noqa: BLE001
        return {}
    report = {"removed": [], "resized": []}
    with SessionLocal() as db:
        with advisory_lock(db):
            for inst in db.scalars(select(RuntimeInstance).where(RuntimeInstance.engine == "ollama",
                                                                 RuntimeInstance.state == "ready",
                                                                 RuntimeInstance.model_id.is_not(None))).all():
                model = db.get(RegisteredModel, inst.model_id)
                r = db.scalar(select(ResourceReservation).where(ResourceReservation.holder_id == inst.id,
                                                                ResourceReservation.released_at.is_(None)))
                if model.engine_ref not in sizes:
                    report["removed"].append(model.alias)
                    if r:
                        r.released_at = utcnow()
                    if model.status in (ModelStatus.ready, ModelStatus.busy):
                        _set_status(db, model, ModelStatus.installed, "unloaded by Ollama", event="reconcile")
                    db.delete(inst)
                    continue
                size = round(sizes[model.engine_ref], 2)
                reserve = round(size * 1.1 + 0.5, 2)
                if size and inst.measured_memory_gb != size:
                    inst.measured_memory_gb = model.measured_memory_gb = size
                    inst.reserved_memory_gb = reserve
                    if r:
                        r.memory_gb = reserve
                    report["resized"].append(f"{model.alias}={size}GB")
            db.commit()
    if report["removed"] or report["resized"]:
        log.info("scheduler.ollama_synced", **report)
    return report


def reconcile() -> dict:
    """Align runtime_instances with what engines actually report (after restarts/crashes)."""
    report = {"removed": [], "released": 0, "adopted": []}
    from ..engines import get_engine as ge

    try:
        ollama_loaded = {m.get("name") for m in ge("ollama").loaded_models()}
    except Exception:  # noqa: BLE001
        ollama_loaded = None
    vllm_served = {m.get("id") for m in ge("vllm").loaded_models()}
    with SessionLocal() as db:
        with advisory_lock(db):
            for inst in db.scalars(select(RuntimeInstance)).all():
                model = db.get(RegisteredModel, inst.model_id) if inst.model_id else None
                alive = True
                if inst.state in ("loading", "unloading", "failed"):
                    alive = False
                elif model and inst.engine == "ollama" and ollama_loaded is not None:
                    alive = model.engine_ref in ollama_loaded
                elif model and inst.engine == "vllm":
                    alive = model.alias in vllm_served
                elif not model:
                    alive = ge(inst.engine).is_running()  # type: ignore[attr-defined]
                if not alive:
                    report["removed"].append(str(inst.id))
                    if model and model.status in (ModelStatus.ready, ModelStatus.loading, ModelStatus.busy,
                                                  ModelStatus.unloading):
                        _set_status(db, model, ModelStatus.installed, "reconciled after restart", event="reconcile")
                    db.delete(inst)
            db.flush()
            live_ids = {i.id for i in db.scalars(select(RuntimeInstance)).all()}
            from ..models import Job, JobStatus

            running_jobs = {j for (j,) in db.execute(select(Job.id).where(
                Job.status.in_([JobStatus.preparing, JobStatus.running])))}
            for r in db.scalars(select(ResourceReservation).where(ResourceReservation.released_at.is_(None))):
                if (r.holder_type == "runtime" and r.holder_id not in live_ids) or \
                        (r.holder_type == "job" and r.holder_id not in running_jobs):
                    r.released_at = utcnow()
                    report["released"] += 1
            # Models stuck in transitional states without an instance
            for m in db.scalars(select(RegisteredModel).where(RegisteredModel.status.in_(
                    [ModelStatus.loading, ModelStatus.unloading, ModelStatus.ready, ModelStatus.busy]))):
                if not db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == m.id)):
                    _set_status(db, m, ModelStatus.installed, "reconciled", event="reconcile")
            db.commit()
    if report["removed"] or report["released"]:
        log.info("scheduler.reconciled", **report)
    return report


@dataclass
class Requirement:
    memory_gb: float = 0.0
    gpu_heavy: bool = False
    note: str = ""


# --------------------------------------------------------------------------- overview for the UI


def expected_load_seconds(db: Session, model: RegisteredModel) -> float | None:
    """Duration of the last successful load (from the lifecycle history), used for progress bars."""
    h = db.scalar(select(ModelInstallation).where(ModelInstallation.model_id == model.id, ModelInstallation.event == "load.ok")
                  .order_by(ModelInstallation.ts.desc()).limit(1))
    sec = (h.details or {}).get("seconds") if h else None
    if sec is None and model.smoke_test:
        sec = model.smoke_test.get("load_seconds")
    return float(sec) if sec else None


def _kind(model: RegisteredModel | None, engine: str) -> str:
    if model is None:
        return {"comfyui": "image", "mlworker": "audio"}.get(engine, "engine")
    caps = set(model.capabilities or [])
    if "chat" in caps:
        return "llm"
    if "embeddings" in caps:
        return "embedding"
    return model.modality or "model"


def memory_overview(db: Session) -> dict:
    """Unified-memory budget with every resident model / engine and running job, for the memory panel."""
    from ..models import Job

    b = budget(db)
    mode = get_setting(db, "scheduler_mode")
    timeout_min = float(get_setting(db, "on_demand_idle_timeout_min" if mode == "on_demand" else "model_idle_timeout_min") or 5)
    default_alias = get_setting(db, "preferred_resident_model")
    now = utcnow()
    instances = []
    for inst in db.scalars(select(RuntimeInstance).order_by(RuntimeInstance.started_at)):
        model = db.get(RegisteredModel, inst.model_id) if inst.model_id else None
        protected = _is_protected(db, inst)
        active = active_requests(inst.id)
        idle_s = max(0.0, (now - inst.last_used_at).total_seconds()) if inst.last_used_at else None
        unload_in = None
        if mode != "persistent" and not protected and inst.state == "ready" and active == 0 and idle_s is not None:
            unload_in = max(0.0, timeout_min * 60 - idle_s)
        instances.append({
            "id": str(inst.id), "model_id": str(model.id) if model else None,
            "name": model.alias if model else {"comfyui": "ComfyUI (imágenes/vídeo)", "mlworker": "ML worker (audio/ciencia)"}.get(inst.engine, inst.engine),
            "engine": inst.engine, "kind": _kind(model, inst.engine), "state": inst.state,
            "reserved_gb": round(inst.reserved_memory_gb or 0, 2), "measured_gb": inst.measured_memory_gb,
            "pinned": bool(model and model.pinned), "default": bool(model and default_alias == model.alias),
            "protected": protected, "active_requests": active, "idle_seconds": round(idle_s) if idle_s is not None else None,
            "unload_in_seconds": round(unload_in) if unload_in is not None else None,
            "started_at": inst.started_at, "load_seconds": inst.load_seconds,
            "expected_load_seconds": expected_load_seconds(db, model) if model and inst.state == "loading" else None,
            "vision": bool(model and "vision" in (model.capabilities or [])),
        })
    jobs = []
    for r in db.scalars(select(ResourceReservation).where(ResourceReservation.released_at.is_(None),
                                                          ResourceReservation.holder_type == "job")):
        j = db.get(Job, r.holder_id)
        jobs.append({"id": str(r.holder_id), "title": j.title if j else r.note, "type": j.type if j else None,
                     "reserved_gb": round(r.memory_gb, 2), "gpu_heavy": r.gpu_heavy})
    return {**b, "mode": mode, "idle_timeout_min": timeout_min, "default_model": default_alias,
            "instances": instances, "jobs": jobs}


def plan_load(db: Session, model: RegisteredModel) -> dict:
    """What loading `model` would do now: fits directly, needs evictions (LRU idle models), or cannot fit."""
    b = budget(db)
    resident = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == model.id,
                                                       RuntimeInstance.state.in_(["ready", "busy", "loading"])))
    need = model_memory_gb(db, model)
    out = {"model": model.alias, "need_gb": need, "free_gb": round(b["free_logical_gb"], 2),
           "schedulable_gb": b["schedulable_gb"], "expected_load_seconds": expected_load_seconds(db, model),
           "resident": bool(resident), "evict": [], "fits": True, "blocked_by": []}
    if resident:
        return out
    if need > b["schedulable_gb"]:
        out.update(fits=False, reason="larger than the whole schedulable memory")
        return out
    free = b["free_logical_gb"]
    candidates = eviction_candidates(db)
    for inst in candidates:
        if free >= need:
            break
        m = db.get(RegisteredModel, inst.model_id) if inst.model_id else None
        out["evict"].append({"name": m.alias if m else inst.engine, "reserved_gb": round(inst.reserved_memory_gb, 2),
                             "idle_seconds": round((utcnow() - inst.last_used_at).total_seconds())})
        free += inst.reserved_memory_gb
    if free < need:
        out["fits"] = False
        out["blocked_by"] = [{"name": (db.get(RegisteredModel, i.model_id).alias if i.model_id else i.engine),
                              "reserved_gb": round(i.reserved_memory_gb, 2), "in_use": active_requests(i.id) > 0,
                              "protected": _is_protected(db, i)}
                             for i in db.scalars(select(RuntimeInstance).where(RuntimeInstance.state.in_(["ready", "busy", "loading"])))
                             if i.id not in {c.id for c in candidates}]
        out["reason"] = "models in use or pinned hold the memory"
    return out


def resident_targets(db: Session) -> list[RegisteredModel]:
    """Models that must stay loaded: the general one (preferred_resident_model) and every pinned one, the general one first.
    Nothing is kept in on-demand mode."""
    if get_setting(db, "scheduler_mode") == "on_demand":
        return []
    out: list[RegisteredModel] = []
    pref = get_setting(db, "preferred_resident_model")
    if pref:
        m = db.scalar(select(RegisteredModel).where(RegisteredModel.alias == pref))
        if m is not None:
            out.append(m)
    for m in db.scalars(select(RegisteredModel).where(RegisteredModel.pinned.is_(True), RegisteredModel.backend.in_(("vllm", "ollama")))
                        .order_by(RegisteredModel.alias)):
        if all(m.id != x.id for x in out):
            out.append(m)
    return [m for m in out if m.status in (ModelStatus.installed, ModelStatus.ready, ModelStatus.loading, ModelStatus.busy)]


def resident_fit(db: Session, extra: RegisteredModel | None = None) -> tuple[bool, str, list[dict]]:
    """Do the models that must stay loaded (plus `extra`, about to be pinned) fit together in what the hub may hand out?
    A vLLM model reserves gpu_memory_utilization × the machine's memory, so two big ones rarely fit side by side."""
    models = resident_targets(db)
    if extra is not None and all(extra.id != m.id for m in models):
        models.append(extra)
    items = [{"alias": m.alias, "gb": round(model_memory_gb(db, m), 1)} for m in models]
    total = round(sum(i["gb"] for i in items), 1)
    cap = round(budget(db)["schedulable_gb"], 1)
    if total <= cap + 0.01:
        return True, "", items
    detail = ", ".join(f"{i['alias']} {i['gb']:.0f} GB" for i in items)
    return False, (f"No caben juntos: reservarían {total:.0f} GB ({detail}) y el hub puede repartir {cap:.0f} GB. Baja la "
                   f"gpu_memory_utilization de alguno (pestaña Parámetros del modelo) o quita otro fijado. / "
                   f"They do not fit together: they would reserve {total:.0f} GB ({detail}) and the hub can hand out {cap:.0f} GB. "
                   "Lower the gpu_memory_utilization of one of them or unpin another."), items


def ensure_resident() -> str | None:
    """Keep the general model and every pinned model loaded: after boots, crashes or manual unloads. Runs from the worker
    loop, one model at a time (big loads never overlap); a failed attempt backs off for 10 minutes per model, and a model that
    died for lack of memory is left for a person to load."""
    import threading

    with SessionLocal() as db:
        loaded = {mid for (mid,) in db.execute(select(RuntimeInstance.model_id)) if mid is not None}
        todo = [(m.id, m.alias) for m in resident_targets(db) if m.id not in loaded and not memguard.oom_flagged(m.id)
                and not memguard.cancelled_flagged(m.id)]
    if not todo:
        return None
    r = get_redis()
    for mid, alias in todo:
        if not r.set(f"sah:resident:attempt:{alias}", alias, nx=True, ex=600):
            continue

        def run(mid=mid, alias=alias):
            try:
                ensure_loaded(mid, wait_s=1800)
                r.delete(f"sah:resident:attempt:{alias}")
                log.info("scheduler.resident_loaded", model=alias)
            except Exception as e:  # noqa: BLE001 - keep the back-off key: retry in 10 min
                log.warning("scheduler.resident_failed", model=alias, error=str(getattr(e, "message", e))[:300])

        threading.Thread(target=run, daemon=True).start()
        return alias
    return None
