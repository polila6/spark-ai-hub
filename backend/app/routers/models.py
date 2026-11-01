"""Model registry API: list/inspect/configure/load/unload/delete/import/share."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_admin, require_researcher, visible_filter
from ..engines import EngineError, get_engine
from ..models import ModelInstallation, ModelStatus, RegisteredModel, RuntimeInstance, SharedResource, Visibility
from ..services import llm, registry, scheduler

router = APIRouter(prefix="/api/v1/models", tags=["models"])

VLLM_PARAM_KEYS = {"max_model_len", "gpu_memory_utilization", "max_num_seqs", "dtype", "kv_cache_dtype",
                   "quantization", "trust_remote_code", "enable_tool_calling", "tool_call_parser", "reasoning_parser",
                   "enforce_eager", "load_timeout_s", "speculative_tokens"}
OLLAMA_PARAM_KEYS = {"keep_alive"}


class ModelPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    alias: str | None = Field(default=None, max_length=128)
    visibility: Visibility | None = None
    pinned: bool | None = None
    disabled: bool | None = None
    runtime_params: dict | None = None


class ShareIn(BaseModel):
    user_ids: list[uuid.UUID]


def _get_visible(db: Session, p: Principal, model_id: uuid.UUID) -> RegisteredModel:
    m = db.get(RegisteredModel, model_id)
    if not m:
        raise not_found("model")
    if p.is_admin:
        return m
    shared = {r for (r,) in db.execute(select(SharedResource.resource_id).where(
        SharedResource.resource_type == "model", SharedResource.grantee_id == p.id))}
    if not registry.can_use(m, p.id, False, shared):
        raise not_found("model")
    return m


def _can_manage(p: Principal, m: RegisteredModel) -> bool:
    return p.is_admin or m.owner_id == p.id


@router.get("")
def list_models(p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                modality: str | None = None, status: str | None = None, backend: str | None = None):
    q = select(RegisteredModel).order_by(RegisteredModel.created_at.desc())
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    if modality:
        q = q.where(RegisteredModel.modality == modality)
    if backend:
        q = q.where(RegisteredModel.backend == backend)
    if status:
        q = q.where(RegisteredModel.status == ModelStatus(status))
    inst = {i.model_id: i for i in db.scalars(select(RuntimeInstance))}
    out = []
    for m in db.scalars(q):
        d = registry.model_public(m)
        i = inst.get(m.id)
        d["runtime"] = {"state": i.state, "reserved_memory_gb": i.reserved_memory_gb,
                        "measured_memory_gb": i.measured_memory_gb, "load_seconds": i.load_seconds,
                        "last_used_at": i.last_used_at} if i else None
        out.append(d)
    return out


@router.get("/{model_id}")
def get_model(model_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    m = _get_visible(db, p, model_id)
    d = registry.model_public(m, include_config=_can_manage(p, m))
    d["dependencies"] = registry.dependencies(db, m)
    d["history"] = [{"ts": h.ts, "event": h.event, "from": h.from_status, "to": h.to_status, "details": h.details}
                    for h in db.scalars(select(ModelInstallation).where(ModelInstallation.model_id == m.id)
                                        .order_by(ModelInstallation.ts.desc()).limit(100))]
    if _can_manage(p, m):
        d["shared_with"] = [str(r) for (r,) in db.execute(select(SharedResource.grantee_id).where(
            SharedResource.resource_type == "model", SharedResource.resource_id == m.id))]
    return d


@router.patch("/{model_id}")
def patch_model(model_id: uuid.UUID, body: ModelPatch, request: Request, p: Principal = Depends(require_researcher),
                db: Session = Depends(get_db)):
    m = _get_visible(db, p, model_id)
    if not _can_manage(p, m):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Only the owner or an admin can change this model"})
    ch = body.model_dump(exclude_unset=True)
    if "visibility" in ch and ch["visibility"] == Visibility.public and not p.is_admin:
        raise HTTPException(403, detail={"code": "admin_required", "message": "Only admins publish models to everyone"})
    if "pinned" in ch and not p.is_admin:
        raise HTTPException(403, detail={"code": "admin_required", "message": "Only admins pin models"})
    if "alias" in ch and ch["alias"] != m.alias:
        if db.scalar(select(RegisteredModel.id).where(RegisteredModel.alias == ch["alias"])):
            raise HTTPException(409, detail={"code": "alias_taken", "message": "Alias already in use"})
        if db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == m.id)):
            raise HTTPException(409, detail={"code": "loaded", "message": "Unload the model before renaming"})
        m.alias = registry.ALIAS_RE.sub("-", ch["alias"].lower()).strip("-")
    for k in ("display_name", "visibility", "pinned"):
        if k in ch:
            setattr(m, k, ch[k])
    if "disabled" in ch:
        if ch["disabled"]:
            if db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == m.id)):
                scheduler.unload_model(m.id, actor_id=p.id)
            registry.mark(db, m, ModelStatus.disabled, "Disabled by user", actor_id=p.id)
        elif m.status == ModelStatus.disabled:
            registry.mark(db, m, ModelStatus.installed, None, actor_id=p.id)
    if body.runtime_params is not None:
        allowed = VLLM_PARAM_KEYS if m.backend == "vllm" else OLLAMA_PARAM_KEYS
        bad = set(body.runtime_params) - allowed
        if bad:
            raise HTTPException(422, detail={"code": "invalid_params", "message": f"Unsupported params: {sorted(bad)}"})
        if body.runtime_params.get("trust_remote_code") and not m.trust_remote_code_approved:
            raise HTTPException(403, detail={"code": "remote_code_not_approved",
                                             "message": "An administrator must approve remote code first"})
        gmu = body.runtime_params.get("gpu_memory_utilization")
        if gmu is not None and not (0.05 <= float(gmu) <= 0.9):
            raise HTTPException(422, detail={"code": "invalid_params", "message": "gpu_memory_utilization 0.05–0.9"})
        registry.set_runtime_params(db, m, {**(m.runtime_config.params if m.runtime_config else {}),
                                            **body.runtime_params})
        registry.history(db, m, "config.changed", {"params": body.runtime_params}, p.id)
    db.commit()
    audit(db, "models.update", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={k: (v.value if hasattr(v, "value") else v) for k, v in ch.items()})
    return registry.model_public(m, include_config=True)


@router.get("/{model_id}/load-plan")
def load_plan(model_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What loading this model would do right now (memory needed, idle models that would be unloaded, ETA)."""
    m = _get_visible(db, p, model_id)
    return scheduler.plan_load(db, m)


@router.post("/{model_id}/load")
def load_model(model_id: uuid.UUID, request: Request, p: Principal = Depends(require_researcher),
               db: Session = Depends(get_db), background: bool = False):
    m = _get_visible(db, p, model_id)
    if m.backend not in ("ollama", "vllm"):
        raise HTTPException(400, detail={"code": "not_loadable", "message": "Only LLM engines keep models resident"})
    if background:  # the memory panel follows the progress; errors end up in the model status message
        import threading

        mid, uid = m.id, p.id

        def run():
            try:
                scheduler.ensure_loaded(mid, wait_s=1800, actor_id=uid)
            except Exception as e:  # noqa: BLE001
                from ..logging_setup import log

                log.warning("models.background_load_failed", model=str(mid), error=str(getattr(e, "message", e)))

        threading.Thread(target=run, daemon=True).start()
        audit(db, "models.load", actor=p.user, request=request, target_type="model", target_id=str(m.id),
              details={"background": True})
        return {"ok": True, "state": "loading"}
    try:
        inst = scheduler.ensure_loaded(m.id, wait_s=1800, actor_id=p.id)
    except scheduler.SchedulingError as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message, "retry_after": e.retry_after})
    except EngineError as e:
        raise HTTPException(502, detail={"code": "engine_error", "message": e.message, "remediation": e.remediation})
    audit(db, "models.load", actor=p.user, request=request, target_type="model", target_id=str(m.id))
    return {"ok": True, "state": inst.state, "load_seconds": inst.load_seconds,
            "measured_memory_gb": inst.measured_memory_gb, "reserved_memory_gb": inst.reserved_memory_gb}


@router.post("/{model_id}/unload")
def unload_model(model_id: uuid.UUID, request: Request, p: Principal = Depends(require_researcher),
                 db: Session = Depends(get_db), force: bool = False):
    m = _get_visible(db, p, model_id)
    if force and not p.is_admin:
        raise HTTPException(403, detail={"code": "admin_required", "message": "Only admins can force unload"})
    if m.pinned and not p.is_manager:
        raise HTTPException(403, detail={"code": "pinned", "message": "The general model stays loaded for everyone; "
                                                                      "only administrators/managers can unload it"})
    try:
        done = scheduler.unload_model(m.id, actor_id=p.id, force=force)
    except scheduler.SchedulingError as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    audit(db, "models.unload", actor=p.user, request=request, target_type="model", target_id=str(m.id))
    return {"ok": True, "was_loaded": done}


CONTEXT_PRESETS = (8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576)


class ContextIn(BaseModel):
    max_model_len: int | None = Field(default=None, ge=4096, le=1_048_576)
    speculative_tokens: int | None = Field(default=None, ge=0, le=4)


def _config(m: RegisteredModel) -> dict:
    import json

    snap = registry.hf_snapshot_dir(m)
    try:
        return json.loads((snap / "config.json").read_text()) if snap else {}
    except (OSError, ValueError):
        return {}


def _has_mtp(m: RegisteredModel) -> bool:
    cfg = _config(m)
    return any(int(c.get("mtp_num_hidden_layers") or c.get("num_nextn_predict_layers") or 0) > 0
               for c in (cfg.get("text_config") or {}, cfg))


def _native_context(m: RegisteredModel) -> int | None:
    """Longest context the model was trained for (config.json max_position_embeddings)."""
    cfg = _config(m)
    for c in (cfg.get("text_config") or {}, cfg):
        if c.get("max_position_embeddings"):
            return int(c["max_position_embeddings"])
    return None


def _kv_tokens(m: RegisteredModel) -> int | None:
    """Tokens that fit in the KV cache vLLM reserved (shared by all concurrent conversations)."""
    import re

    import httpx

    from ..engines.vllm import container_host

    try:
        text = httpx.get(f"http://{container_host(m.alias)}:8000/metrics", timeout=3).text
    except httpx.HTTPError:
        return None
    hit = re.search(r'kv_cache_size_tokens="(\d+)"', text)
    return int(hit.group(1)) if hit else None


@router.get("/{model_id}/context")
def context_info(model_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Context window of a vLLM model: current, the model's maximum, and how many long conversations fit at once."""
    m = _get_visible(db, p, model_id)
    if m.backend != "vllm":
        return {"supported": False, "current": llm.context_tokens(m)}
    params = (m.runtime_config.params if m.runtime_config else {}) or {}
    current = int(params.get("max_model_len") or 8192)
    native = _native_context(m)
    loaded = bool(db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == m.id)))
    kv = _kv_tokens(m) if loaded else None
    top = native or 262144
    return {"supported": True, "current": current, "native_max": native, "loaded": loaded, "kv_cache_tokens": kv,
            "max_num_seqs": int(params.get("max_num_seqs") or 16), "can_manage": _can_manage(p, m),
            "mtp_available": _has_mtp(m), "speculative_tokens": int(params.get("speculative_tokens") or 0),
            "presets": [{"tokens": n, "fits_at_once": (kv // n) if kv else None}
                        for n in sorted({*(x for x in CONTEXT_PRESETS if x <= top), current, *([native] if native else [])})]}


@router.post("/{model_id}/context")
def set_context(model_id: uuid.UUID, body: ContextIn, request: Request, p: Principal = Depends(require_researcher),
                db: Session = Depends(get_db)):
    """Change max_model_len and, if the model is loaded, reload it in the background (a few minutes)."""
    m = _get_visible(db, p, model_id)
    if not _can_manage(p, m):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Only the owner or an admin can change this model"})
    if m.backend != "vllm":
        raise HTTPException(400, detail={"code": "not_vllm", "message": "Only vLLM models have a configurable window"})
    native = _native_context(m)
    if body.max_model_len and native and body.max_model_len > native:
        raise HTTPException(422, detail={"code": "too_long", "message": f"Este modelo admite como máximo {native} tokens"})
    if body.speculative_tokens and not _has_mtp(m):
        raise HTTPException(422, detail={"code": "no_mtp", "message": "Este modelo no trae capas MTP"})
    old = (m.runtime_config.params if m.runtime_config else {}) or {}
    change = {k: v for k, v in body.model_dump().items() if v is not None}
    if not change:
        raise HTTPException(422, detail={"code": "empty", "message": "Nothing to change"})
    registry.set_runtime_params(db, m, {**old, **change})
    registry.history(db, m, "config.changed", {"params": change}, p.id)
    loaded = bool(db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == m.id)))
    db.commit()
    audit(db, "models.context", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={"from": {k: old.get(k) for k in change}, "to": change})
    if loaded:
        import threading

        mid, uid = m.id, p.id

        def run():
            try:
                scheduler.unload_model(mid, actor_id=uid, force=True)
                scheduler.ensure_loaded(mid, wait_s=1800, actor_id=uid)
            except Exception as e:  # noqa: BLE001
                from ..logging_setup import log

                log.warning("models.context_reload_failed", model=str(mid), error=str(getattr(e, "message", e)))

        threading.Thread(target=run, daemon=True).start()
    return {"ok": True, **change, "reloading": loaded}


@router.post("/{model_id}/check")
def check_model(model_id: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db),
                deep: bool = False):
    m = _get_visible(db, p, model_id)
    res = registry.check_consistency(m, deep=deep)
    registry.history(db, m, "consistency.check", res, p.id)
    db.commit()
    return res


@router.post("/{model_id}/smoke-test")
def smoke(model_id: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    from ..models import SchedClass
    from ..services.jobs import create_job

    m = _get_visible(db, p, model_id)
    j = create_job(db, p.user, "smoke_test", {"model_id": str(m.id)}, title=f"Smoke test {m.alias}",
                   sched_class=SchedClass.batch)
    return {"job_id": str(j.id)}


@router.post("/{model_id}/approve-remote-code")
def approve_remote_code(model_id: uuid.UUID, request: Request, p: Principal = Depends(require_admin),
                        db: Session = Depends(get_db), approve: bool = True):
    m = db.get(RegisteredModel, model_id)
    if not m:
        raise not_found("model")
    m.trust_remote_code_approved = approve
    registry.history(db, m, "remote_code.approved" if approve else "remote_code.revoked", {}, p.id)
    db.commit()
    audit(db, "models.remote_code", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={"approved": approve})
    return {"ok": True}


@router.put("/{model_id}/shares")
def share_model(model_id: uuid.UUID, body: ShareIn, request: Request, p: Principal = Depends(require_researcher),
                db: Session = Depends(get_db)):
    m = _get_visible(db, p, model_id)
    if not _can_manage(p, m):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Only owner/admin can share"})
    db.query(SharedResource).filter(SharedResource.resource_type == "model", SharedResource.resource_id == m.id).delete()
    for uid in set(body.user_ids):
        db.add(SharedResource(resource_type="model", resource_id=m.id, grantee_id=uid, granted_by=p.id))
    if body.user_ids and m.visibility == Visibility.private:
        m.visibility = Visibility.shared
    db.commit()
    audit(db, "models.share", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={"users": [str(u) for u in body.user_ids]})
    return {"ok": True}


@router.delete("/{model_id}")
def delete_model(model_id: uuid.UUID, request: Request, p: Principal = Depends(require_researcher),
                 db: Session = Depends(get_db), delete_files: bool = True):
    m = _get_visible(db, p, model_id)
    if not _can_manage(p, m):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Only owner/admin can delete"})
    deps = registry.dependencies(db, m)
    if deps["active_jobs"]:
        raise HTTPException(409, detail={"code": "in_use", "message": "Model is used by active jobs",
                                         "jobs": deps["active_jobs"]})
    if deps["loaded"]:
        try:
            scheduler.unload_model(m.id, actor_id=p.id)
        except scheduler.SchedulingError as e:
            raise HTTPException(409, detail={"code": e.code, "message": e.message})
        db.expire_all()
        m = db.get(RegisteredModel, model_id)
    registry.mark(db, m, ModelStatus.removing, None, actor_id=p.id)
    db.commit()
    removed = {"removed": []}
    if delete_files:
        try:
            removed = registry.delete_files(db, m)
        except Exception as e:  # noqa: BLE001
            registry.mark(db, m, ModelStatus.failed, f"Delete failed: {e}", actor_id=p.id)
            db.commit()
            raise HTTPException(500, detail={"code": "delete_failed", "message": str(e)})
    db.query(SharedResource).filter(SharedResource.resource_type == "model", SharedResource.resource_id == m.id).delete()
    db.delete(m)
    db.commit()
    audit(db, "models.delete", actor=p.user, request=request, target_type="model", target_id=str(model_id),
          details={"alias": m.alias, **removed})
    return {"ok": True, **removed}


@router.get("/import/candidates")
def import_candidates(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Models present in engines/caches but not yet registered."""
    from ..config import get_settings

    registered_refs = {r for (r,) in db.execute(select(RegisteredModel.engine_ref).where(RegisteredModel.source == "ollama"))}
    registered_repos = {(a, b) for a, b in db.execute(select(RegisteredModel.repo_id, RegisteredModel.revision))}
    out = {"ollama": [], "hf_cache": []}
    try:
        for m in get_engine("ollama").list_local():  # type: ignore[attr-defined]
            if m["name"] not in registered_refs:
                out["ollama"].append({"name": m["name"], "size": m.get("size"), "details": m.get("details")})
    except Exception as e:  # noqa: BLE001
        out["ollama_error"] = str(e)
    from huggingface_hub import scan_cache_dir

    for cache in (get_settings().hf_cache_dir, get_settings().import_hf_cache / "hub"):
        try:
            info = scan_cache_dir(cache)
        except Exception:  # noqa: BLE001
            continue
        for repo in info.repos:
            if repo.repo_type != "model":
                continue
            for rev in repo.revisions:
                if (repo.repo_id, rev.commit_hash) in registered_repos or rev.size_on_disk < 50e6:
                    continue
                out["hf_cache"].append({"repo_id": repo.repo_id, "revision": rev.commit_hash,
                                        "size": rev.size_on_disk, "cache": str(cache),
                                        "needs_copy": cache != get_settings().hf_cache_dir})
    return out


class ImportOllama(BaseModel):
    name: str = Field(max_length=200)
    visibility: Visibility = Visibility.public


@router.post("/import/ollama")
def import_ollama(body: ImportOllama, request: Request, p: Principal = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from ..worker.handlers.downloads import register_ollama_details

    names = {m["name"] for m in get_engine("ollama").list_local()}  # type: ignore[attr-defined]
    if body.name not in names:
        raise not_found("ollama model")
    m = RegisteredModel(alias=registry.make_alias(db, body.name.replace(":", "-")), display_name=body.name,
                        source="ollama", engine_ref=body.name, backend="ollama", status=ModelStatus.installed,
                        owner_id=p.id, visibility=body.visibility, repo_id=None)
    db.add(m)
    db.commit()
    register_ollama_details(m.id)
    audit(db, "models.import", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={"ollama": body.name})
    db.refresh(m)
    return registry.model_public(m)
