"""Image and video generation jobs executed through registered ComfyUI workflows."""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import func, select

from ...config import get_settings
from ...db import SessionLocal
from ...engines import get_engine
from ...models import Artifact, Job, JobStatus, RegisteredModel
from ...services import scheduler
from ...services.scheduler import Requirement
from ...services.workflows import WORKFLOWS, build, comfy_output_path, estimate_memory, validate
from ...settings_store import get_setting
from ..context import Cancelled, JobContext
from . import Handler, register


def _ws_progress(client_id: str, prompt_id: str, ctx: JobContext, stop: threading.Event, total_nodes: int) -> None:
    """Listen to ComfyUI websocket progress events (best effort)."""
    try:
        from websockets.sync.client import connect
    except ImportError:
        return
    url = get_settings().comfyui_url.replace("http", "ws", 1) + f"/ws?clientId={client_id}"
    done_nodes = 0
    try:
        with connect(url, open_timeout=10, max_size=None) as ws:
            while not stop.is_set():
                try:
                    msg = ws.recv(timeout=1)
                except TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    continue  # preview frames
                ev = json.loads(msg)
                d = ev.get("data", {})
                if d.get("prompt_id") not in (None, prompt_id):
                    continue
                if ev.get("type") == "progress" and d.get("max"):
                    frac = d["value"] / d["max"]
                    ctx.progress(0.1 + 0.8 * frac, f"Sampling step {d['value']}/{d['max']}")
                elif ev.get("type") == "executing" and d.get("node"):
                    done_nodes += 1
                    ctx.progress(0.1 + 0.05 * min(done_nodes / max(total_nodes, 1), 1), f"Node {d['node']}")
    except Exception:  # noqa: BLE001
        return


def _stage_input(owner_id, artifact_id: str | None) -> str | None:
    """Copy the input image (a media artifact or a file of the owner's drive) into ComfyUI's input folder."""
    if not artifact_id:
        return None
    from ...models import UserFile

    s = get_settings()
    with SessionLocal() as db:
        a = db.get(Artifact, uuid.UUID(artifact_id))
        if a is None:
            uf = db.get(UserFile, uuid.UUID(artifact_id))
            if uf is None or uf.owner_id != owner_id or uf.kind != "image":
                raise PermissionError("Input image not found or not owned by the job owner")
            src, fname = s.data_root / uf.path, uf.filename
        elif a.owner_id != owner_id or not a.mime.startswith("image/"):
            raise PermissionError("Input image not found or not owned by the job owner")
        else:
            src, fname = s.data_root / a.path, a.filename
    name = f"sah_{uuid.uuid4().hex}{Path(fname).suffix.lower() or '.png'}"
    shutil.copyfile(src, s.data_root / "cache" / "comfyui" / "input" / name)
    return name


def run_workflow(ctx: JobContext, wf_id: str, inputs: dict, *, register_outputs: bool = True) -> dict:
    wf = WORKFLOWS[wf_id]
    params = validate(wf, inputs)
    comfy = get_engine("comfyui")
    ctx.progress(0.02, "Starting ComfyUI", force=True)
    scheduler.ensure_engine("comfyui")
    input_name = _stage_input(ctx.owner_id, params.get("image")) if "image" in wf.params else None
    prefix = f"sah/{ctx.job_id}/out"
    graph = build(wf, params, input_name, prefix)
    client_id = uuid.uuid4().hex
    prompt_id = comfy.submit(graph, client_id)  # type: ignore[attr-defined]
    ctx.log(f"Submitted to ComfyUI (prompt {prompt_id})", data={"workflow": wf_id})
    stop = threading.Event()
    t = threading.Thread(target=_ws_progress, args=(client_id, prompt_id, ctx, stop, len(graph)), daemon=True)
    t.start()
    t0 = time.time()
    try:
        while True:
            if ctx.cancelled():
                comfy.cancel(prompt_id)
                raise Cancelled()
            h = comfy.history(prompt_id)  # type: ignore[attr-defined]
            if h:
                st = h.get("status", {})
                if st.get("status_str") == "error" or (st.get("completed") is False and st.get("messages")):
                    errs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                    if errs or st.get("status_str") == "error":
                        detail = errs[0][1] if errs else st
                        raise RuntimeError(f"ComfyUI execution error: {json.dumps(detail)[:3000]}")
                if st.get("completed") or h.get("outputs"):
                    break
            time.sleep(1.5)
    finally:
        stop.set()
        scheduler.touch_engine("comfyui")
    seconds = round(time.time() - t0, 2)
    outputs = []
    s = get_settings()
    dest = s.outputs_dir / str(ctx.owner_id) / str(ctx.job_id)
    dest.mkdir(parents=True, exist_ok=True)
    for _node, out in (h.get("outputs") or {}).items():
        for key in ("images", "gifs", "videos", "animated"):
            for item in out.get(key, []) if isinstance(out.get(key), list) else []:
                if not isinstance(item, dict) or item.get("type") != "output":
                    continue
                src = comfy_output_path(item)
                if not src.exists():
                    continue
                target = dest / src.name
                shutil.move(str(src), target)
                outputs.append(target)
    if not outputs:
        raise RuntimeError("Workflow finished without output files")
    meta = {"workflow": wf_id, "workflow_source": wf.source, "params": params, "generation_seconds": seconds,
            "models": [m.filename for m in wf.models], "prompt_id": prompt_id}
    arts = []
    if register_outputs:
        for p in outputs:
            m = dict(meta)
            if wf.kind == "video":
                m.update(frames=params.get("frames"), fps=params.get("fps"),
                         duration_s=round(params["frames"] / params["fps"], 2) if params.get("fps") else None)
            m.update(width=params.get("width"), height=params.get("height"))
            arts.append(ctx.add_artifact(p, wf.kind, m))
        (dest / "workflow.json").write_text(json.dumps({"api_graph": graph, "meta": meta}, indent=2))
        arts.append(ctx.add_artifact(dest / "workflow.json", "workflow", {"workflow": wf_id}))
    ctx.actual.update(generation_seconds=seconds)
    return {"artifacts": arts, "meta": meta, "files": [str(p) for p in outputs]}


class _MediaBase(Handler):
    def requirements(self, db, job: Job) -> Requirement:
        wf = WORKFLOWS[job.params["workflow_id"]]
        try:
            p = validate(wf, job.params.get("inputs", {}))
        except Exception:  # noqa: BLE001
            p = {}
        return Requirement(memory_gb=estimate_memory(wf, p), gpu_heavy=wf.gpu_heavy,
                           note=f"workflow {wf.id} estimate (heuristic)")

    def run(self, ctx: JobContext) -> None:
        res = run_workflow(ctx, ctx.params["workflow_id"], ctx.params.get("inputs", {}))
        ctx.set_result({"artifacts": res["artifacts"], "meta": res["meta"]})

    def cleanup(self, ctx: JobContext) -> None:
        # Release ComfyUI's cached weights when no further media work is queued (hybrid/on-demand).
        with SessionLocal() as db:
            if get_setting(db, "scheduler_mode") == "persistent":
                return
            pending = db.scalar(select(func.count()).select_from(Job).where(
                Job.type.in_(("image_generate", "video_generate")), Job.id != ctx.job_id,
                Job.status.in_([JobStatus.queued, JobStatus.preparing, JobStatus.running]))) or 0
        if not pending:
            get_engine("comfyui").free_memory()  # type: ignore[attr-defined]


@register
class ImageGenerate(_MediaBase):
    type = "image_generate"


@register
class VideoGenerate(_MediaBase):
    type = "video_generate"


def run_checkpoint_smoke(ctx: JobContext, model_id: uuid.UUID) -> dict:
    """Smoke test for a ComfyUI model file: run the smallest workflow that uses it."""
    with SessionLocal() as db:
        m = db.get(RegisteredModel, model_id)
        fname = Path(((m.compat or {}).get("comfyui_files") or [""])[0]).name
    wf = next((w for w in WORKFLOWS.values() if any(f.filename == fname for f in w.models)), None)
    if wf is None:
        return {"ok": False, "error": "No registered workflow uses this file"}
    from ...services.workflows import missing_models

    if missing_models(wf):
        return {"ok": True, "skipped_generation": True,
                "note": f"File installed; workflow {wf.id} still needs other files before a generation test"}
    inputs = {"prompt": "a lighthouse on a rocky coast at sunset, photo", "steps": 8, "seed": 42}
    if wf.mode == "txt2img":
        inputs.update(width=512, height=512)
    elif wf.mode == "t2v":
        inputs.update(width=480, height=320, frames=9, steps=10)
    elif wf.mode in ("img2img", "inpaint", "upscale", "i2v"):
        return {"ok": True, "skipped_generation": True, "note": "Requires an input image; tested via txt2img"}
    res = run_workflow(ctx, wf.id, inputs, register_outputs=True)
    return {"ok": True, "workflow": wf.id, "generation_seconds": res["meta"]["generation_seconds"],
            "artifacts": res["artifacts"][:1]}
