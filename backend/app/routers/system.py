"""System status, metrics (current/history/SSE), alerts and engines."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal, require_admin, session_principal
from ..engines import all_engines, get_engine
from ..models import Alert, Job, JobStatus, MetricSample, RegisteredModel, RuntimeInstance
from ..redis_client import get_redis
from ..services import orchestrator, scheduler

router = APIRouter(tags=["system"])


@router.get("/api/v1/system/health")
def health(db: Session = Depends(get_db)):
    """Unauthenticated liveness/readiness (no sensitive data)."""
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "down"
    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001
        checks["redis"] = "down"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise HTTPException(503, detail={"status": "degraded", "checks": checks})
    return {"status": "ok", "checks": checks, "version": get_settings().version}


@router.get("/api/v1/system/status")
def status(_: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    engines = {}
    for name, eng in all_engines().items():
        h = eng.health_check()
        engines[name] = {"ok": h.ok, "status": h.status, "detail": h.detail, "version": h.version, **h.extra,
                         "capabilities": sorted(eng.capabilities)}
    raw = get_redis().get("sah:containers")
    containers = orjson.loads(raw) if raw else None
    instances = db.scalars(select(RuntimeInstance)).all()
    active = []
    for i in instances:
        m = db.get(RegisteredModel, i.model_id) if i.model_id else None
        active.append({"id": str(i.id), "engine": i.engine, "model": m.alias if m else None, "state": i.state,
                       "reserved_memory_gb": i.reserved_memory_gb, "measured_memory_gb": i.measured_memory_gb,
                       "load_seconds": i.load_seconds, "last_used_at": i.last_used_at,
                       "active_requests": scheduler.active_requests(i.id),
                       "pinned": bool(m and m.pinned)})
    counts = dict(db.execute(select(Job.status, func.count()).where(
        Job.status.in_([JobStatus.queued, JobStatus.running, JobStatus.preparing])).group_by(Job.status)).all())
    errors_24h = db.scalar(select(func.count()).select_from(Job).where(
        Job.status == JobStatus.failed, Job.finished_at > datetime.now(timezone.utc) - timedelta(hours=24))) or 0
    return {
        "engines": engines,
        "containers": containers,
        "runtime_instances": active,
        "budget": scheduler.budget(db),
        "jobs": {"queued": counts.get(JobStatus.queued, 0), "running": counts.get(JobStatus.running, 0)
                 + counts.get(JobStatus.preparing, 0), "failed_24h": errors_24h},
    }


@router.get("/api/v1/metrics/current")
def metrics_current(_: Principal = Depends(current_principal)):
    raw = get_redis().get("sah:metrics:latest")
    if not raw:
        raise HTTPException(503, detail={"code": "no_metrics", "message": "Monitor has not produced data yet"})
    return orjson.loads(raw)


@router.get("/api/v1/metrics/recent")
def metrics_recent(_: Principal = Depends(current_principal), limit: int = Query(180, le=720)):
    rows = get_redis().lrange("sah:metrics:recent", 0, limit - 1)
    return [orjson.loads(r) for r in reversed(rows)]


@router.get("/api/v1/metrics/history")
def metrics_history(_: Principal = Depends(current_principal), db: Session = Depends(get_db),
                    hours: int = Query(24, ge=1, le=24 * 90)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    res = "1m" if hours <= 48 else "1h"
    rows = db.scalars(select(MetricSample).where(MetricSample.ts >= since, MetricSample.resolution == res)
                      .order_by(MetricSample.ts)).all()
    step = max(1, len(rows) // 600)
    return {"resolution": res, "points": [{"ts": r.ts, **r.data} for r in rows[::step]]}


@router.get("/api/v1/metrics/stream")
async def metrics_stream(request: Request, _: Principal = Depends(current_principal)):
    async def gen():
        last = None
        while not await request.is_disconnected():
            raw = await asyncio.to_thread(get_redis().get, "sah:metrics:latest")
            if raw and raw != last:
                last = raw
                yield f"data: {raw}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache",
                                                                             "X-Accel-Buffering": "no"})


@router.get("/api/v1/metrics/alerts")
def alerts(_: Principal = Depends(current_principal), db: Session = Depends(get_db), include_resolved: bool = False):
    q = select(Alert).order_by(Alert.last_seen.desc()).limit(200)
    if not include_resolved:
        q = q.where(Alert.resolved_at.is_(None))
    return [{"id": a.id, "key": a.key, "kind": a.kind, "severity": a.severity, "message": a.message,
             "first_seen": a.first_seen, "last_seen": a.last_seen, "resolved_at": a.resolved_at,
             "acknowledged": a.acknowledged_by is not None} for a in db.scalars(q)]


@router.post("/api/v1/metrics/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, detail={"code": "not_found", "message": "Alert not found"})
    a.acknowledged_by = p.user.id
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ engines


@router.get("/api/v1/engines")
def engines(_: Principal = Depends(current_principal)):
    out = {}
    for name, eng in all_engines().items():
        h = eng.health_check()
        out[name] = {"ok": h.ok, "status": h.status, "detail": h.detail, "version": h.version, "extra": h.extra,
                     "capabilities": sorted(eng.capabilities), "on_demand": eng.on_demand_container}
    return out


@router.post("/api/v1/engines/{engine}/start")
def engine_start(engine: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    if engine not in ("comfyui", "mlworker"):
        raise HTTPException(400, detail={"code": "not_startable", "message": "LLM engines start by loading a model"})
    try:
        inst = scheduler.ensure_engine(engine)
    except scheduler.SchedulingError as e:
        raise HTTPException(409, detail={"code": e.code, "message": e.message})
    audit(db, "engines.start", actor=p.user, request=request, target_type="engine", target_id=engine)
    return {"ok": True, "load_seconds": inst.load_seconds}


@router.post("/api/v1/engines/{engine}/stop")
def engine_stop(engine: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    if engine not in ("comfyui", "mlworker", "vllm"):
        raise HTTPException(400, detail={"code": "not_stoppable", "message": "Engine cannot be stopped from here"})
    for inst in db.scalars(select(RuntimeInstance).where(RuntimeInstance.engine == engine)).all():
        if scheduler.active_requests(inst.id) > 0:
            raise HTTPException(409, detail={"code": "busy", "message": "Engine is serving requests"})
        scheduler.unload_instance(inst.id, reason="admin stop", actor_id=p.user.id)
    if engine in ("comfyui", "mlworker", "vllm"):
        try:
            orchestrator.stop_engine(engine)
        except orchestrator.OrchestratorError:
            pass
    audit(db, "engines.stop", actor=p.user, request=request, target_type="engine", target_id=engine)
    return {"ok": True}


@router.post("/api/v1/engines/ollama/restart")
def ollama_restart(request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    busy = [i for i in db.scalars(select(RuntimeInstance).where(RuntimeInstance.engine == "ollama"))
            if scheduler.active_requests(i.id) > 0]
    if busy:
        raise HTTPException(409, detail={"code": "busy", "message": "Ollama is serving requests"})
    orchestrator.restart_service("ollama")
    scheduler.reconcile()
    audit(db, "engines.restart", actor=p.user, request=request, target_type="engine", target_id="ollama")
    return {"ok": True}


@router.get("/api/v1/engines/{engine}/logs")
def engine_logs(engine: str, _: Principal = Depends(require_admin), tail: int = Query(200, le=2000)):
    try:
        if engine in ("vllm", "comfyui", "mlworker"):
            return {"logs": orchestrator.engine_logs(engine, tail)}
        return {"logs": orchestrator.service_logs(engine, tail)}
    except orchestrator.OrchestratorError as e:
        raise HTTPException(e.status or 502, detail={"code": "orchestrator", "message": str(e)})


@router.get("/api/v1/system/containers")
def containers(_: Principal = Depends(session_principal)):
    try:
        return {"containers": orchestrator.containers(), "stats": orchestrator.container_stats()}
    except orchestrator.OrchestratorError as e:
        raise HTTPException(502, detail={"code": "orchestrator", "message": str(e)})


_ = get_engine


@router.get("/api/v1/system/memory")
def memory(_: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Unified-memory budget, resident models (with idle countdown) and running jobs: the memory panel."""
    return scheduler.memory_overview(db)
