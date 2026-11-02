"""The general ("main") model: the LLM kept resident for everyone and used by default by chat, API and tools.

Making a model main pins it, unpins the previous main, points the default-model settings at it and loads it in the
background. The previous main becomes a normal model: the scheduler unloads it when the room is needed (or after the
idle timeout). A model that is not downloaded yet can be made main once its installation finishes: a *pending main*
marker is stored in the system settings and the install job performs the switch when it succeeds.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..engines import EngineError
from ..logging_setup import log
from ..models import DownloadTask, ModelStatus, RegisteredModel, RuntimeInstance
from ..redis_client import get_redis
from ..settings_store import get_setting, set_setting
from . import registry, scheduler

MAIN_KEY = "preferred_resident_model"  # alias kept resident by scheduler.ensure_resident (the "general" model)
PENDING_KEY = "pending_main_model"  # {model_id, alias, repo_id, requested_by, requested_at[, error]}
FOLLOW_KEYS = ("chat_default_model", "harness_default_model")  # follow the main model when they pointed at the old one
SWITCH_KEY = "sah:main:switch"  # Redis: progress of the last switch (shared by the API and worker processes)
RESIDENT_ATTEMPT_KEY = "sah:resident:attempt"  # scheduler.ensure_resident back-off key (avoids a parallel load)
PENDING_LOCK = "sah:main:pending-lock"
LLM_BACKENDS = ("ollama", "vllm")
NOT_DOWNLOADED = (ModelStatus.discovered, ModelStatus.downloading, ModelStatus.downloaded, ModelStatus.installing)
SWITCHABLE = (ModelStatus.installed, ModelStatus.ready, ModelStatus.loading, ModelStatus.busy, ModelStatus.unloading,
              ModelStatus.failed)
LOAD_WAIT_S = 7200  # some engines autotune their kernels on the first start (~45 min)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- pure helpers (unit tested)


def is_llm(backend: str | None, capabilities: list | None, modality: str | None, installed: bool = True) -> bool:
    """A chat LLM served by Ollama/vLLM. Before installation Ollama capabilities are unknown, so modality decides."""
    if backend not in LLM_BACKENDS or modality == "embedding":
        return False
    caps = set(capabilities or [])
    if "chat" in caps:
        return True
    return not installed and not caps and modality in (None, "text")


def settings_changes(current: dict, prev_alias: str | None, new_alias: str) -> dict:
    """Settings to write when `new_alias` becomes main: always the resident/default model; chat/tool defaults only
    when they pointed at the previous main (an explicit different choice of the administrator is kept)."""
    out = {MAIN_KEY: new_alias, "default_model": new_alias}
    for k in FOLLOW_KEYS:
        if prev_alias and current.get(k) == prev_alias:
            out[k] = new_alias
    return out


def adjust_plan(plan: dict, previous: dict | None) -> dict:
    """scheduler.plan_load() counts the current main as protected. When switching it stops being protected, so its
    memory can be freed: recompute fits/evict counting it last (it is unloaded only if the room is still needed)."""
    out = {**plan, "evict": list(plan.get("evict") or []), "blocked_by": list(plan.get("blocked_by") or []),
           "unload_previous": False, "previous": previous}
    if plan.get("resident") or plan.get("fits") or not previous:
        return out
    need, free = float(plan["need_gb"]), float(plan["free_gb"])
    if need > float(plan.get("schedulable_gb") or 0):
        return out
    pool = [e for e in out["evict"] if e.get("name") != previous["name"]]
    pool.append({"name": previous["name"], "reserved_gb": previous["reserved_gb"],
                 "idle_seconds": previous.get("idle_seconds"), "previous_main": True})
    evict = []
    for e in pool:
        if free >= need:
            break
        evict.append(e)
        free += float(e["reserved_gb"] or 0)
    if free >= need:
        out.update(fits=True, evict=evict, reason=None, unload_previous=any(e.get("previous_main") for e in evict),
                   blocked_by=[b for b in out["blocked_by"] if b.get("name") != previous["name"]])
    return out


# --------------------------------------------------------------------------- state


def current_main(db: Session) -> RegisteredModel | None:
    alias = get_setting(db, MAIN_KEY)
    return db.scalar(select(RegisteredModel).where(RegisteredModel.alias == alias)) if alias else None


def _switch_state() -> dict | None:
    try:
        raw = get_redis().get(SWITCH_KEY)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _set_switch(**fields) -> None:
    try:
        st = {**(_switch_state() or {}), **fields, "updated_at": utcnow_iso()}
        get_redis().set(SWITCH_KEY, json.dumps(st, default=str), ex=86400)
    except Exception:  # noqa: BLE001 - progress is informative; the durable state is in Postgres
        pass


def _instance_info(db: Session, model: RegisteredModel) -> dict:
    inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == model.id))
    if inst is None:
        state = "stopped"
    else:
        state = {"ready": "loaded", "busy": "loaded", "loading": "loading", "unloading": "unloading"}.get(inst.state, inst.state)
    return {"state": state, "reserved_gb": round(inst.reserved_memory_gb or 0, 2) if inst else None,
            "measured_gb": inst.measured_memory_gb if inst else None, "started_at": inst.started_at if inst else None,
            "load_seconds": inst.load_seconds if inst else None,
            "active_requests": scheduler.active_requests(inst.id) if inst else 0,
            "expected_load_seconds": scheduler.expected_load_seconds(db, model)}


def plan_switch(db: Session, model: RegisteredModel) -> dict:
    """What making `model` main would do to the memory now (load-plan with the previous main as freeable)."""
    plan = scheduler.plan_load(db, model)
    prev = current_main(db)
    info = None
    if prev and prev.id != model.id:
        inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == prev.id,
                                                       RuntimeInstance.state.in_(["ready", "busy"])))
        if inst:
            info = {"name": prev.alias, "reserved_gb": round(inst.reserved_memory_gb or 0, 2),
                    "idle_seconds": round((datetime.now(timezone.utc) - inst.last_used_at).total_seconds())
                    if inst.last_used_at else None, "in_use": scheduler.active_requests(inst.id) > 0}
    out = adjust_plan(plan, info)
    out["previous_alias"] = prev.alias if prev and prev.id != model.id else None
    out["idle_timeout_min"] = float(get_setting(db, "model_idle_timeout_min") or 15)
    return out


def _latest_task(db: Session, model_id) -> DownloadTask | None:
    return db.scalar(select(DownloadTask).where(DownloadTask.model_id == model_id)
                     .order_by(DownloadTask.created_at.desc()).limit(1))


def pending_info(db: Session) -> dict | None:
    pend = get_setting(db, PENDING_KEY)
    if not pend:
        return None
    try:
        m = db.get(RegisteredModel, uuid.UUID(pend["model_id"]))
    except (KeyError, ValueError):
        return None
    out = dict(pend)
    if m is None:
        out.update(status="removed", error=pend.get("error") or "The model was deleted before its installation finished")
        return out
    t = _latest_task(db, m.id)
    out.update(alias=m.alias, status=m.status.value, status_message=m.status_message,
               download={"id": str(t.id), "status": t.status.value, "downloaded_bytes": t.downloaded_bytes,
                         "total_bytes": t.total_bytes, "speed_bps": t.speed_bps, "error": t.error,
                         "job_id": str(t.job_id) if t.job_id else None,
                         "awaiting_approval": t.job_id is None and t.status.value == "pending"} if t else None)
    if not out.get("error") and m.status == ModelStatus.failed:
        out["error"] = (t.error if t and t.error else None) or m.status_message or "Installation failed"
    return out


def overview(db: Session) -> dict:
    """Current main model with its runtime state, the last switch and the pending (downloading) main, if any."""
    m = current_main(db)
    model = None
    if m:
        model = {**registry.model_public(m), **_instance_info(db, m)}
    return {"alias": get_setting(db, MAIN_KEY), "model": model, "switch": _switch_state(), "pending": pending_info(db),
            "mode": get_setting(db, "scheduler_mode"), "idle_timeout_min": float(get_setting(db, "model_idle_timeout_min") or 15)}


# --------------------------------------------------------------------------- actions


def set_pending(db: Session, model: RegisteredModel, actor_id) -> dict:
    marker = {"model_id": str(model.id), "alias": model.alias, "repo_id": model.repo_id,
              "requested_by": str(actor_id) if actor_id else None, "requested_at": utcnow_iso()}
    set_setting(db, PENDING_KEY, marker, actor_id)
    registry.history(db, model, "main.pending", {"repo": model.repo_id}, actor_id)
    db.commit()
    return marker


def clear_pending(db: Session, actor_id=None) -> dict | None:
    pend = get_setting(db, PENDING_KEY)
    if pend:
        set_setting(db, PENDING_KEY, None, actor_id)
    return pend


def make_main(db: Session, model: RegisteredModel, actor_id=None) -> dict:
    """Pin `model`, unpin the previous main, update the default-model settings and load it in the background."""
    prev = current_main(db)
    prev_alias = prev.alias if prev else get_setting(db, MAIN_KEY)
    if prev and prev.id == model.id:
        prev = None
    changes = settings_changes({k: get_setting(db, k) for k in FOLLOW_KEYS}, prev_alias, model.alias)
    model.pinned = True
    made_public = False
    if model.visibility.value != "public":  # the general model is for everyone
        from ..models import Visibility

        model.visibility = Visibility.public
        made_public = True
    registry.history(db, model, "main.set", {"previous": prev_alias, "made_public": made_public}, actor_id)
    if prev:
        prev.pinned = False
        registry.history(db, prev, "main.unset", {"next": model.alias}, actor_id)
    db.commit()
    for k, v in changes.items():
        set_setting(db, k, v, actor_id)
    if get_setting(db, PENDING_KEY):  # the latest choice wins over a download waiting to become main
        set_setting(db, PENDING_KEY, None, actor_id)
    start_load(model.id, model.alias, prev.id if prev else None, prev_alias if prev else None, actor_id)
    return {"model_id": str(model.id), "alias": model.alias, "previous": prev_alias if prev else None,
            "settings": changes, "made_public": made_public, "loading": True}


def start_load(model_id, alias: str, prev_id, prev_alias: str | None, actor_id) -> None:
    try:  # keep scheduler.ensure_resident (worker loop) from starting the same load in parallel
        get_redis().set(RESIDENT_ATTEMPT_KEY, alias, ex=LOAD_WAIT_S)
    except Exception:  # noqa: BLE001
        pass
    _set_switch(model_id=str(model_id), alias=alias, previous=prev_alias, state="loading", error=None,
                message=None, started_at=utcnow_iso(), finished_at=None)
    threading.Thread(target=_load_worker, args=(model_id, alias, prev_id, actor_id), daemon=True).start()


def _load_worker(model_id, alias: str, prev_id, actor_id) -> None:
    deadline = time.time() + LOAD_WAIT_S
    tried_prev = False
    while True:
        try:
            inst = scheduler.ensure_loaded(model_id, wait_s=max(60.0, deadline - time.time()), actor_id=actor_id)
            _set_switch(state="ready", message=None, finished_at=utcnow_iso(), load_seconds=inst.load_seconds)
            try:
                get_redis().delete(RESIDENT_ATTEMPT_KEY)
            except Exception:  # noqa: BLE001
                pass
            log.info("main_model.loaded", model=alias)
            return
        except scheduler.SchedulingError as e:
            if e.code == "insufficient_resources" and prev_id and not tried_prev and time.time() < deadline:
                # The previous main may still be protected (persistent mode) or busy: unload it explicitly.
                with SessionLocal() as db:
                    prev = db.get(RegisteredModel, prev_id)
                    still_main = bool(prev and (prev.pinned or get_setting(db, MAIN_KEY) == prev.alias))
                if not still_main:
                    try:
                        scheduler.unload_model(prev_id, actor_id=actor_id)
                        tried_prev = True
                        continue
                    except scheduler.SchedulingError:  # answering requests: wait until it is idle
                        _set_switch(state="waiting", message="waiting_previous")
                        time.sleep(15)
                        continue
            _fail(alias, e.message)
            return
        except EngineError as e:
            _fail(alias, e.message)
            return
        except Exception as e:  # noqa: BLE001
            _fail(alias, str(getattr(e, "message", e)))
            return


def _fail(alias: str, message: str) -> None:
    _set_switch(state="failed", error=message[:2000], finished_at=utcnow_iso())
    try:  # ensure_resident retries on its own after the 10-minute back-off
        get_redis().set(RESIDENT_ATTEMPT_KEY, alias, ex=600)
    except Exception:  # noqa: BLE001
        pass
    log.warning("main_model.load_failed", model=alias, error=message[:300])


def on_installed(model_id) -> bool:
    """Install-job hook: if this model was waiting to become main, switch now. Never raises."""
    try:
        with SessionLocal() as db:
            pend = get_setting(db, PENDING_KEY)
            if not pend or pend.get("model_id") != str(model_id):
                return False
            r = get_redis()
            if not r.set(PENDING_LOCK, str(model_id), nx=True, ex=120):
                return False
            try:
                m = db.get(RegisteredModel, model_id)
                if m is None or m.status not in SWITCHABLE:
                    return False
                if not is_llm(m.backend, m.capabilities, m.modality):
                    set_setting(db, PENDING_KEY, {**pend, "error": "The installed model is not a chat LLM; it cannot be the main model"})
                    return False
                actor = uuid.UUID(pend["requested_by"]) if pend.get("requested_by") else None
                res = make_main(db, m, actor)
                from ..audit import audit
                from ..models import User

                audit(db, "models.main", actor=db.get(User, actor) if actor else None, target_type="model",
                      target_id=str(m.id), details={"from": res["previous"], "to": m.alias, "auto": "after_install"})
                log.info("main_model.switched_after_install", model=m.alias)
                return True
            finally:
                r.delete(PENDING_LOCK)
    except Exception as e:  # noqa: BLE001 - an install must never fail because of the switch
        log.warning("main_model.pending_switch_failed", model=str(model_id), error=str(e)[:300])
        return False


def check_pending() -> None:
    """Worker-loop safety net: complete a pending switch whose install finished without the hook (e.g. a restart)."""
    with SessionLocal() as db:
        pend = get_setting(db, PENDING_KEY)
        if not pend or pend.get("error") or not pend.get("model_id"):
            return
        try:
            m = db.get(RegisteredModel, uuid.UUID(pend["model_id"]))
        except ValueError:
            return
        if m is None or m.status not in (ModelStatus.installed, ModelStatus.ready):
            return
        mid = m.id
    on_installed(mid)
