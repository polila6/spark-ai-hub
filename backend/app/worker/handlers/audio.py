"""Audio jobs (speech-to-text, text-to-speech) executed by the isolated ML worker."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from ...config import get_settings
from ...db import SessionLocal
from ...engines import get_engine
from ...models import Artifact, RegisteredModel
from ...services import scheduler
from ...services.scheduler import Requirement
from ..context import JobContext
from . import Handler, register


def _model(model_id: str) -> RegisteredModel:
    with SessionLocal() as db:
        m = db.get(RegisteredModel, uuid.UUID(model_id))
        if m is None or m.backend != "mlworker" or not m.path:
            raise ValueError("Audio model not installed")
        return m


def _workdir(job_id) -> tuple[Path, str]:
    s = get_settings()
    d = s.outputs_dir / "mlworker" / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d, f"/data/outputs/{job_id}"


def _collect(ctx: JobContext, workdir: Path, names: list[str], kind: str, meta: dict) -> list[dict]:
    s = get_settings()
    dest = s.outputs_dir / str(ctx.owner_id) / str(ctx.job_id)
    dest.mkdir(parents=True, exist_ok=True)
    arts = []
    for n in names:
        src = (workdir / n).resolve()
        if workdir.resolve() not in src.parents:
            continue
        target = dest / src.name
        shutil.move(str(src), target)
        arts.append(ctx.add_artifact(target, kind, meta))
    shutil.rmtree(workdir, ignore_errors=True)
    return arts


@register
class AudioTranscribe(Handler):
    type = "audio_transcribe"

    def requirements(self, db, job):
        return Requirement(memory_gb=6.0, note="whisper-class model estimate")

    def run(self, ctx: JobContext) -> None:
        m = _model(ctx.params["model_id"])
        s = get_settings()
        with SessionLocal() as db:
            a = db.get(Artifact, uuid.UUID(ctx.params["artifact_id"]))
            if a is None or a.owner_id != ctx.owner_id:
                raise PermissionError("Audio file not found")
            src = s.data_root / a.path
            fname = a.filename
        workdir, inner = _workdir(ctx.job_id)
        shutil.copyfile(src, workdir / f"input{Path(fname).suffix.lower()}")
        ctx.progress(0.05, "Starting ML worker", force=True)
        scheduler.ensure_engine("mlworker")
        ctx.progress(0.2, "Transcribing", force=True)
        res = get_engine("mlworker").call("/asr", {  # type: ignore[attr-defined]
            "job_id": str(ctx.job_id), "model_path": f"/models/hf/{m.path}",
            "input": f"{inner}/input{Path(fname).suffix.lower()}", "output_dir": inner,
            "language": ctx.params.get("language"), "task": ctx.params.get("task", "transcribe"),
            "timestamps": bool(ctx.params.get("timestamps", True))})
        scheduler.touch_engine("mlworker")
        meta = {"model": m.alias, "revision": m.revision, "language": res.get("language"),
                "duration_s": res.get("duration_s"), "processing_s": res.get("processing_s"),
                "task": ctx.params.get("task", "transcribe")}
        arts = _collect(ctx, workdir, res.get("files", []), "text", meta)
        ctx.actual.update(processing_s=res.get("processing_s"), realtime_factor=res.get("realtime_factor"))
        ctx.set_result({"text": res.get("text", "")[:20000], "language": res.get("language"),
                        "segments": (res.get("segments") or [])[:2000], "artifacts": arts, "meta": meta})


@register
class AudioTTS(Handler):
    type = "audio_tts"

    def requirements(self, db, job):
        return Requirement(memory_gb=3.0, note="VITS/MMS-class model estimate")

    def run(self, ctx: JobContext) -> None:
        m = _model(ctx.params["model_id"])
        workdir, inner = _workdir(ctx.job_id)
        scheduler.ensure_engine("mlworker")
        ctx.progress(0.3, "Synthesising", force=True)
        res = get_engine("mlworker").call("/tts", {  # type: ignore[attr-defined]
            "job_id": str(ctx.job_id), "model_path": f"/models/hf/{m.path}", "text": ctx.params["text"][:5000],
            "output_dir": inner, "seed": ctx.params.get("seed", 0), "speaking_rate": ctx.params.get("speaking_rate", 1.0)})
        scheduler.touch_engine("mlworker")
        meta = {"model": m.alias, "revision": m.revision, "sample_rate": res.get("sample_rate"),
                "duration_s": res.get("duration_s"), "processing_s": res.get("processing_s")}
        arts = _collect(ctx, workdir, res.get("files", []), "audio", meta)
        ctx.set_result({"artifacts": arts, "meta": meta})


def run_audio_smoke(ctx: JobContext, model_id: uuid.UUID) -> dict:
    m = _model(str(model_id))
    if m.task == "image-classification":
        return {"ok": True, "skipped_inference": True,
                "note": "Weights downloaded and verified; inference is exercised through a Scientific Lab "
                        "image_classifier model registered from this snapshot"}
    workdir, inner = _workdir(ctx.job_id)
    scheduler.ensure_engine("mlworker")
    eng = get_engine("mlworker")
    if m.task == "automatic-speech-recognition":
        res = eng.call("/asr", {"job_id": str(ctx.job_id), "model_path": f"/models/hf/{m.path}",  # type: ignore[attr-defined]
                                "input": "__synthetic_tone__", "output_dir": inner, "timestamps": False})
        out = {"ok": "text" in res, "processing_s": res.get("processing_s"), "note": "synthetic 2 s tone input"}
    else:
        res = eng.call("/tts", {"job_id": str(ctx.job_id), "model_path": f"/models/hf/{m.path}",  # type: ignore[attr-defined]
                                "text": "Hola, esto es una prueba.", "output_dir": inner})
        out = {"ok": bool(res.get("files")), "duration_s": res.get("duration_s"), "processing_s": res.get("processing_s")}
    shutil.rmtree(workdir, ignore_errors=True)
    return out
