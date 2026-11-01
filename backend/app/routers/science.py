"""Scientific Lab: datasets, custom models with trusted adapters, experiments and batch inference."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, can_read, current_principal, not_found, require_researcher, visible_filter
from ..models import Dataset, Experiment, ModelStatus, RegisteredModel, SchedClass, SharedResource, Visibility
from ..services import registry, storage
from ..services.jobs import JobError, create_job
from ..services.sci_adapters import ADAPTERS, validate_artifacts
from ..services.uploads import (
    DATASET_EXT,
    EXECUTABLE_MODEL_EXT,
    MODEL_FILE_EXT,
    UploadError,
    extract_image_zip,
    inspect_dataset,
    safe_name,
    save_stream,
)

router = APIRouter(tags=["science"])


def _ds_pub(d: Dataset) -> dict:
    return {"id": str(d.id), "name": d.name, "description": d.description, "format": d.format, "size_bytes": d.size_bytes,
            "sha256": d.sha256, "schema": d.schema, "visibility": d.visibility.value, "owner_id": str(d.owner_id),
            "created_at": d.created_at}


@router.get("/api/v1/datasets")
def list_datasets(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return [_ds_pub(d) for d in db.scalars(select(Dataset).where(visible_filter(Dataset, p, "dataset"))
                                           .order_by(Dataset.created_at.desc()))]


@router.post("/api/v1/datasets")
async def upload_dataset(file: UploadFile = File(...), name: str = Form(..., max_length=255),
                         description: str = Form("", max_length=5000), p: Principal = Depends(require_researcher),
                         db: Session = Depends(get_db)):
    s = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in DATASET_EXT:
        raise HTTPException(422, detail={"code": "unsupported_format",
                                         "message": f"Supported: {sorted(DATASET_EXT)}. Pickle formats are not accepted."})
    did = uuid.uuid4()
    base = s.datasets_dir / str(p.id) / str(did)
    target = base / (safe_name(Path(file.filename).stem) + ext)
    try:
        storage.ensure_user_quota(db, p.user, 0)
        size, sha = await save_stream(file, target)
        storage.ensure_user_quota(db, p.user, size)
        if ext == ".zip":
            schema = extract_image_zip(target, base / "images")
            target.unlink()
            rel = str((base / "images").relative_to(s.datasets_dir))
            size = storage.dir_size(base)
        else:
            schema = inspect_dataset(target, ext)
            rel = str(target.relative_to(s.datasets_dir))
    except (UploadError, storage.StorageError) as e:
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(422, detail={"code": e.code, "message": e.message})
    except Exception as e:  # noqa: BLE001 - malformed files
        shutil.rmtree(base, ignore_errors=True)
        raise HTTPException(422, detail={"code": "invalid_dataset", "message": f"Could not parse dataset: {e}"[:500]})
    d = Dataset(id=did, owner_id=p.id, name=name, description=description, format=ext.lstrip("."), path=rel,
                size_bytes=size, sha256=sha, schema=schema)
    db.add(d)
    db.commit()
    return _ds_pub(d)


def _dataset(db, p, did, write=False) -> Dataset:
    d = db.get(Dataset, did)
    if not d or (write and d.owner_id != p.id) or not can_read(db, p, "dataset", d.id, d.owner_id, d.visibility):
        raise not_found("dataset")
    return d


@router.get("/api/v1/datasets/{did}")
def get_dataset(did: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    d = _dataset(db, p, did)
    out = _ds_pub(d)
    s = get_settings()
    path = s.datasets_dir / d.path
    if d.format in ("csv", "jsonl") and path.is_file():
        with open(path, encoding="utf-8", errors="replace") as f:
            out["preview"] = [next(f, "") for _ in range(11)]
    return out


class SharePatch(BaseModel):
    visibility: Visibility | None = None
    share_with: list[uuid.UUID] | None = None


@router.patch("/api/v1/datasets/{did}")
def patch_dataset(did: uuid.UUID, body: SharePatch, request: Request, p: Principal = Depends(current_principal),
                  db: Session = Depends(get_db)):
    d = _dataset(db, p, did, write=True)
    if body.visibility:
        d.visibility = body.visibility
    if body.share_with is not None:
        db.query(SharedResource).filter(SharedResource.resource_type == "dataset", SharedResource.resource_id == d.id).delete()
        for u in set(body.share_with):
            db.add(SharedResource(resource_type="dataset", resource_id=d.id, grantee_id=u, granted_by=p.id))
        if body.share_with and d.visibility == Visibility.private:
            d.visibility = Visibility.shared
    db.commit()
    audit(db, "datasets.share", actor=p.user, request=request, target_type="dataset", target_id=str(d.id),
          details={"visibility": d.visibility.value})
    return _ds_pub(d)


@router.delete("/api/v1/datasets/{did}")
def delete_dataset(did: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    d = _dataset(db, p, did, write=True)
    s = get_settings()
    shutil.rmtree(s.datasets_dir / str(d.owner_id) / str(d.id), ignore_errors=True)
    db.delete(d)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ custom models


@router.get("/api/v1/science/adapters")
def adapters(_: Principal = Depends(current_principal)):
    return ADAPTERS


@router.post("/api/v1/science/models")
async def register_custom_model(request: Request, name: str = Form(..., max_length=100),
                                adapter: str = Form(...), config_json: str = Form(...),
                                description: str = Form("", max_length=5000),
                                files: list[UploadFile] = File(...), p: Principal = Depends(require_researcher),
                                db: Session = Depends(get_db)):
    s = get_settings()
    if adapter not in ADAPTERS:
        raise HTTPException(422, detail={"code": "unknown_adapter", "message": "Unknown adapter"})
    try:
        cfg = json.loads(config_json)
        assert isinstance(cfg, dict)
    except (ValueError, AssertionError):
        raise HTTPException(422, detail={"code": "invalid_config", "message": "config_json must be a JSON object"})
    mid = uuid.uuid4()
    rel = f"{p.id}/{mid}"
    d = s.custom_models_dir / rel
    d.mkdir(parents=True)
    total = 0
    try:
        for f in files:
            ext = Path(f.filename or "").suffix.lower()
            if ext in EXECUTABLE_MODEL_EXT:
                raise UploadError("executable_format", f"{f.filename}: pickle/executable checkpoints are rejected. "
                                                       "Convert to safetensors (see docs/MODEL_ADAPTERS.md).")
            if ext not in MODEL_FILE_EXT:
                raise UploadError("unsupported_file", f"{f.filename}: allowed {sorted(MODEL_FILE_EXT)}")
            size, _ = await save_stream(f, d / safe_name(f.filename))
            total += size
        (d / "config.json").write_text(json.dumps(cfg, indent=2))
        info = validate_artifacts(adapter, d, cfg)
    except UploadError as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, detail={"code": e.code, "message": e.message})
    m = RegisteredModel(alias=registry.make_alias(db, name), display_name=name, source="custom", modality="scientific",
                        format="safetensors", backend="mlworker", path=rel, size_bytes=total, status=ModelStatus.installed,
                        owner_id=p.id, visibility=Visibility.private, task=adapter, capabilities=["scientific"],
                        parameters_b=round(info["parameters"] / 1e9, 6),
                        compat={"adapter": adapter, "validation": info, "description": description,
                                "overall": "likely", "verified_engines": []})
    db.add(m)
    db.commit()
    audit(db, "science.model_register", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={"adapter": adapter, "bytes": total})
    return registry.model_public(m, include_config=True)


class FromHfIn(BaseModel):
    source_model_id: uuid.UUID
    name: str = Field(max_length=100)
    adapter: str
    config: dict = {}


@router.post("/api/v1/science/models/from-hf")
def custom_from_hf(body: FromHfIn, request: Request, p: Principal = Depends(require_researcher),
                   db: Session = Depends(get_db)):
    """Register a downloaded HF snapshot (e.g. timm/resnet18) as a scientific model (hard links, no copy)."""
    s = get_settings()
    src = db.get(RegisteredModel, body.source_model_id)
    if not src or src.source != "hf" or not src.path or src.status not in (ModelStatus.installed, ModelStatus.downloaded):
        raise not_found("downloaded HF model")
    if not (p.is_admin or src.owner_id == p.id or src.visibility == Visibility.public):
        raise not_found("downloaded HF model")
    snap = s.hf_cache_dir / src.path
    mid = uuid.uuid4()
    rel = f"{p.id}/{mid}"
    d = s.custom_models_dir / rel
    d.mkdir(parents=True)
    st = snap / "model.safetensors"
    if not st.exists():
        shutil.rmtree(d)
        raise HTTPException(422, detail={"code": "missing_file", "message": "Snapshot has no model.safetensors"})
    os.link(st.resolve(), d / "model.safetensors")
    hf_cfg = {}
    if (snap / "config.json").exists():
        hf_cfg = json.loads((snap / "config.json").read_text())
    cfg = {**{k: hf_cfg.get(k) for k in ("architecture", "num_classes", "label_names") if hf_cfg.get(k) is not None},
           **(hf_cfg.get("pretrained_cfg") or {}), **body.config, "source_repo": src.repo_id, "source_revision": src.revision}
    cfg.setdefault("architecture", hf_cfg.get("architecture"))
    (d / "config.json").write_text(json.dumps(cfg, indent=2))
    try:
        info = validate_artifacts(body.adapter, d, cfg)
    except UploadError as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(422, detail={"code": e.code, "message": e.message})
    m = RegisteredModel(alias=registry.make_alias(db, body.name), display_name=body.name, source="custom",
                        modality="scientific", format="safetensors", backend="mlworker", path=rel,
                        size_bytes=st.stat().st_size, status=ModelStatus.installed, owner_id=p.id,
                        visibility=Visibility.private, task=body.adapter, capabilities=["scientific"],
                        repo_id=src.repo_id, revision=src.revision, license=src.license,
                        parameters_b=round(info["parameters"] / 1e9, 6),
                        compat={"adapter": body.adapter, "validation": info, "overall": "likely", "verified_engines": []})
    db.add(m)
    db.commit()
    audit(db, "science.model_register", actor=p.user, request=request, target_type="model", target_id=str(m.id),
          details={"adapter": body.adapter, "from": src.repo_id})
    return registry.model_public(m, include_config=True)


# ------------------------------------------------------------------ experiments


class ExperimentIn(BaseModel):
    name: str = Field(max_length=255)
    description: str = Field(default="", max_length=10000)
    model_id: uuid.UUID | None = None
    dataset_id: uuid.UUID | None = None
    config: dict = {}


def _exp_pub(e: Experiment) -> dict:
    return {"id": str(e.id), "name": e.name, "description": e.description, "kind": e.kind,
            "model_id": str(e.model_id) if e.model_id else None, "dataset_id": str(e.dataset_id) if e.dataset_id else None,
            "config": e.config, "metrics": e.metrics, "job_ids": e.job_ids, "status": e.status,
            "visibility": e.visibility.value, "owner_id": str(e.owner_id), "created_at": e.created_at,
            "updated_at": e.updated_at}


@router.get("/api/v1/experiments")
def list_experiments(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return [_exp_pub(e) for e in db.scalars(select(Experiment).where(visible_filter(Experiment, p, "experiment"))
                                            .order_by(Experiment.updated_at.desc()))]


@router.post("/api/v1/experiments")
def create_experiment(body: ExperimentIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if body.dataset_id:
        _dataset(db, p, body.dataset_id)
    e = Experiment(owner_id=p.id, **body.model_dump())
    db.add(e)
    db.commit()
    return _exp_pub(e)


def _exp(db, p, eid, write=False) -> Experiment:
    e = db.get(Experiment, eid)
    if not e or (write and e.owner_id != p.id) or not can_read(db, p, "experiment", e.id, e.owner_id, e.visibility):
        raise not_found("experiment")
    return e


@router.get("/api/v1/experiments/{eid}")
def get_experiment(eid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _exp_pub(_exp(db, p, eid))


@router.patch("/api/v1/experiments/{eid}")
def patch_experiment(eid: uuid.UUID, body: dict, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    e = _exp(db, p, eid, write=True)
    for k in ("name", "description", "config"):
        if k in body:
            setattr(e, k, body[k])
    if "visibility" in body:
        e.visibility = Visibility(body["visibility"])
    db.commit()
    return _exp_pub(e)


class RunIn(BaseModel):
    options: dict = {}


@router.post("/api/v1/experiments/{eid}/run")
def run_experiment(eid: uuid.UUID, body: RunIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    e = _exp(db, p, eid, write=True)
    if not e.model_id or not e.dataset_id:
        raise HTTPException(422, detail={"code": "incomplete", "message": "Experiment needs a model and a dataset"})
    m = db.scalar(select(RegisteredModel).where(RegisteredModel.id == e.model_id,
                                                visible_filter(RegisteredModel, p, "model")))
    if not m or m.source != "custom":
        raise not_found("scientific model")
    ds = _dataset(db, p, e.dataset_id)
    fmt = "." + ds.format if ds.format != "zip" else ".zip"
    if fmt not in ADAPTERS[m.task]["dataset_formats"]:
        raise HTTPException(422, detail={"code": "incompatible_dataset",
                                         "message": f"Adapter {m.task} accepts {ADAPTERS[m.task]['dataset_formats']}"})
    try:
        j = create_job(db, p.user, "sci_infer", {"experiment_id": str(e.id), "model_id": str(m.id),
                                                 "dataset_id": str(ds.id), "options": {**(e.config or {}), **body.options}},
                       title=f"Experiment {e.name}", sched_class=SchedClass.scientific)
    except JobError as ex:
        raise HTTPException(ex.status, detail={"code": ex.code, "message": ex.message})
    e.status = "queued"
    e.job_ids = [*(e.job_ids or []), str(j.id)]
    db.commit()
    return {"job_id": str(j.id)}


@router.delete("/api/v1/experiments/{eid}")
def delete_experiment(eid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    e = _exp(db, p, eid, write=True)
    db.delete(e)
    db.commit()
    return {"ok": True}
