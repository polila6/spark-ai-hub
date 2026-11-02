"""Model registry operations: registration, serialisation, consistency checks, safe deletion."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Job,
    JobStatus,
    ModelInstallation,
    ModelRuntimeConfig,
    ModelStatus,
    ModelVersion,
    RegisteredModel,
    RuntimeInstance,
    Visibility,
)

ALIAS_RE = re.compile(r"[^a-z0-9._:-]+")


def make_alias(db: Session, base: str) -> str:
    a = ALIAS_RE.sub("-", base.lower()).strip("-")[:100] or "model"
    alias, i = a, 2
    while db.scalar(select(RegisteredModel.id).where(RegisteredModel.alias == alias)):
        alias = f"{a}-{i}"
        i += 1
    return alias


def model_public(m: RegisteredModel, include_config: bool = False) -> dict:
    d = {
        "id": str(m.id), "alias": m.alias, "display_name": m.display_name or m.alias, "source": m.source,
        "repo_id": m.repo_id, "revision": m.revision, "task": m.task, "modality": m.modality,
        "architecture": m.architecture, "quantization": m.quantization, "format": m.format,
        "parameters_b": m.parameters_b, "context_length": m.context_length, "license": m.license,
        "gated": m.gated, "custom_code": m.custom_code, "trust_remote_code_approved": m.trust_remote_code_approved,
        "size_bytes": m.size_bytes, "backend": m.backend, "engine_ref": m.engine_ref,
        "status": m.status.value, "status_message": m.status_message,
        "owner_id": str(m.owner_id) if m.owner_id else None, "visibility": m.visibility.value,
        "capabilities": m.capabilities or [], "pinned": m.pinned,
        "measured_memory_gb": m.measured_memory_gb, "estimated_memory_gb": m.estimated_memory_gb,
        "smoke_test": m.smoke_test, "compat_summary": (m.compat or {}).get("overall"),
        "created_at": m.created_at, "updated_at": m.updated_at, "last_used_at": m.last_used_at,
    }
    if m.backend in ("ollama", "vllm") and "chat" in (m.capabilities or []):
        from . import llm

        d["context_window"] = llm.context_tokens(m)
        d["tools_enabled"] = llm.supports_tools(m)
    if include_config:
        d["runtime_params"] = m.runtime_config.params if m.runtime_config else {}
        d["compat"] = m.compat
        d["path"] = m.path
    return d


def set_runtime_params(db: Session, model: RegisteredModel, params: dict) -> None:
    if model.runtime_config is None:
        model.runtime_config = ModelRuntimeConfig(params=params)
    else:
        model.runtime_config.params = params


def history(db: Session, model: RegisteredModel, event: str, details: dict | None = None, actor_id=None,
            frm: str | None = None, to: str | None = None) -> None:
    db.add(ModelInstallation(model_id=model.id, event=event, details=details or {}, actor_id=actor_id,
                             from_status=frm, to_status=to))


def users_of_model(db: Session, model: RegisteredModel) -> list[str]:
    """Active jobs referencing the model (blocks deletion)."""
    rows = db.scalars(select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.preparing, JobStatus.running]))).all()
    out = []
    for j in rows:
        p = j.params or {}
        ids = {p.get("model_id"), *(p.get("model_ids") or [])}
        if str(model.id) in ids:
            out.append(str(j.id))
    return out


def dependencies(db: Session, model: RegisteredModel) -> dict:
    jobs = users_of_model(db, model)
    inst = db.scalar(select(RuntimeInstance).where(RuntimeInstance.model_id == model.id))
    shared_blobs = 0
    if model.source == "hf" and model.repo_id:
        shared_blobs = db.scalar(select(func.count()).select_from(RegisteredModel).where(
            RegisteredModel.repo_id == model.repo_id, RegisteredModel.id != model.id,
            RegisteredModel.source == "hf")) or 0
    return {"active_jobs": jobs, "loaded": bool(inst), "runtime_state": inst.state if inst else None,
            "other_registrations_same_repo": shared_blobs, "engine": model.backend,
            "comfyui_links": (model.compat or {}).get("comfyui_links", [])}


def hf_snapshot_dir(model: RegisteredModel) -> Path | None:
    if not model.path:
        return None
    return get_settings().hf_cache_dir / model.path


def hf_config(model: RegisteredModel) -> dict:
    """The snapshot's config.json ({} when missing or unreadable)."""
    snap = hf_snapshot_dir(model)
    try:
        return json.loads((snap / "config.json").read_text()) if snap else {}
    except (OSError, ValueError):
        return {}


def check_consistency(model: RegisteredModel, deep: bool = False) -> dict:
    """Verify files listed in the model version exist with the expected size (and sha256 if deep)."""
    if model.source != "hf":
        return {"checked": False, "reason": f"source '{model.source}' is verified by its engine"}
    snap = hf_snapshot_dir(model)
    if not snap or not snap.exists():
        return {"ok": False, "missing": ["<snapshot directory>"], "checked": True}
    files = (model.compat or {}).get("files") or []
    missing, mismatched, verified = [], [], 0
    for f in files:
        p = snap / f["path"]
        if not p.exists():
            missing.append(f["path"])
            continue
        size = os.path.getsize(p)
        if f.get("size") and size != f["size"]:
            mismatched.append({"path": f["path"], "expected": f["size"], "actual": size})
            continue
        if deep and f.get("sha256"):
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(8 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != f["sha256"]:
                mismatched.append({"path": f["path"], "reason": "sha256 mismatch"})
                continue
        verified += 1
    return {"ok": not missing and not mismatched, "checked": True, "files": len(files), "verified": verified,
            "missing": missing, "mismatched": mismatched, "deep": deep}


def can_use(model: RegisteredModel, user_id: uuid.UUID, is_admin: bool, shared_ids: set) -> bool:
    if is_admin or model.owner_id == user_id or model.visibility == Visibility.public:
        return True
    return model.visibility == Visibility.shared and model.id in shared_ids


def delete_files(db: Session, model: RegisteredModel) -> dict:
    """Remove weights only when no other registration references the same snapshot."""
    s = get_settings()
    removed = []
    if model.source == "hf" and model.path:
        others = db.scalar(select(func.count()).select_from(RegisteredModel).where(
            RegisteredModel.path == model.path, RegisteredModel.id != model.id)) or 0
        for link in (model.compat or {}).get("comfyui_links", []):
            p = s.models_dir / "comfyui" / link
            if p.exists():
                p.unlink()
                removed.append(str(p))
        if others == 0:
            from huggingface_hub import scan_cache_dir

            info = scan_cache_dir(s.hf_cache_dir)
            revs = [r.commit_hash for repo in info.repos if repo.repo_id == model.repo_id for r in repo.revisions
                    if r.commit_hash == model.revision]
            if revs:
                strategy = info.delete_revisions(*revs)
                strategy.execute()
                removed.append(f"hf:{model.repo_id}@{model.revision} ({strategy.expected_freed_size_str})")
    elif model.source == "ollama" and model.engine_ref:
        from ..engines import get_engine

        get_engine("ollama").delete(model.engine_ref)  # type: ignore[attr-defined]
        removed.append(f"ollama:{model.engine_ref}")
    elif model.source == "custom" and model.path:
        p = s.custom_models_dir / model.path
        if p.exists() and s.custom_models_dir.resolve() in p.resolve().parents:
            import shutil

            shutil.rmtree(p)
            removed.append(str(p))
    return {"removed": removed}


def ensure_version(db: Session, model: RegisteredModel, files: list[dict], verified: bool) -> None:
    if not model.revision:
        return
    v = db.scalar(select(ModelVersion).where(ModelVersion.model_id == model.id, ModelVersion.revision == model.revision))
    if v is None:
        v = ModelVersion(model_id=model.id, revision=model.revision)
        db.add(v)
    v.files = files
    v.size_bytes = sum(f.get("size") or 0 for f in files)
    v.verified = verified


def mark(db: Session, model: RegisteredModel, status: ModelStatus, msg: str | None = None, actor_id=None,
         event: str | None = None, details: dict | None = None) -> None:
    old = model.status.value if model.status else None
    model.status = status
    model.status_message = msg
    history(db, model, event or f"status.{status.value}", details, actor_id, old, status.value)
