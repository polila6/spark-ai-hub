"""Hugging Face explorer, installation assistant and download manager."""
from __future__ import annotations

import fnmatch
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import Principal, current_principal, require_admin, require_manager, require_researcher
from ..models import (
    DownloadStatus,
    DownloadTask,
    Job,
    ModelStatus,
    RegisteredModel,
    Role,
    SchedClass,
    User,
    Visibility,
)
from ..services import compat, hf, notify, registry, scheduler, storage
from ..services.jobs import create_job, request_cancel
from ..settings_store import get_setting

router = APIRouter(tags=["huggingface"])

LICENSE_ATTENTION = {"llama2", "llama3", "llama3.1", "llama3.2", "llama3.3", "llama4", "gemma", "cc-by-nc-4.0",
                     "cc-by-nc-sa-4.0", "cc-by-nc-nd-4.0", "creativeml-openrail-m", "openrail", "openrail++",
                     "other", "unknown", "bigscience-openrail-m", "deepfloyd-if-license", "flux-1-dev-non-commercial-license"}


def _token(db: Session) -> str | None:
    return get_setting(db, "hf_token")


def _hferr(e: hf.HfError):
    return HTTPException(e.status, detail={"code": e.code, "message": e.message})


@router.get("/api/v1/huggingface/search")
def search(p: Principal = Depends(current_principal), db: Session = Depends(get_db),
           q: str | None = Query(None, max_length=200), task: str | None = None, library: str | None = None,
           author: str | None = None, quantization: str | None = None, architecture: str | None = None,
           sort: str = Query("downloads", pattern="^(downloads|likes|lastModified|trendingScore|createdAt)$"),
           limit: int = Query(30, ge=1, le=100), gated: bool | None = None):
    tags = []
    if quantization:
        tags.append({"gguf": "gguf", "awq": "awq", "gptq": "gptq", "fp8": "fp8", "nvfp4": "nvfp4",
                     "bnb": "bitsandbytes", "mxfp4": "mxfp4"}.get(quantization, quantization))
    if architecture:
        tags.append(architecture)
    if q and (q.startswith("http") or q.startswith("hf.co/")):
        try:
            q = hf.normalise_repo_id(q)
        except hf.HfError:
            pass
    try:
        return hf.search(_token(db), query=q, task=task, library=library, author=author, tags=tags, sort=sort,
                         limit=limit, gated=gated)
    except hf.HfError as e:
        raise _hferr(e)


def _analysis(db: Session, info: dict, context: int | None = None, concurrency: int = 1) -> dict:
    b = scheduler.budget(db)
    verified = set()
    for m in db.scalars(select(RegisteredModel).where(RegisteredModel.repo_id == info["id"])):
        verified |= set((m.compat or {}).get("verified_engines") or [])
    return compat.analyse(
        repo_id=info["id"], config=info.get("config"), files=info["files"], tags=info.get("tags") or [],
        pipeline_tag=info.get("pipeline_tag"), params_b=(info["parameters"] / 1e9) if info.get("parameters") else None,
        budget_gb=b["schedulable_gb"], free_disk_gb=storage.available_for_downloads(db) / 1e9,
        verified_engines=verified, library=info.get("library"), context=context, concurrency=concurrency)


def _security(info: dict) -> list[dict]:
    warnings = []
    lic = (info.get("license") or "unknown").lower()
    if info.get("gated"):
        warnings.append({"level": "warning", "code": "gated", "message": "Gated repository: requires accepting terms and a token"})
    if lic in LICENSE_ATTENTION or lic.startswith(("cc-by-nc", "llama")):
        warnings.append({"level": "warning", "code": "license", "message": f"Licence '{lic}' requires review before use"})
    names = [f["path"] for f in info["files"]]
    if any(n.endswith(".py") for n in names) or (info.get("config") or {}).get("auto_map"):
        warnings.append({"level": "danger", "code": "custom_code",
                         "message": "Contains custom Python code; it will NOT run unless an admin approves trust_remote_code"})
    pick = [n for n in names if n.endswith(compat.PICKLE_EXT)]
    if pick:
        warnings.append({"level": "danger" if not any(n.endswith((".safetensors", ".gguf")) for n in names) else "info",
                         "code": "pickle", "message": f"{len(pick)} pickle-based files (.bin/.pt/.ckpt) — excluded from downloads by default"})
    sec = info.get("security")
    if isinstance(sec, dict) and sec.get("scansDone") and sec.get("filesWithIssues"):
        flagged = sec["filesWithIssues"]
        desc = ", ".join(f"{f.get('path')} ({f.get('level')})" for f in flagged[:6])
        only_st = all(str(f.get("path", "")).endswith((".safetensors", ".gguf")) for f in flagged)
        warnings.append({"level": "warning" if only_st else "danger", "code": "hub_scan",
                         "message": f"Hugging Face security scan reported: {desc}."
                                    + (" Safetensors/GGUF files are not executable; review on huggingface.co." if only_st else "")})
    return warnings


@router.get("/api/v1/huggingface/models/{owner}/{name}")
def model_details(owner: str, name: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                  revision: str | None = None):
    try:
        repo = hf.normalise_repo_id(f"{owner}/{name}")
        info = hf.model_info(_token(db), repo, revision)
    except hf.HfError as e:
        raise _hferr(e)
    info["analysis"] = _analysis(db, info)
    info["warnings"] = _security(info)
    info["registered"] = [registry.model_public(m) for m in db.scalars(
        select(RegisteredModel).where(RegisteredModel.repo_id == repo))]
    return info


@router.get("/api/v1/huggingface/models/{owner}/{name}/card")
def model_card(owner: str, name: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
               revision: str | None = None):
    return {"markdown": hf.model_card(_token(db), f"{owner}/{name}", revision)}


@router.get("/api/v1/huggingface/models/{owner}/{name}/refs")
def model_refs(owner: str, name: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    try:
        repo = hf.normalise_repo_id(f"{owner}/{name}")
        return {"refs": hf.refs(_token(db), repo), "commits": hf.commits(_token(db), repo)}
    except hf.HfError as e:
        raise _hferr(e)


class PlanIn(BaseModel):
    repo_id: str = Field(max_length=200)
    revision: str | None = Field(default=None, max_length=64)
    engine: str | None = Field(default=None, pattern="^(vllm|ollama|comfyui|mlworker)$")
    gguf_quant: str | None = Field(default=None, max_length=32)
    context_length: int | None = Field(default=None, ge=256, le=1_048_576)
    concurrency: int = Field(default=1, ge=1, le=64)
    include_patterns: list[str] | None = None


def _select_files(info: dict, engine: str, gguf_quant: str | None, include: list[str] | None) -> list[dict]:
    files = info["files"]
    if include:
        return [f for f in files if any(fnmatch.fnmatch(f["path"], pat) for pat in include)]
    if engine == "vllm" or engine == "mlworker":
        has_st = any(f["path"].endswith(".safetensors") for f in files)
        keep = []
        for f in files:
            n = f["path"]
            if n.endswith(compat.PICKLE_EXT) and has_st:
                continue
            if n.endswith((".gguf", ".onnx", ".msgpack", ".h5", ".ot")) or n.startswith(("onnx/", "openvino/")):
                continue
            if n.endswith(compat.PICKLE_EXT) and not has_st:
                continue  # never download pickles by default
            keep.append(f)
        return keep
    if engine == "comfyui":
        return [f for f in files if f["path"].endswith(".safetensors") and "/" not in f["path"]][:1]
    return files


@router.post("/api/v1/huggingface/plan")
def plan(body: PlanIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    """Installation assistant: inspect → formats → engines → memory/disk → plan (no side effects)."""
    try:
        repo = hf.normalise_repo_id(body.repo_id)
        info = hf.model_info(_token(db), repo, body.revision)
    except hf.HfError as e:
        raise _hferr(e)
    analysis = _analysis(db, info, body.context_length, body.concurrency)
    engine = body.engine or analysis.get("recommended_engine")
    ev = next((e for e in analysis["engines"] if e["engine"] == engine), None)
    if engine == "ollama":
        quant = (body.gguf_quant or (ev or {}).get("recommended_params", {}).get("quant") or "Q4_K_M").upper()
        variants = (ev or {}).get("recommended_params", {}).get("variants", {})
        size = int((variants.get(quant.lower()) or 0) * 1e9)
        files = [f for f in info["files"] if f["path"].endswith(".gguf") and quant.lower() in f["path"].lower()]
        size = size or sum(f.get("size") or 0 for f in files)
        engine_ref = f"hf.co/{repo}:{quant}"
    else:
        files = _select_files(info, engine or "", None, body.include_patterns)
        size = sum(f.get("size") or 0 for f in files)
        engine_ref = None
    avail = storage.available_for_downloads(db)
    steps = [
        {"step": "inspect", "status": "done", "detail": f"{len(info['files'])} files, sha {info['sha'][:12]}"},
        {"step": "formats", "status": "done", "detail": analysis.get("quantization") or "unknown precision"},
        {"step": "engine", "status": "done" if engine else "blocked", "detail": engine or "no compatible engine"},
        {"step": "memory", "status": "done", "detail": (ev or {}).get("estimate", {}).get("total_gb")},
        {"step": "disk", "status": "ok" if size <= avail else "blocked",
         "detail": f"{size / 1e9:.2f} GB needed, {max(avail, 0) / 1e9:.0f} GB available above threshold"},
    ]
    blockers = []
    if not engine:
        blockers.append("No engine adapter supports this repository")
    if size > avail:
        blockers.append("Insufficient disk space above the safety threshold")
    if ev and ev["verdict"] == "unsupported":
        blockers.append("Engine verdict: unsupported — " + "; ".join(ev["reasons"][-2:]))
    if info.get("gated") and not _token(db):
        blockers.append("Gated repository and no Hugging Face token configured (Settings → Hugging Face)")
    return {
        "repo_id": repo, "revision": info["sha"], "requested_revision": body.revision or "main",
        "engine": engine, "engine_ref": engine_ref, "verdict": ev["verdict"] if ev else analysis["overall"],
        "analysis": analysis, "engine_analysis": ev, "files": files, "download_bytes": size,
        "warnings": _security(info), "steps": steps, "blockers": blockers,
        "requires_admin_approval": p.user.role not in (Role.admin, Role.manager),
        "license": info.get("license"), "gated": info.get("gated"), "pipeline_tag": info.get("pipeline_tag"),
    }


class InstallIn(PlanIn):
    alias: str | None = Field(default=None, max_length=100)
    visibility: Visibility = Visibility.private
    smoke_test: bool = True
    accept_license: bool = False
    reason: str = Field(default="", max_length=2000)  # why the researcher needs it (shown to the reviewer)


def _modality(pipeline_tag: str | None, engine: str) -> str:
    if engine == "comfyui":
        return "video" if pipeline_tag in ("text-to-video", "image-to-video") else "image"
    if pipeline_tag in ("feature-extraction", "sentence-similarity"):
        return "embedding"
    if pipeline_tag in ("automatic-speech-recognition", "text-to-speech", "text-to-audio"):
        return "audio"
    if pipeline_tag == "image-classification":
        return "vision"
    return "text"


def _caps(engine: str, modality: str, pipeline_tag: str | None, ev: dict | None) -> list[str]:
    if modality == "embedding":
        return ["embeddings"]
    if engine == "vllm":
        caps = ["chat", "completion", "streaming"]
        if pipeline_tag == "image-text-to-text":
            caps.append("vision")
        return caps
    if engine == "mlworker":
        if pipeline_tag == "image-classification":
            return ["image_classification"]
        return ["asr"] if pipeline_tag == "automatic-speech-recognition" else ["tts"]
    if engine == "comfyui":
        return [modality]
    return []


@router.post("/api/v1/huggingface/install")
def install(body: InstallIn, request: Request, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    pl = plan(PlanIn(**body.model_dump(include=set(PlanIn.model_fields))), p, db)
    if pl["blockers"]:
        raise HTTPException(409, detail={"code": "blocked", "message": "; ".join(pl["blockers"]), "plan": pl})
    if any(w["code"] == "license" for w in pl["warnings"]) and not body.accept_license:
        raise HTTPException(409, detail={"code": "license_ack_required",
                                         "message": "Review the licence and confirm accept_license"})
    if body.visibility == Visibility.public and not p.is_manager:
        raise HTTPException(403, detail={"code": "admin_required", "message": "Only admins/managers publish shared models"})
    engine = pl["engine"]
    ev = pl["engine_analysis"] or {}
    info = hf.model_info(_token(db), pl["repo_id"], pl["revision"])
    existing = db.scalar(select(RegisteredModel).where(RegisteredModel.repo_id == pl["repo_id"],
                                                       RegisteredModel.revision == pl["revision"],
                                                       RegisteredModel.backend == engine,
                                                       RegisteredModel.engine_ref == pl["engine_ref"]))
    # A rejected or failed request that never downloaded anything can be requested again.
    if existing and existing.status not in (ModelStatus.failed, ModelStatus.discovered) and not (
            existing.status == ModelStatus.disabled and not existing.path):
        raise HTTPException(409, detail={"code": "already_registered", "message": f"Already registered as {existing.alias}",
                                         "model_id": str(existing.id)})
    try:
        storage.ensure_download_space(db, pl["download_bytes"])
    except storage.StorageError as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    modality = _modality(pl["pipeline_tag"], engine)
    cfg = info.get("config") or {}
    est = (ev.get("estimate") or {})
    model = existing or RegisteredModel(alias=registry.make_alias(db, body.alias or pl["repo_id"].split("/")[-1]),
                                        source="ollama" if engine == "ollama" else "hf")
    model.display_name = pl["repo_id"]
    model.repo_id = pl["repo_id"]
    model.revision = pl["revision"]
    model.task = pl["pipeline_tag"]
    model.modality = modality
    model.architecture = ", ".join(cfg.get("architectures") or []) or None
    model.quantization = pl["analysis"].get("quantization")
    model.format = "gguf" if engine == "ollama" else "safetensors"
    model.parameters_b = round(info["parameters"] / 1e9, 3) if info.get("parameters") else None
    model.context_length = cfg.get("max_position_embeddings") or (cfg.get("text_config") or {}).get("max_position_embeddings")
    model.license = info.get("license")
    model.gated = bool(info.get("gated"))
    model.custom_code = pl["analysis"].get("custom_code", False)
    model.backend = engine
    model.engine_ref = pl["engine_ref"]
    model.status = ModelStatus.discovered
    model.owner_id = p.id
    model.visibility = body.visibility
    model.capabilities = _caps(engine, modality, pl["pipeline_tag"], ev)
    model.estimated_memory_gb = est.get("total_gb")
    model.size_bytes = pl["download_bytes"]
    model.compat = {"overall": pl["verdict"], "analysis": pl["analysis"], "files": pl["files"],
                    "verified_engines": []}
    if engine == "comfyui":
        model.compat["comfyui_kind"] = "checkpoints"
        model.compat["comfyui_files"] = [f["path"] for f in pl["files"]]
    if not existing:
        db.add(model)
    db.flush()
    rp = dict(ev.get("recommended_params") or {})
    if engine == "vllm":
        registry.set_runtime_params(db, model, {k: v for k, v in rp.items() if v is not None and k in
                                                ("max_model_len", "gpu_memory_utilization", "max_num_seqs",
                                                 "enable_tool_calling", "tool_call_parser", "reasoning_parser",
                                                 "speculative_tokens", "profile", "kv_cache_dtype", "load_timeout_s")})
    task = DownloadTask(model_id=model.id, requested_by=p.id, kind="ollama" if engine == "ollama" else "hf",
                        repo_id=pl["repo_id"], revision=pl["revision"],
                        allow_patterns=None if engine == "ollama" else [f["path"] for f in pl["files"]],
                        total_bytes=pl["download_bytes"], reserved_bytes=pl["download_bytes"],
                        status=DownloadStatus.pending, request_reason=body.reason.strip() or None)
    db.add(task)
    registry.history(db, model, "install.requested", {"engine": engine, "bytes": pl["download_bytes"],
                                                      "by": p.user.username}, p.id)
    db.commit()
    job_id = None
    if p.is_manager:
        job_id = _start_download(db, task, p.user, body.smoke_test)
    else:
        size_gb = pl["download_bytes"] / 1e9
        notify.notify_managers(db, "model_request", f"Solicitud de modelo: {pl['repo_id']}",
                               f"{p.user.display_name or p.user.username} · {size_gb:.1f} GB · {engine}"
                               + (f" — {task.request_reason}" if task.request_reason else ""),
                               link="/requests", data={"download_task_id": str(task.id)})
        db.commit()
    audit(db, "models.install_request", actor=p.user, request=request, target_type="model", target_id=str(model.id),
          details={"repo": pl["repo_id"], "revision": pl["revision"], "engine": engine, "auto_started": bool(job_id)})
    return {"model_id": str(model.id), "alias": model.alias, "download_task_id": str(task.id),
            "job_id": job_id, "status": "started" if job_id else "awaiting_admin_approval"}


def _start_download(db: Session, task: DownloadTask, user: User, smoke_test: bool = True, use_token: bool = True) -> str:
    model = db.get(RegisteredModel, task.model_id)
    jtype = "ollama_pull" if task.kind == "ollama" else "hf_download"
    j = create_job(db, user, jtype, {"download_task_id": str(task.id), "model_id": str(task.model_id),
                                     "smoke_test": smoke_test, "use_token": use_token},
                   title=f"Download {task.repo_id}", sched_class=SchedClass.maintenance, max_attempts=3)
    task.job_id = j.id
    registry.mark(db, model, ModelStatus.downloading, "Queued for download", actor_id=user.id)
    db.commit()
    return str(j.id)


# ------------------------------------------------------------------ downloads


def _uname(db: Session, uid) -> str | None:
    u = db.get(User, uid) if uid else None
    return (u.display_name or u.username) if u else None


def _task_public(t: DownloadTask, db: Session) -> dict:
    m = db.get(RegisteredModel, t.model_id) if t.model_id else None
    return {"id": str(t.id), "model_id": str(t.model_id) if t.model_id else None, "alias": m.alias if m else None,
            "kind": t.kind, "repo_id": t.repo_id, "revision": t.revision, "status": t.status.value,
            "total_bytes": t.total_bytes, "downloaded_bytes": t.downloaded_bytes, "speed_bps": t.speed_bps,
            "error": t.error, "verification": t.verification, "job_id": str(t.job_id) if t.job_id else None,
            "requested_by": str(t.requested_by) if t.requested_by else None,
            "awaiting_approval": t.status == DownloadStatus.pending and t.job_id is None,
            "request_reason": t.request_reason, "review_comment": t.review_comment, "reviewed_at": t.reviewed_at,
            "requested_by_name": _uname(db, t.requested_by), "reviewed_by_name": _uname(db, t.reviewed_by),
            "model_visibility": m.visibility.value if m else None, "estimated_memory_gb": m.estimated_memory_gb if m else None,
            "backend": m.backend if m else None, "license": m.license if m else None,
            "created_at": t.created_at, "updated_at": t.updated_at}


@router.get("/api/v1/downloads")
def list_downloads(p: Principal = Depends(current_principal), db: Session = Depends(get_db), pending: bool = False):
    q = select(DownloadTask).order_by(DownloadTask.created_at.desc()).limit(200)
    if pending:
        q = q.where(DownloadTask.status == DownloadStatus.pending, DownloadTask.job_id.is_(None))
    if not p.is_manager:
        q = q.where(DownloadTask.requested_by == p.id)
    return [_task_public(t, db) for t in db.scalars(q)]


def _get_task(db, p, task_id) -> DownloadTask:
    t = db.get(DownloadTask, task_id)
    if not t or (not p.is_manager and t.requested_by != p.id):
        raise HTTPException(404, detail={"code": "not_found", "message": "Download not found"})
    return t


class ReviewIn(BaseModel):
    comment: str = Field(default="", max_length=2000)
    visibility: Visibility | None = None  # approve: publish to everyone (public) or keep it for the requester


@router.post("/api/v1/downloads/{task_id}/approve")
def approve(task_id: uuid.UUID, request: Request, body: ReviewIn | None = None, p: Principal = Depends(require_manager),
            db: Session = Depends(get_db)):
    body = body or ReviewIn()
    t = _get_task(db, p, task_id)
    if t.job_id is not None or t.status != DownloadStatus.pending:
        raise HTTPException(409, detail={"code": "not_pending", "message": "Download is not awaiting approval"})
    try:
        storage.ensure_download_space(db, t.reserved_bytes or 0)
    except storage.StorageError as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    m = db.get(RegisteredModel, t.model_id) if t.model_id else None
    if m and body.visibility:
        m.visibility = body.visibility
    t.reviewed_by, t.reviewed_at, t.review_comment = p.id, datetime.now(timezone.utc), body.comment or None
    requester = db.get(User, t.requested_by) if t.requested_by else p.user
    job_id = _start_download(db, t, requester or p.user)
    if requester and requester.id != p.id:
        notify.notify(db, requester.id, "model_request", f"Solicitud aprobada: {t.repo_id}",
                      "La descarga ha empezado; te avisaremos cuando el modelo esté listo."
                      + (f"\n{body.comment}" if body.comment else ""), link="/models")
        db.commit()
    audit(db, "downloads.approve", actor=p.user, request=request, target_type="download", target_id=str(t.id),
          details={"visibility": body.visibility.value if body.visibility else None})
    return {"job_id": job_id}


@router.post("/api/v1/downloads/{task_id}/reject")
def reject(task_id: uuid.UUID, body: ReviewIn, request: Request, p: Principal = Depends(require_manager),
           db: Session = Depends(get_db)):
    t = _get_task(db, p, task_id)
    if t.job_id is not None or t.status != DownloadStatus.pending:
        raise HTTPException(409, detail={"code": "not_pending", "message": "Download is not awaiting approval"})
    t.status = DownloadStatus.rejected
    t.reviewed_by, t.reviewed_at, t.review_comment = p.id, datetime.now(timezone.utc), body.comment or None
    m = db.get(RegisteredModel, t.model_id) if t.model_id else None
    if m and m.status == ModelStatus.discovered:
        registry.mark(db, m, ModelStatus.disabled, f"Request rejected: {body.comment or '-'}", actor_id=p.id)
    if t.requested_by and t.requested_by != p.id:
        notify.notify(db, t.requested_by, "model_request", f"Solicitud no aprobada: {t.repo_id}",
                      body.comment or "", link="/requests")
    db.commit()
    audit(db, "downloads.reject", actor=p.user, request=request, target_type="download", target_id=str(t.id))
    return {"ok": True}


@router.post("/api/v1/downloads/{task_id}/pause")
def pause(task_id: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    t = _get_task(db, p, task_id)
    if t.kind != "hf":
        raise HTTPException(400, detail={"code": "not_supported", "message": "Pause is supported for Hugging Face downloads; cancel Ollama pulls instead"})
    if t.status != DownloadStatus.running:
        raise HTTPException(409, detail={"code": "not_running", "message": "Download is not running"})
    t.status = DownloadStatus.paused
    db.commit()
    return {"ok": True}


@router.post("/api/v1/downloads/{task_id}/resume")
def resume(task_id: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    t = _get_task(db, p, task_id)
    if t.status not in (DownloadStatus.paused, DownloadStatus.failed, DownloadStatus.cancelled):
        raise HTTPException(409, detail={"code": "not_resumable", "message": "Download cannot be resumed in its state"})
    if t.job_id is None and not p.is_manager:
        raise HTTPException(403, detail={"code": "approval_required", "message": "Awaiting admin approval"})
    t.status = DownloadStatus.pending
    db.commit()
    return {"job_id": _start_download(db, t, p.user)}


@router.post("/api/v1/downloads/{task_id}/cancel")
def cancel(task_id: uuid.UUID, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    t = _get_task(db, p, task_id)
    if t.job_id:
        j = db.get(Job, t.job_id)
        if j:
            request_cancel(db, j)
    if t.status in (DownloadStatus.pending, DownloadStatus.paused):
        t.status = DownloadStatus.cancelled
        m = db.get(RegisteredModel, t.model_id) if t.model_id else None
        if m and m.status in (ModelStatus.discovered, ModelStatus.downloading):
            registry.mark(db, m, ModelStatus.discovered, "Download cancelled", actor_id=p.id)
    t.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ recommendations


class RecommendIn(BaseModel):
    task: str = Field(default="text-generation", max_length=64)
    max_memory_gb: float | None = Field(default=None, ge=1)
    max_disk_gb: float | None = Field(default=None, ge=0.1)
    min_context: int | None = Field(default=None, ge=256)
    speed: str = Field(default="balanced", pattern="^(fast|balanced|quality)$")
    quantization: list[str] | None = None
    required_capabilities: list[str] | None = None
    allow_experimental: bool = False
    query: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=30)


@router.post("/api/v1/recommendations")
def recommend(body: RecommendIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Explainable recommendations: deterministic rules + local measurements; no LLM decides."""
    from ..models import BenchmarkRun

    token = _token(db)
    budget = scheduler.budget(db)["schedulable_gb"]
    mem_cap = min(body.max_memory_gb or budget, budget)
    cands: dict[str, dict] = {}
    # 1. NVIDIA documented recipes (strong prior), 2. Hub search by task.
    for rid in sorted(compat.NVIDIA_SPARK_VLLM):
        cands[rid] = {"source": "nvidia_spark_matrix"}
    try:
        for m in hf.search(token, query=body.query, task=body.task, library=None, author=None, tags=[],
                           sort="downloads", limit=40):
            cands.setdefault(m["id"], {"source": "hub_search"})
    except hf.HfError:
        pass
    local_perf: dict[str, dict] = {}
    for run in db.scalars(select(BenchmarkRun).where(BenchmarkRun.status == "completed", BenchmarkRun.kind == "llm_perf")):
        rid = (run.model_snapshot or {}).get("repo_id")
        if rid:
            local_perf[rid] = run.summary
    results = []
    for rid, meta in list(cands.items())[:70]:
        try:
            info = hf.model_info(token, rid)
        except hf.HfError:
            continue
        if body.task and info.get("pipeline_tag") not in (body.task, None) and meta["source"] != "nvidia_spark_matrix":
            continue
        if body.task and meta["source"] == "nvidia_spark_matrix" and info.get("pipeline_tag") not in (body.task, "image-text-to-text", None):
            continue
        a = _analysis(db, info, body.min_context)
        if not a["engines"]:
            continue
        ev = min(a["engines"], key=lambda e: {"verified": 0, "likely": 1, "experimental": 2, "insufficient": 3,
                                              "unsupported": 4}[e["verdict"]])
        reasons = list(ev["reasons"])
        lim = list(ev["limitations"])
        est = ev["estimate"].get("total_gb")
        disk = ev["estimate"].get("disk_gb") or a["disk_gb_total"]
        if ev["verdict"] == "unsupported" or (ev["verdict"] == "experimental" and not body.allow_experimental):
            continue
        if est and est > mem_cap:
            continue
        if body.max_disk_gb and disk and disk > body.max_disk_gb:
            continue
        if body.quantization and (a.get("quantization") or "").lower() not in [q.lower() for q in body.quantization]:
            continue
        ctx = (info.get("config") or {}).get("max_position_embeddings")
        if body.min_context and ctx and ctx < body.min_context:
            continue
        if body.required_capabilities and "tools" in body.required_capabilities and ev["engine"] != "vllm":
            lim.append("Tool calling depends on chat template; verify after installation")
        score = {"verified": 1.0, "likely": 0.75, "experimental": 0.4, "insufficient": 0.2}[ev["verdict"]] * ev["confidence"]
        if meta["source"] == "nvidia_spark_matrix":
            score += 0.15
        if est:
            if body.speed == "fast":
                score += 0.2 * (1 - est / mem_cap)
            elif body.speed == "quality":
                score += 0.2 * (est / mem_cap)
        perf = local_perf.get(rid)
        if perf:
            reasons.append(f"Local benchmark: {perf.get('tokens_per_s')} tok/s, TTFT p50 {perf.get('ttft_ms_p50')} ms")
            score += 0.1
        results.append({"repo_id": rid, "engine": ev["engine"], "verdict": ev["verdict"], "confidence": ev["confidence"],
                        "score": round(score, 3), "estimated_memory_gb": est, "disk_gb": disk,
                        "quantization": a.get("quantization"), "parameters_b": round(info["parameters"] / 1e9, 2) if info.get("parameters") else None,
                        "license": info.get("license"), "gated": info.get("gated"), "downloads": info.get("downloads"),
                        "reasons": reasons, "limitations": lim, "source": meta["source"],
                        "install": {"engine": ev["engine"], "params": ev["recommended_params"]}})
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"budget_gb": budget, "memory_cap_gb": mem_cap, "results": results[:body.limit],
            "method": "deterministic rules (formats, architectures, memory, disk, NVIDIA Spark matrix, local benchmarks)"}
