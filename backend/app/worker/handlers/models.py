"""Model lifecycle jobs: smoke tests (real inference) after installation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from ...db import SessionLocal
from ...engines import LLM_ENGINES
from ...models import RegisteredModel, RuntimeInstance
from ...services import llm, registry, scheduler
from ..context import JobContext
from . import Handler, register

SMOKE_PROMPT = [{"role": "user", "content": "Reply with exactly one word: OK"}]


@register
class SmokeTest(Handler):
    type = "smoke_test"

    def run(self, ctx: JobContext) -> None:
        model_id = uuid.UUID(ctx.params["model_id"])
        with SessionLocal() as db:
            model = db.get(RegisteredModel, model_id)
            was_loaded = bool(db.scalar(select(RuntimeInstance.id).where(RuntimeInstance.model_id == model_id)))
            backend = model.backend
            modality = model.modality
        result: dict = {"ts": datetime.now(timezone.utc).isoformat(), "engine": backend}
        if backend in LLM_ENGINES:
            ctx.progress(0.1, f"Loading model in {backend}", force=True)
            inst = scheduler.ensure_loaded(model_id)
            result["load_seconds"] = inst.load_seconds
            result["measured_memory_gb"] = inst.measured_memory_gb
            ctx.progress(0.7, "Running test inference", force=True)
            scheduler.begin_request(inst.id)
            try:
                with SessionLocal() as db:
                    model = db.get(RegisteredModel, model_id)
                if modality == "embedding":
                    out = llm.embeddings(model, {"input": ["smoke test"]})
                    dim = len(out["data"][0]["embedding"])
                    result.update(ok=dim > 0, embedding_dim=dim)
                else:
                    out = llm.chat_measured(model, SMOKE_PROMPT, {"max_tokens": 32, "temperature": 0})
                    result.update(ok=bool(out["text"].strip() or out.get("reasoning")), output=out["text"][:200],
                                  ttft_ms=out["ttft_ms"], tokens_per_s=out["tokens_per_s"],
                                  completion_tokens=out["completion_tokens"])
            finally:
                scheduler.end_request(inst.id)
            if not was_loaded:
                ctx.progress(0.9, "Unloading (installation does not keep models resident)", force=True)
                scheduler.unload_model(model_id)
        elif backend == "comfyui":
            from .media import run_checkpoint_smoke

            result.update(run_checkpoint_smoke(ctx, model_id))
        elif backend == "mlworker":
            from .audio import run_audio_smoke

            result.update(run_audio_smoke(ctx, model_id))
        else:
            result.update(ok=False, error=f"No smoke test for backend {backend}")
        with SessionLocal() as db:
            model = db.get(RegisteredModel, model_id)
            model.smoke_test = result
            compat = dict(model.compat or {})
            ver = set(compat.get("verified_engines") or [])
            if result.get("ok"):
                ver.add(backend)
                compat["overall"] = "verified"
            compat["verified_engines"] = sorted(ver)
            model.compat = compat
            registry.history(db, model, "smoke_test.ok" if result.get("ok") else "smoke_test.failed", result)
            db.commit()
        ctx.set_result(result)
        if not result.get("ok"):
            raise RuntimeError(f"Smoke test failed: {result.get('error') or result.get('output')}")
