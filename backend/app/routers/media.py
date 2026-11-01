"""Image / Video / Audio studios and artifact access."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_admin, require_manager, require_researcher, visible_filter
from ..models import Artifact, DownloadStatus, DownloadTask, Job, ModelStatus, RegisteredModel, SchedClass, Visibility
from ..services import hf, registry, storage, workflows
from ..services.jobs import JobError, create_job
from ..services.uploads import AUDIO_MAGIC, IMAGE_MAGIC, UploadError, safe_name, save_stream, sniff
from ..settings_store import get_setting

router = APIRouter(prefix="/api/v1/media", tags=["media"])


def _art_pub(a: Artifact) -> dict:
    return {"id": str(a.id), "kind": a.kind, "filename": a.filename, "mime": a.mime, "size_bytes": a.size_bytes,
            "sha256": a.sha256, "meta": a.meta, "visibility": a.visibility.value,
            "job_id": str(a.job_id) if a.job_id else None, "owner_id": str(a.owner_id) if a.owner_id else None,
            "created_at": a.created_at, "url": f"/api/v1/media/artifacts/{a.id}/file"}


def _comfy_nodes() -> set[str] | None:
    from ..engines import get_engine

    eng = get_engine("comfyui")
    if not eng.is_running():  # type: ignore[attr-defined]
        return None
    try:
        import httpx

        with httpx.Client(base_url=get_settings().comfyui_url, timeout=20) as c:
            return set(c.get("/object_info").json().keys())
    except Exception:  # noqa: BLE001
        return None


@router.get("/workflows")
def list_workflows(_: Principal = Depends(current_principal), kind: str | None = None):
    files = workflows.installed_files()
    nodes = _comfy_nodes()
    out = [workflows.public(w, files, nodes) for w in workflows.WORKFLOWS.values() if not kind or w.kind == kind]
    return {"workflows": out, "node_check": nodes is not None}


@router.post("/workflows/{wf_id}/install")
def install_workflow_models(wf_id: str, request: Request, p: Principal = Depends(require_manager),
                            db: Session = Depends(get_db)):
    """Admin-approved installation of the model files a workflow needs (through the download manager)."""
    from .huggingface import _start_download

    wf = workflows.WORKFLOWS.get(wf_id)
    if not wf:
        raise not_found("workflow")
    missing = workflows.missing_models(wf)
    need = int(sum(m.size_gb for m in missing) * 1e9)
    try:
        storage.ensure_download_space(db, need)
    except storage.StorageError as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    token = get_setting(db, "hf_token")
    jobs = []
    for mf in missing:
        info = hf.model_info(token, mf.repo_id)
        f = next((x for x in info["files"] if x["path"] == mf.repo_file), None)
        if not f:
            raise HTTPException(502, detail={"code": "file_missing", "message": f"{mf.repo_file} not in {mf.repo_id}"})
        busy = db.scalar(select(DownloadTask).where(DownloadTask.repo_id == mf.repo_id,
                                                    DownloadTask.allow_patterns == [mf.repo_file],
                                                    DownloadTask.status.in_([DownloadStatus.running, DownloadStatus.pending])))
        if busy:
            continue
        m = RegisteredModel(alias=registry.make_alias(db, Path(mf.filename).stem), display_name=mf.filename,
                            source="hf", repo_id=mf.repo_id, revision=info["sha"], task=info.get("pipeline_tag"),
                            modality=wf.kind, format="safetensors", license=mf.license, backend="comfyui",
                            status=ModelStatus.discovered, owner_id=p.id, visibility=Visibility.public,
                            capabilities=[wf.kind], size_bytes=f["size"],
                            compat={"overall": "experimental", "files": [f], "comfyui_kind": mf.kind,
                                    "comfyui_files": [mf.repo_file], "workflow": wf.id, "verified_engines": []})
        db.add(m)
        db.flush()
        t = DownloadTask(model_id=m.id, requested_by=p.id, kind="hf", repo_id=mf.repo_id, revision=info["sha"],
                         allow_patterns=[mf.repo_file], total_bytes=f["size"], reserved_bytes=f["size"])
        db.add(t)
        db.commit()
        jobs.append(_start_download(db, t, p.user, smoke_test=False))
    audit(db, "workflows.install", actor=p.user, request=request, target_type="workflow", target_id=wf_id,
          details={"files": [m.filename for m in missing], "bytes": need})
    return {"jobs": jobs, "files": [m.filename for m in missing], "bytes": need}


@router.post("/uploads")
async def upload_media(file: UploadFile = File(...), kind: str = "image", p: Principal = Depends(require_researcher),
                       db: Session = Depends(get_db)):
    """Upload an input image or audio file (stored as a private artifact)."""
    if kind not in ("image", "audio"):
        raise HTTPException(422, detail={"code": "invalid_kind", "message": "kind must be image|audio"})
    s = get_settings()
    uid = uuid.uuid4()
    tmp = s.outputs_dir / str(p.id) / "uploads" / f"{uid}.tmp"
    try:
        storage.ensure_user_quota(db, p.user, 0)
        size, sha = await save_stream(file, tmp, max_bytes=(50 if kind == "image" else 500) * 1024 * 1024)
        head = tmp.read_bytes()[:16] if size < 64 else open(tmp, "rb").read(16)
        ext = sniff(head, IMAGE_MAGIC if kind == "image" else AUDIO_MAGIC)
        if not ext:
            tmp.unlink(missing_ok=True)
            raise UploadError("invalid_type", f"File content is not a supported {kind} format")
        storage.ensure_user_quota(db, p.user, size)
    except UploadError as e:
        raise HTTPException(422, detail={"code": e.code, "message": e.message})
    except storage.StorageError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    final = tmp.with_name(f"{uid}{ext}")
    tmp.rename(final)
    import mimetypes

    mimetypes.add_type("image/webp", ".webp")
    a = Artifact(owner_id=p.id, kind=f"upload_{kind}", path=str(final.relative_to(s.data_root)),
                 filename=safe_name(Path(file.filename or "upload").stem) + ext,
                 mime=mimetypes.guess_type(final.name)[0] or "application/octet-stream", size_bytes=size, sha256=sha)
    db.add(a)
    db.commit()
    return _art_pub(a)


class GenerateIn(BaseModel):
    workflow_id: str
    inputs: dict = {}
    priority: int | None = Field(default=None, ge=0, le=100)


@router.post("/generate")
def generate(body: GenerateIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    wf = workflows.WORKFLOWS.get(body.workflow_id)
    if not wf:
        raise not_found("workflow")
    feats = get_setting(db, "features") or {}
    if not feats.get("comfyui", True) or (wf.kind == "video" and not feats.get("video", True)):
        raise HTTPException(403, detail={"code": "feature_disabled", "message": "Feature disabled by administrator"})
    if workflows.missing_models(wf):
        raise HTTPException(409, detail={"code": "workflow_not_installed",
                                         "message": "Workflow models not installed; an administrator must install them"})
    try:
        params = workflows.validate(wf, body.inputs)
    except workflows.WorkflowError as e:
        raise HTTPException(422, detail={"code": "invalid_params", "message": str(e)})
    if params.get("image"):
        from ..models import UserFile

        a = db.get(Artifact, uuid.UUID(params["image"]))
        uf = None if a else db.get(UserFile, uuid.UUID(params["image"]))
        if not ((a and a.owner_id == p.id) or (uf and uf.owner_id == p.id and uf.kind == "image")):
            raise not_found("input image")
    mem = workflows.estimate_memory(wf, params)
    try:
        j = create_job(db, p.user, "video_generate" if wf.kind == "video" else "image_generate",
                       {"workflow_id": wf.id, "inputs": params}, title=f"{wf.name['en']}: {params.get('prompt', '')[:60]}",
                       sched_class=SchedClass.video if wf.kind == "video" else SchedClass.image,
                       priority=body.priority if p.is_admin else None,
                       requested_resources={"memory_gb": mem, "gpu_heavy": wf.gpu_heavy})
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    disk_mb = (params.get("width", 512) * params.get("height", 512) * (params.get("frames") or 1) * 3) / 1e6
    return {"job_id": str(j.id), "estimate": {"memory_gb": mem, "disk_mb_upper_bound": round(disk_mb, 1),
                                              "gpu_heavy": wf.gpu_heavy, "basis": "heuristic"}}


@router.get("/artifacts")
def list_artifacts(p: Principal = Depends(current_principal), db: Session = Depends(get_db), kind: str | None = None,
                   limit: int = 100, offset: int = 0):
    q = select(Artifact).where(visible_filter(Artifact, p, "artifact")).order_by(Artifact.created_at.desc())
    if kind:
        q = q.where(Artifact.kind.in_(kind.split(",")))
    return [_art_pub(a) for a in db.scalars(q.limit(min(limit, 500)).offset(offset))]


def _artifact(db: Session, p: Principal, aid: uuid.UUID) -> Artifact:
    from ..deps import can_read

    a = db.get(Artifact, aid)
    if not a or not can_read(db, p, "artifact", a.id, a.owner_id, a.visibility):
        raise not_found("artifact")
    return a


@router.get("/artifacts/{aid}")
def get_artifact(aid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _art_pub(_artifact(db, p, aid))


@router.get("/artifacts/{aid}/file")
def artifact_file(aid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                  download: bool = False):
    a = _artifact(db, p, aid)
    s = get_settings()
    path = storage.safe_join(s.data_root, a.path)
    if not path.exists():
        raise HTTPException(410, detail={"code": "file_missing", "message": "Artifact file no longer exists"})
    return FileResponse(path, media_type=a.mime, filename=a.filename if download else None,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


class ArtifactPatch(BaseModel):
    visibility: Visibility


@router.patch("/artifacts/{aid}")
def patch_artifact(aid: uuid.UUID, body: ArtifactPatch, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    a = db.get(Artifact, aid)
    if not a or a.owner_id != p.id:
        raise not_found("artifact")
    a.visibility = body.visibility
    db.commit()
    return _art_pub(a)


@router.delete("/artifacts/{aid}")
def delete_artifact(aid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = db.get(Artifact, aid)
    if not a or (a.owner_id != p.id and not p.is_admin):
        raise not_found("artifact")
    s = get_settings()
    try:
        storage.safe_join(s.data_root, a.path).unlink(missing_ok=True)
    except storage.StorageError:
        pass
    db.delete(a)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ audio


class TranscribeIn(BaseModel):
    artifact_id: uuid.UUID
    model_id: uuid.UUID
    language: str | None = Field(default=None, max_length=8)
    task: str = Field(default="transcribe", pattern="^(transcribe|translate)$")
    timestamps: bool = True


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    model_id: uuid.UUID
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    seed: int = 0


def _audio_model(db, p, mid, task) -> RegisteredModel:
    q = select(RegisteredModel).where(RegisteredModel.id == mid, RegisteredModel.backend == "mlworker")
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    m = db.scalar(q)
    if not m or m.status not in (ModelStatus.installed, ModelStatus.ready):
        raise not_found("installed audio model")
    if task == "asr" and m.task != "automatic-speech-recognition":
        raise HTTPException(422, detail={"code": "wrong_model", "message": "Model is not a speech-recognition model"})
    if task == "tts" and m.task not in ("text-to-speech", "text-to-audio"):
        raise HTTPException(422, detail={"code": "wrong_model", "message": "Model is not a text-to-speech model"})
    return m


@router.post("/audio/transcribe")
def transcribe(body: TranscribeIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    m = _audio_model(db, p, body.model_id, "asr")
    a = db.get(Artifact, body.artifact_id)
    if not a or a.owner_id != p.id or a.kind != "upload_audio":
        raise not_found("audio upload")
    try:
        j = create_job(db, p.user, "audio_transcribe", {**body.model_dump(mode="json"), "model_id": str(m.id)},
                       title=f"Transcribe {a.filename}", sched_class=SchedClass.batch)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"job_id": str(j.id)}


@router.post("/audio/tts")
def tts(body: TTSIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    m = _audio_model(db, p, body.model_id, "tts")
    try:
        j = create_job(db, p.user, "audio_tts", {**body.model_dump(mode="json"), "model_id": str(m.id)},
                       title=f"TTS: {body.text[:50]}", sched_class=SchedClass.batch)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {"job_id": str(j.id)}


_ = Job
