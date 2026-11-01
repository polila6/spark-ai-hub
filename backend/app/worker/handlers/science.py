"""Scientific batch inference through trusted adapters running in the isolated ML worker."""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone

from ...config import get_settings
from ...db import SessionLocal
from ...engines import get_engine
from ...models import Dataset, Experiment, RegisteredModel
from ...services import scheduler
from ...services.scheduler import Requirement
from ..context import JobContext
from . import Handler, register


@register
class SciInfer(Handler):
    type = "sci_infer"

    def requirements(self, db, job):
        m = db.get(RegisteredModel, uuid.UUID(job.params["model_id"]))
        mem = max(1.0, (m.size_bytes or 0) / 1e9 * 3 + 1.0) if m else 2.0
        return Requirement(memory_gb=round(mem, 2), note="3 × artifact size + 1 GB")

    def run(self, ctx: JobContext) -> None:
        s = get_settings()
        with SessionLocal() as db:
            m = db.get(RegisteredModel, uuid.UUID(ctx.params["model_id"]))
            ds = db.get(Dataset, uuid.UUID(ctx.params["dataset_id"]))
            exp = db.get(Experiment, uuid.UUID(ctx.params["experiment_id"])) if ctx.params.get("experiment_id") else None
            if m is None or m.source != "custom":
                raise ValueError("Scientific model not found")
            if ds is None or (ds.owner_id != ctx.owner_id and ds.visibility.value == "private"):
                raise PermissionError("Dataset not accessible")
            adapter = (m.compat or {}).get("adapter")
            model_rel, ds_rel = m.path, ds.path
            if exp:
                exp.status = "running"
                exp.job_ids = list({*(exp.job_ids or []), str(ctx.job_id)})
                db.commit()
        workdir = s.outputs_dir / "mlworker" / str(ctx.job_id)
        workdir.mkdir(parents=True, exist_ok=True)
        ctx.progress(0.05, "Starting ML worker", force=True)
        scheduler.ensure_engine("mlworker")
        ctx.progress(0.2, f"Running adapter {adapter}", force=True)
        res = get_engine("mlworker").call("/sci/infer", {  # type: ignore[attr-defined]
            "job_id": str(ctx.job_id), "adapter": adapter, "model_dir": f"/models/custom/{model_rel}",
            "dataset": f"/data/datasets/{ds_rel}", "output_dir": f"/data/outputs/{ctx.job_id}",
            "options": ctx.params.get("options", {})})
        scheduler.touch_engine("mlworker")
        dest = s.experiments_dir / str(ctx.owner_id) / str(ctx.job_id)
        dest.mkdir(parents=True, exist_ok=True)
        arts = []
        for name in res.get("files", []):
            src = (workdir / name).resolve()
            if workdir.resolve() not in src.parents:
                continue
            tgt = dest / src.name
            shutil.move(str(src), tgt)
            arts.append(ctx.add_artifact(tgt, "predictions" if tgt.suffix in (".csv", ".npy", ".json") else "report",
                                         {"adapter": adapter, "model": m.alias, "dataset": ds.name}))
        shutil.rmtree(workdir, ignore_errors=True)
        metrics = res.get("metrics", {})
        ctx.actual.update(device=res.get("device"), processing_s=res.get("processing_s"),
                          peak_memory_mb=res.get("peak_memory_mb"))
        with SessionLocal() as db:
            if ctx.params.get("experiment_id"):
                exp = db.get(Experiment, uuid.UUID(ctx.params["experiment_id"]))
                exp.metrics = {**(exp.metrics or {}), str(ctx.job_id): {
                    "metrics": metrics, "rows": res.get("rows"), "device": res.get("device"),
                    "processing_s": res.get("processing_s"), "ts": datetime.now(timezone.utc).isoformat()}}
                exp.status = "completed"
                db.commit()
        ctx.set_result({"metrics": metrics, "rows": res.get("rows"), "artifacts": arts,
                        "device": res.get("device"), "summary": json.dumps(metrics)[:2000]})
