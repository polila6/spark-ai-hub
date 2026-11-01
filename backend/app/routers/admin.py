"""Storage overview and administration: settings, audit, backups, diagnostics."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal, require_admin
from ..logging_setup import redact
from ..models import (
    Artifact,
    AuditEvent,
    Dataset,
    DownloadTask,
    RegisteredModel,
    SchedClass,
    User,
)
from ..services import hf, mailer, orchestrator, scheduler, storage, tailscale, telemetry
from ..services.jobs import create_job
from ..settings_store import DEFAULTS, SECRET_KEYS, all_settings, get_setting, set_setting

router = APIRouter(tags=["admin"])


# ------------------------------------------------------------------ storage


@router.get("/api/v1/storage")
def storage_overview(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    s = get_settings()
    fs = storage.fs_stats()
    pol = storage.effective_policy(db, p.user)
    out = {
        "filesystem": {**fs, "mount": s.host_data_root},
        "min_free_gb": get_setting(db, "storage_min_free_gb"),
        "warn_percent": get_setting(db, "storage_warn_percent"),
        "reserved_for_downloads_bytes": storage.reserved_download_bytes(db),
        "available_for_downloads_bytes": storage.available_for_downloads(db),
        "my_usage_bytes": storage.user_usage_bytes(db, p.id),
        "my_quota_gb": pol.storage_quota_gb,
    }
    if p.is_admin:
        cats = {}
        for name in ("models/hf", "models/comfyui", "models/ollama", "models/custom", "datasets", "outputs",
                     "experiments", "benchmarks", "jobs", "cache", "backups", "database", "logs"):
            cats[name] = storage.dir_size(s.data_root / name) if (s.data_root / name).exists() else 0
        out["categories"] = cats
        out["note"] = "models/comfyui contains hard links to models/hf blobs (not double-counted on disk)"
        per_user = []
        for u in db.scalars(select(User)):
            per_user.append({"user_id": str(u.id), "username": u.username,
                             "datasets_bytes": int(db.scalar(select(func.coalesce(func.sum(Dataset.size_bytes), 0))
                                                             .where(Dataset.owner_id == u.id)) or 0),
                             "artifacts_bytes": int(db.scalar(select(func.coalesce(func.sum(Artifact.size_bytes), 0))
                                                              .where(Artifact.owner_id == u.id)) or 0),
                             "quota_gb": storage.effective_policy(db, u).storage_quota_gb})
        out["per_user"] = per_user
        out["models"] = [{"alias": m.alias, "backend": m.backend, "size_bytes": m.size_bytes, "status": m.status.value}
                         for m in db.scalars(select(RegisteredModel).order_by(RegisteredModel.size_bytes.desc().nulls_last()))]
    return out


@router.get("/api/v1/storage/hf-cache")
def hf_cache(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    from huggingface_hub import scan_cache_dir

    s = get_settings()
    info = scan_cache_dir(s.hf_cache_dir)
    registered = {(a, b) for a, b in db.execute(select(RegisteredModel.repo_id, RegisteredModel.revision))}
    active = {(t.repo_id, t.revision) for t in db.scalars(select(DownloadTask))}
    repos = []
    for r in info.repos:
        for rev in r.revisions:
            key = (r.repo_id, rev.commit_hash)
            repos.append({"repo_id": r.repo_id, "revision": rev.commit_hash, "size": rev.size_on_disk,
                          "registered": key in registered, "download_task": key in active,
                          "orphan": key not in registered and key not in active})
    return {"total_size": info.size_on_disk, "revisions": repos}


class GcIn(BaseModel):
    revisions: list[str]


@router.post("/api/v1/storage/hf-cache/gc")
def hf_cache_gc(body: GcIn, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete *orphan* HF cache revisions (not registered, not being downloaded). Never touches user data."""
    from huggingface_hub import scan_cache_dir

    s = get_settings()
    info = scan_cache_dir(s.hf_cache_dir)
    registered = {b for (b,) in db.execute(select(RegisteredModel.revision))}
    active = {t.revision for t in db.scalars(select(DownloadTask))}
    allowed = [h for h in body.revisions if h not in registered and h not in active]
    if not allowed:
        return {"freed": 0, "deleted": []}
    strat = info.delete_revisions(*allowed)
    freed = strat.expected_freed_size
    strat.execute()
    audit(db, "storage.hf_gc", actor=p.user, request=request, details={"revisions": allowed, "freed": freed})
    return {"freed": freed, "deleted": allowed}


# ------------------------------------------------------------------ settings


@router.get("/api/v1/admin/settings")
def get_settings_api(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return all_settings(db)


@router.get("/api/v1/settings/public")
def public_settings(_: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    keys = ("default_language", "default_model", "scheduler_mode", "features", "model_idle_timeout_min",
            "chat_default_model", "harness_default_model", "harness_small_model", "embedding_model", "signup_mode",
            "web_search_enabled", "skills_enabled", "skills_auto_install", "agents_enabled", "max_parallel_agents")
    out = {k: get_setting(db, k) for k in keys}
    out["public_base_url"] = get_settings().public_base_url
    out["mail_enabled"] = mailer.mail_enabled(db)
    return out


@router.put("/api/v1/admin/settings")
def put_settings(body: dict, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    changed = {}
    for k, v in body.items():
        if k not in DEFAULTS and k not in SECRET_KEYS:
            raise HTTPException(422, detail={"code": "unknown_setting", "message": f"Unknown setting {k}"})
        if k == "scheduler_mode" and v not in ("persistent", "on_demand", "hybrid"):
            raise HTTPException(422, detail={"code": "invalid", "message": "scheduler_mode invalid"})
        if k == "hf_token" and v:
            who = hf.whoami(v)
            if not who:
                raise HTTPException(422, detail={"code": "invalid_token", "message": "Hugging Face rejected the token"})
        if k == "system_reserve_gb" and not (4 <= float(v) <= 64):
            raise HTTPException(422, detail={"code": "invalid", "message": "system_reserve_gb must be 4–64"})
        if k == "signup_mode" and v not in ("approval", "open", "closed"):
            raise HTTPException(422, detail={"code": "invalid", "message": "signup_mode must be approval|open|closed"})
        if k == "smtp_security" and v not in ("starttls", "ssl", "none"):
            raise HTTPException(422, detail={"code": "invalid", "message": "smtp_security must be starttls|ssl|none"})
        if k == "terms_version":
            v = int(v)
        if k == "max_parallel_agents":
            v = int(v)
            if not 1 <= v <= 6:
                raise HTTPException(422, detail={"code": "invalid", "message": "max_parallel_agents must be 1–6"})
        if k == "skill_sources":
            import re

            if not isinstance(v, list) or not all(isinstance(x, str) and re.fullmatch(r"[\w.-]+/[\w.-]+(/[\w./-]+)?", x)
                                                  for x in v) or len(v) > 20:
                raise HTTPException(422, detail={"code": "invalid", "message": "skill_sources: list of owner/repo[/path]"})
        set_setting(db, k, v, p.user.id)
        changed[k] = "***" if k in SECRET_KEYS else v
    audit(db, "settings.update", actor=p.user, request=request, details=changed)
    return all_settings(db)


@router.get("/api/v1/admin/hf-token/status")
def hf_token_status(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    t = get_setting(db, "hf_token")
    if not t:
        return {"configured": False}
    who = hf.whoami(t)
    return {"configured": True, "valid": bool(who), "user": (who or {}).get("name")}


# ------------------------------------------------------------------ audit


@router.get("/api/v1/admin/audit")
def audit_log(_: Principal = Depends(require_admin), db: Session = Depends(get_db), action: str | None = None,
              limit: int = Query(200, le=2000), offset: int = 0):
    q = select(AuditEvent).order_by(AuditEvent.ts.desc())
    if action:
        q = q.where(AuditEvent.action.like(f"{action}%"))
    return [{"id": e.id, "ts": e.ts, "actor": e.actor_name, "action": e.action, "target_type": e.target_type,
             "target_id": e.target_id, "success": e.success, "ip": e.ip, "request_id": e.request_id,
             "details": e.details} for e in db.scalars(q.limit(limit).offset(offset))]


# ------------------------------------------------------------------ backups


@router.get("/api/v1/admin/backups")
def list_backups(_: Principal = Depends(require_admin)):
    s = get_settings()
    out = []
    for d in sorted(s.backups_dir.glob("sah-backup-*"), reverse=True):
        mf = d / "manifest.json"
        m = json.loads(mf.read_text()) if mf.exists() else None
        out.append({"name": d.name, "complete": m is not None, "size_bytes": sum(f["size"] for f in m["files"]) if m else None,
                    "created_at": m.get("created_at") if m else None,
                    "includes_user_data": m.get("includes_user_data") if m else None,
                    "table_counts": m.get("table_counts") if m else None})
    return {"backups": out, "external_mounted": Path("/backups-external").is_dir(),
            "warning": "Local backups live on the same NVMe as the data; configure an off-host destination."}


class BackupIn(BaseModel):
    include_user_data: bool = False


@router.post("/api/v1/admin/backups")
def create_backup(body: BackupIn, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    j = create_job(db, p.user, "backup", body.model_dump(), title="Manual backup", sched_class=SchedClass.maintenance)
    audit(db, "backups.create", actor=p.user, request=request, target_type="job", target_id=str(j.id))
    return {"job_id": str(j.id)}


@router.post("/api/v1/admin/backups/{name}/restore-test")
def restore_test(name: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    j = create_job(db, p.user, "restore_test", {"backup": name}, title=f"Restore test {name}",
                   sched_class=SchedClass.maintenance)
    audit(db, "backups.restore_test", actor=p.user, request=request, target_type="backup", target_id=name)
    return {"job_id": str(j.id)}


# ------------------------------------------------------------------ diagnostics


@router.get("/api/v1/admin/diagnostics")
def diagnostics(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    try:
        versions = orchestrator.versions()
        containers = orchestrator.containers()
    except orchestrator.OrchestratorError as e:
        versions, containers = {"error": str(e)}, []
    g = telemetry.gpu()
    return {
        "app_version": s.version,
        "time": datetime.now(timezone.utc),
        "gpu": {k: g.get(k) for k in ("name", "driver_version", "available", "unified_memory")},
        "memory_total_gb": round((telemetry.meminfo().get("MemTotal") or 0) / 1e9, 1),
        "budget": scheduler.budget(db),
        "docker": versions,
        "containers": [{k: c.get(k) for k in ("name", "image", "state", "health", "started_at", "restart_count")}
                       for c in containers],
        "settings": {k: v for k, v in all_settings(db).items() if k not in SECRET_KEYS},
        "counts": {"users": db.scalar(select(func.count()).select_from(User)),
                   "models": db.scalar(select(func.count()).select_from(RegisteredModel))},
    }


@router.get("/api/v1/admin/diagnostics/bundle")
def diagnostics_bundle(request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Redacted ZIP: diagnostics, container logs (secrets masked), recent audit and failed jobs."""
    from ..models import Job, JobStatus

    diag = diagnostics(p, db)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("diagnostics.json", json.dumps(diag, default=str, indent=2))
        for svc in ("backend", "worker", "monitor", "engine-manager", "ollama", "proxy", "frontend", "postgres", "redis"):
            try:
                z.writestr(f"logs/{svc}.log", redact(orchestrator.service_logs(svc, 1000)))
            except orchestrator.OrchestratorError as e:
                z.writestr(f"logs/{svc}.log", f"unavailable: {e}")
        for eng in ("vllm", "comfyui", "mlworker"):
            try:
                z.writestr(f"logs/engine-{eng}.log", redact(orchestrator.engine_logs(eng, 1000)))
            except orchestrator.OrchestratorError:
                pass
        failed = [{"id": str(j.id), "type": j.type, "error": redact(j.error or ""), "finished_at": str(j.finished_at)}
                  for j in db.scalars(select(Job).where(Job.status == JobStatus.failed).order_by(Job.finished_at.desc()).limit(50))]
        z.writestr("failed_jobs.json", json.dumps(failed, indent=2))
    audit(db, "admin.diagnostic_bundle", actor=p.user, request=request)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="spark-ai-hub-diagnostics.zip"'})


# ------------------------------------------------------------------ access: mail and Tailscale checks


class MailTestIn(BaseModel):
    to: str = Field(min_length=3, max_length=255)


@router.post("/api/v1/admin/mail/test")
def mail_test(body: MailTestIn, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        mailer.send_test(db, body.to.strip())
    except mailer.MailError as e:
        raise HTTPException(502, detail={"code": "smtp_failed", "message": str(e),
                                         "remediation": "Revisa servidor y puerto (normalmente 587 con STARTTLS), "
                                                        "usuario y contraseña de correo, y que el remitente sea tu dirección."})
    audit(db, "mail.test", actor=p.user, request=request, details={"to": body.to})
    return {"ok": True}


@router.get("/api/v1/admin/mail/outbox")
def mail_outbox(_: Principal = Depends(require_admin), db: Session = Depends(get_db), limit: int = 50):
    from ..models import EmailOutbox

    rows = db.scalars(select(EmailOutbox).order_by(EmailOutbox.id.desc()).limit(min(limit, 200)))
    return [{"id": r.id, "kind": r.kind, "to": r.to_addr, "subject": r.subject, "status": r.status,
             "attempts": r.attempts, "last_error": r.last_error, "created_at": r.created_at, "sent_at": r.sent_at}
            for r in rows]


@router.post("/api/v1/admin/tailscale/check")
def tailscale_check(_: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    key, dev = get_setting(db, "tailscale_api_key"), get_setting(db, "tailscale_device_id")
    if not key or not dev:
        raise HTTPException(422, detail={"code": "not_configured", "message": "Set the Tailscale API key and device id"})
    try:
        return {"ok": True, "device": tailscale.check(key, dev)}
    except tailscale.TailscaleError as e:
        raise HTTPException(502, detail={"code": "tailscale_failed", "message": str(e)})
