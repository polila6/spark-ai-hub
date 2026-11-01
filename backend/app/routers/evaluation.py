"""Benchmarks and model comparisons (results in PostgreSQL, exports in CSV/JSON)."""
from __future__ import annotations

import csv
import io
import json
import random
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal, not_found, require_researcher, visible_filter
from ..engines import LLM_ENGINES
from ..models import BenchmarkRun, BenchmarkSample, Comparison, RegisteredModel, SchedClass
from ..services.benchsuites import SUITES, public_suites
from ..services.jobs import JobError, create_job

router = APIRouter(tags=["evaluation"])


class BenchIn(BaseModel):
    model_id: uuid.UUID
    suite: str
    temperature: float = Field(default=0.0, ge=0, le=2)
    seed: int = 1234
    unload_after: bool | None = None


def _model_for(db: Session, p: Principal, mid: uuid.UUID) -> RegisteredModel:
    q = select(RegisteredModel).where(RegisteredModel.id == mid)
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    m = db.scalar(q)
    if not m or m.backend not in LLM_ENGINES:
        raise not_found("LLM model")
    return m


def _run_pub(r: BenchmarkRun) -> dict:
    return {"id": str(r.id), "kind": r.kind, "suite": r.suite, "model_id": str(r.model_id) if r.model_id else None,
            "model": r.model_snapshot, "config": r.config, "environment": r.environment, "status": r.status,
            "summary": r.summary, "created_at": r.created_at, "finished_at": r.finished_at,
            "job_id": str(r.job_id) if r.job_id else None, "owner_id": str(r.owner_id) if r.owner_id else None}


@router.get("/api/v1/benchmarks/suites")
def suites(_: Principal = Depends(current_principal)):
    return public_suites()


@router.post("/api/v1/benchmarks")
def create_benchmark(body: BenchIn, p: Principal = Depends(require_researcher), db: Session = Depends(get_db)):
    if body.suite not in SUITES:
        raise HTTPException(422, detail={"code": "unknown_suite", "message": "Unknown suite"})
    m = _model_for(db, p, body.model_id)
    run = BenchmarkRun(owner_id=p.id, kind=SUITES[body.suite]["kind"], suite=body.suite, model_id=m.id,
                       config=body.model_dump(exclude={"model_id"}, mode="json"), status="queued",
                       model_snapshot={"alias": m.alias})
    db.add(run)
    db.commit()
    try:
        j = create_job(db, p.user, "benchmark", {"run_id": str(run.id), "model_id": str(m.id)},
                       title=f"Benchmark {body.suite} · {m.alias}", sched_class=SchedClass.batch)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    run.job_id = j.id
    db.commit()
    return _run_pub(run)


@router.get("/api/v1/benchmarks")
def list_benchmarks(p: Principal = Depends(current_principal), db: Session = Depends(get_db), suite: str | None = None,
                    model_id: uuid.UUID | None = None, mine: bool = False):
    # Benchmark results describe shared infrastructure: visible to all users unless 'mine'.
    q = select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc()).limit(500)
    if mine:
        q = q.where(BenchmarkRun.owner_id == p.id)
    if suite:
        q = q.where(BenchmarkRun.suite == suite)
    if model_id:
        q = q.where(BenchmarkRun.model_id == model_id)
    return [_run_pub(r) for r in db.scalars(q)]


@router.get("/api/v1/benchmarks/{run_id}")
def get_benchmark(run_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = db.get(BenchmarkRun, run_id)
    if not r:
        raise not_found("benchmark")
    d = _run_pub(r)
    show_out = r.owner_id == p.id or p.is_admin
    d["samples"] = [{"idx": s.idx, "case_id": s.case_id, "concurrency": s.concurrency, "ttft_ms": s.ttft_ms,
                     "latency_ms": s.latency_ms, "prompt_tokens": s.prompt_tokens,
                     "completion_tokens": s.completion_tokens, "tokens_per_s": s.tokens_per_s, "score": s.score,
                     "error": s.error, "output": s.output if show_out else None, "meta": s.meta}
                    for s in db.scalars(select(BenchmarkSample).where(BenchmarkSample.run_id == r.id)
                                        .order_by(BenchmarkSample.idx))]
    return d


@router.get("/api/v1/benchmarks/{run_id}/export")
def export_benchmark(run_id: uuid.UUID, fmt: str = "csv", p: Principal = Depends(current_principal),
                     db: Session = Depends(get_db)):
    d = get_benchmark(run_id, p, db)
    if fmt == "json":
        return PlainTextResponse(json.dumps(d, default=str, indent=2), media_type="application/json",
                                 headers={"Content-Disposition": f'attachment; filename="benchmark-{run_id}.json"'})
    buf = io.StringIO()
    cols = ["run_id", "suite", "model", "revision", "backend", "quantization", "idx", "case_id", "concurrency",
            "ttft_ms", "latency_ms", "prompt_tokens", "completion_tokens", "tokens_per_s", "score", "error"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for s in d["samples"]:
        w.writerow({"run_id": d["id"], "suite": d["suite"], "model": d["model"].get("alias"),
                    "revision": d["model"].get("revision"), "backend": d["model"].get("backend"),
                    "quantization": d["model"].get("quantization"), **{k: s.get(k) for k in cols[6:]}})
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="benchmark-{run_id}.csv"'})


# ------------------------------------------------------------------ comparisons


class CompareIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    system_prompt: str | None = Field(default=None, max_length=20_000)
    model_ids: list[uuid.UUID] = Field(min_length=2, max_length=6)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    seed: int | None = 1234
    blinded: bool = False
    title: str = Field(default="", max_length=255)


class PreferenceIn(BaseModel):
    winner_index: int | None = Field(default=None, ge=0)
    ratings: dict[str, int] | None = None
    notes: str | None = Field(default=None, max_length=5000)


def _comp_pub(c: Comparison, reveal: bool) -> dict:
    results = list(c.results or [])
    if c.blinded and not reveal:
        results = [{**{k: v for k, v in r.items() if k not in ("alias", "model_id", "backend", "revision",
                                                                "quantization")}, "label": f"Model {chr(65 + i)}"}
                   for i, r in enumerate(results)]
    return {"id": str(c.id), "title": c.title, "prompt": c.prompt, "system_prompt": c.system_prompt,
            "settings": c.settings, "model_ids": c.model_ids if (reveal or not c.blinded) else None,
            "results": results, "blinded": c.blinded, "preference": c.preference, "created_at": c.created_at}


@router.post("/api/v1/comparisons")
def create_comparison(body: CompareIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    ids = [str(_model_for(db, p, m).id) for m in body.model_ids]
    if body.blinded:
        random.shuffle(ids)
    c = Comparison(owner_id=p.id, title=body.title or body.prompt[:60], prompt=body.prompt,
                   system_prompt=body.system_prompt,
                   settings={"temperature": body.temperature, "max_tokens": body.max_tokens, "seed": body.seed},
                   model_ids=ids, blinded=body.blinded, results=[])
    db.add(c)
    db.commit()
    try:
        j = create_job(db, p.user, "comparison", {"comparison_id": str(c.id), "model_ids": ids},
                       title=f"Comparison: {c.title}", sched_class=SchedClass.interactive)
    except JobError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})
    return {**_comp_pub(c, False), "job_id": str(j.id)}


@router.get("/api/v1/comparisons")
def list_comparisons(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return [_comp_pub(c, c.preference is not None) for c in db.scalars(
        select(Comparison).where(Comparison.owner_id == p.id).order_by(Comparison.created_at.desc()).limit(200))]


def _comp(db, p, cid) -> Comparison:
    c = db.get(Comparison, cid)
    if not c or c.owner_id != p.id:
        raise not_found("comparison")
    return c


@router.get("/api/v1/comparisons/{cid}")
def get_comparison(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                   reveal: bool = False):
    c = _comp(db, p, cid)
    return _comp_pub(c, reveal or c.preference is not None)


@router.post("/api/v1/comparisons/{cid}/preference")
def set_preference(cid: uuid.UUID, body: PreferenceIn, p: Principal = Depends(current_principal),
                   db: Session = Depends(get_db)):
    c = _comp(db, p, cid)
    c.preference = body.model_dump()
    db.commit()
    return _comp_pub(c, True)


@router.post("/api/v1/comparisons/{cid}/rerun")
def rerun(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = _comp(db, p, cid)
    body = CompareIn(prompt=c.prompt, system_prompt=c.system_prompt, model_ids=[uuid.UUID(m) for m in c.model_ids],
                     blinded=c.blinded, title=f"{c.title} (re-run)", **{k: v for k, v in (c.settings or {}).items()
                                                                       if k in ("temperature", "max_tokens", "seed")})
    return create_comparison(body, p, db)


@router.get("/api/v1/comparisons/{cid}/export")
def export_comparison(cid: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                      fmt: str = "json"):
    c = _comp(db, p, cid)
    d = _comp_pub(c, True)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["model", "backend", "ttft_ms", "latency_ms", "completion_tokens", "tokens_per_s", "output"])
        for r in d["results"]:
            m = r.get("metrics") or {}
            w.writerow([r.get("alias"), r.get("backend"), m.get("ttft_ms"), m.get("latency_ms"),
                        m.get("completion_tokens"), m.get("tokens_per_s"), r.get("output")])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="comparison-{cid}.csv"'})
    return PlainTextResponse(json.dumps(d, default=str, ensure_ascii=False, indent=2), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="comparison-{cid}.json"'})
