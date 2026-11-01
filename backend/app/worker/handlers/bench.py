"""Benchmark and model-comparison jobs (sequential model execution)."""
from __future__ import annotations

import statistics
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from ...db import SessionLocal
from ...engines import get_engine
from ...models import BenchmarkRun, BenchmarkSample, Comparison, RegisteredModel, RuntimeInstance
from ...services import llm, scheduler, telemetry
from ...services.benchsuites import SUITES, score
from ..context import Cancelled, JobContext
from . import Handler, register


def environment(model: RegisteredModel) -> dict:
    # The worker has no GPU utility access; read the GPU identity from the monitor's latest sample.
    import orjson

    from ...redis_client import get_redis

    raw = get_redis().get("sah:metrics:latest")
    g = (orjson.loads(raw).get("gpu") if raw else None) or telemetry.gpu()
    h = get_engine(model.backend).health_check()
    return {
        "gpu": g.get("name"), "driver": g.get("driver_version"),
        "memory_total_gb": round((telemetry.meminfo().get("MemTotal") or 0) / 1e9, 1),
        "engine": model.backend, "engine_version": h.version, "engine_image": h.extra.get("image"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def snapshot(model: RegisteredModel) -> dict:
    return {"alias": model.alias, "repo_id": model.repo_id, "revision": model.revision, "backend": model.backend,
            "quantization": model.quantization, "format": model.format, "parameters_b": model.parameters_b,
            "runtime_params": model.runtime_config.params if model.runtime_config else {}}


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 2)


@register
class Benchmark(Handler):
    type = "benchmark"

    def run(self, ctx: JobContext) -> None:
        run_id = uuid.UUID(ctx.params["run_id"])
        with SessionLocal() as db:
            run = db.get(BenchmarkRun, run_id)
            model = db.get(RegisteredModel, run.model_id)
            suite = SUITES[run.suite]
            cfg = run.config or {}
            run.status = "running"
            run.model_snapshot = snapshot(model)
            db.commit()
            model_id = model.id
            was_loaded = bool(db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == model_id)))
        ctx.progress(0.02, "Loading model", force=True)
        inst = scheduler.ensure_loaded(model_id)
        with SessionLocal() as db:
            model = db.get(RegisteredModel, model_id)
            env = environment(model)
            env["load_seconds_this_run"] = inst.load_seconds
            env["measured_memory_gb"] = inst.measured_memory_gb
            env["reserved_memory_gb"] = inst.reserved_memory_gb
        params = {"temperature": cfg.get("temperature", 0.0), "seed": cfg.get("seed", 1234)}
        samples: list[dict] = []
        scheduler.begin_request(inst.id)
        try:
            if suite["kind"] == "llm_perf":
                samples = self._perf(ctx, model, suite, params)
            else:
                samples = self._quality(ctx, model, suite, params)
        finally:
            scheduler.end_request(inst.id)
        summary = self._summary(suite, samples)
        with SessionLocal() as db:
            for i, s in enumerate(samples):
                db.add(BenchmarkSample(run_id=run_id, idx=i, **s))
            run = db.get(BenchmarkRun, run_id)
            run.environment = env
            run.summary = summary
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        ctx.set_result({"run_id": str(run_id), "summary": summary})
        if cfg.get("unload_after", not was_loaded):
            scheduler.unload_model(model_id)

    def _one(self, model, messages, params, max_tokens, ctx) -> dict:
        try:
            r = llm.chat_measured(model, messages, {**params, "max_tokens": max_tokens}, cancel_check=ctx.cancelled)
            return {"ttft_ms": r["ttft_ms"], "latency_ms": r["latency_ms"], "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"], "tokens_per_s": r["tokens_per_s"],
                    "output": r["text"][:4000], "meta": {"token_count_source": r["token_count_source"],
                                                         "finish_reason": r["finish_reason"]}}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)[:1000], "meta": {}}

    def _perf(self, ctx, model, suite, params) -> list[dict]:
        out = []
        msgs = [{"role": "user", "content": suite["prompt"]}]
        # Warm-up (excluded from results).
        self._one(model, msgs, params, 8, ctx)
        steps = sum(suite["repeats"] * c for c in suite["concurrency"])
        done = 0
        for conc in suite["concurrency"]:
            for rep in range(suite["repeats"]):
                ctx.raise_if_cancelled()
                with ThreadPoolExecutor(max_workers=conc) as ex:
                    res = list(ex.map(lambda _: self._one(model, msgs, params, suite["max_tokens"], ctx), range(conc)))
                wall = max((r.get("latency_ms") or 0) for r in res)
                tot_tokens = sum((r.get("completion_tokens") or 0) for r in res)
                for r in res:
                    r["concurrency"] = conc
                    r["case_id"] = f"c{conc}-r{rep}"
                    r["meta"]["batch_throughput_tps"] = round(tot_tokens / (wall / 1000), 2) if wall else None
                    out.append(r)
                done += conc
                ctx.progress(0.05 + 0.9 * done / steps, f"concurrency {conc}, repeat {rep + 1}")
        return out

    def _quality(self, ctx, model, suite, params) -> list[dict]:
        out = []
        cases = suite["cases"]
        for i, case in enumerate(cases):
            ctx.raise_if_cancelled()
            prompt = suite["template"].format(**case)
            r = self._one(model, [{"role": "user", "content": prompt}], params, 512, ctx)
            r["case_id"] = case["id"]
            r["concurrency"] = 1
            r["score"] = score(suite["scorer"], case, r.get("output") or "") if not r.get("error") else 0.0
            out.append(r)
            ctx.progress(0.05 + 0.9 * (i + 1) / len(cases), f"{i + 1}/{len(cases)}")
        return out

    def _summary(self, suite, samples) -> dict:
        ok = [s for s in samples if not s.get("error")]
        summ: dict = {"samples": len(samples), "errors": len(samples) - len(ok),
                      "error_rate": round((len(samples) - len(ok)) / len(samples), 3) if samples else None}
        if suite["kind"] == "llm_perf":
            by_c: dict = {}
            for s in ok:
                by_c.setdefault(s["concurrency"], []).append(s)
            summ["by_concurrency"] = {}
            for c, ss in sorted(by_c.items()):
                ttft = [s["ttft_ms"] for s in ss if s.get("ttft_ms") is not None]
                tps = [s["tokens_per_s"] for s in ss if s.get("tokens_per_s")]
                lat = [s["latency_ms"] for s in ss if s.get("latency_ms")]
                thr = [s["meta"].get("batch_throughput_tps") for s in ss if s["meta"].get("batch_throughput_tps")]
                summ["by_concurrency"][str(c)] = {
                    "ttft_ms_p50": _pct(ttft, 0.5), "ttft_ms_p90": _pct(ttft, 0.9),
                    "tokens_per_s_mean": round(statistics.mean(tps), 2) if tps else None,
                    "latency_ms_p50": _pct(lat, 0.5), "throughput_tps_mean": round(statistics.mean(thr), 2) if thr else None,
                }
            first = summ["by_concurrency"].get("1", {})
            summ.update(ttft_ms_p50=first.get("ttft_ms_p50"), tokens_per_s=first.get("tokens_per_s_mean"))
        else:
            scores = [s.get("score") or 0 for s in samples]
            summ["score"] = round(sum(scores) / len(scores), 4) if scores else None
            summ["scoring"] = "deterministic"
        return summ


@register
class ComparisonJob(Handler):
    """Runs the same prompt on several models sequentially (never assumes co-residency)."""

    type = "comparison"

    def run(self, ctx: JobContext) -> None:
        cid = uuid.UUID(ctx.params["comparison_id"])
        with SessionLocal() as db:
            comp = db.get(Comparison, cid)
            model_ids = [uuid.UUID(m) for m in comp.model_ids]
            msgs = ([{"role": "system", "content": comp.system_prompt}] if comp.system_prompt else []) + \
                [{"role": "user", "content": comp.prompt}]
            settings = comp.settings or {}
            comp.results = [{"model_id": str(m), "status": "pending"} for m in model_ids]
            db.commit()
        for i, mid in enumerate(model_ids):
            ctx.raise_if_cancelled()
            self._update(cid, i, {"status": "loading"})
            ctx.progress(i / len(model_ids), f"Model {i + 1}/{len(model_ids)}", force=True)
            try:
                with SessionLocal() as db:
                    was_loaded = bool(db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == mid)))
                inst = scheduler.ensure_loaded(mid)
                with SessionLocal() as db:
                    model = db.get(RegisteredModel, mid)
                self._update(cid, i, {"status": "running", "alias": model.alias, "backend": model.backend})
                scheduler.begin_request(inst.id)
                buf: list[str] = []
                try:
                    r = llm.chat_measured(model, msgs, {k: settings[k] for k in ("temperature", "max_tokens", "top_p", "seed")
                                                        if k in settings}, on_delta=buf.append, cancel_check=ctx.cancelled)
                finally:
                    scheduler.end_request(inst.id)
                self._update(cid, i, {"status": "done", "output": r["text"], "reasoning": r.get("reasoning"),
                                      "metrics": {k: r[k] for k in ("ttft_ms", "latency_ms", "prompt_tokens",
                                                                    "completion_tokens", "tokens_per_s",
                                                                    "token_count_source", "finish_reason")},
                                      "load_seconds": inst.load_seconds, "revision": model.revision,
                                      "quantization": model.quantization})
                # Keep at most one large model resident: unload models we loaded unless it is the last one.
                if not was_loaded and i < len(model_ids) - 1:
                    scheduler.unload_model(mid)
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                self._update(cid, i, {"status": "error", "error": getattr(e, "message", str(e))[:2000]})
        ctx.set_result({"comparison_id": str(cid)})

    def _update(self, cid, idx, patch: dict) -> None:
        with SessionLocal() as db:
            comp = db.get(Comparison, cid)
            res = list(comp.results or [])
            res[idx] = {**res[idx], **patch}
            comp.results = res
            db.commit()
