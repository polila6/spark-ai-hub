"""Model download handlers: Hugging Face snapshots and Ollama pulls."""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from ...config import get_settings
from ...db import SessionLocal
from ...engines import get_engine
from ...models import DownloadStatus, DownloadTask, JobStatus, ModelStatus, RegisteredModel
from ...services import registry
from ...services.jobs import create_job
from ...settings_store import get_setting
from ..context import Cancelled, JobContext
from . import Handler, register

GB = 1e9


def _snapshot_child(repo_id: str, revision: str, allow: list | None, cache_dir: str, token: str | None, q) -> None:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(repo_id, revision=revision, allow_patterns=allow or None, cache_dir=cache_dir,
                                 token=token or False, max_workers=8)
        q.put({"ok": True, "path": path})
    except Exception as e:  # noqa: BLE001
        q.put({"ok": False, "error": f"{type(e).__name__}: {e}"[:2000]})


def _repo_bytes(cache_dir: Path, repo_id: str) -> int:
    d = cache_dir / f"models--{repo_id.replace('/', '--')}" / "blobs"
    total = 0
    if d.exists():
        for f in d.iterdir():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(16 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@register
class HfDownload(Handler):
    type = "hf_download"

    def run(self, ctx: JobContext) -> None:
        s = get_settings()
        task_id = uuid.UUID(ctx.params["download_task_id"])
        model_id = uuid.UUID(ctx.params["model_id"])
        with SessionLocal() as db:
            task = db.get(DownloadTask, task_id)
            token = get_setting(db, "hf_token") if ctx.params.get("use_token") else None
            repo_id, revision, allow = task.repo_id, task.revision, task.allow_patterns
            total = task.total_bytes
            task.status = DownloadStatus.running
            task.job_id = ctx.job_id
            task.error = None
            model = db.get(RegisteredModel, model_id)
            registry.mark(db, model, ModelStatus.downloading, None, event="download.start",
                          details={"repo": repo_id, "revision": revision})
            db.commit()
        ctx.log(f"Downloading {repo_id}@{revision[:12]} ({total / GB:.2f} GB expected)")
        with SessionLocal() as db:
            files = (db.get(RegisteredModel, model_id).compat or {}).get("files") or []
        res: dict = {}
        for attempt in range(1, 4):
            res = self._fetch(ctx, task_id, model_id, repo_id, revision, allow, token, total)
            if res.get("ok"):
                missing = [f["path"] for f in files if not (Path(res["path"]) / f["path"]).exists()]
                if not missing:
                    break
                # huggingface_hub falls back to a partial local snapshot when the Hub is unreachable.
                res = {"ok": False, "error": f"Hub unreachable or incomplete snapshot (missing {missing[:3]})"}
            if attempt < 3:
                wait = 15 * attempt
                ctx.log(f"Attempt {attempt} failed ({res.get('error', '')[:200]}); retrying in {wait}s", "warning")
                ctx.progress(0.0, f"Retrying in {wait}s (attempt {attempt + 1}/3)", force=True)
                time.sleep(wait)
                ctx.raise_if_cancelled()
        self._finish(ctx, task_id, model_id, res, files)

    def _fetch(self, ctx: JobContext, task_id, model_id, repo_id, revision, allow, token, total) -> dict:
        s = get_settings()
        start_bytes = _repo_bytes(s.hf_cache_dir, repo_id)
        q = mp.get_context("spawn").Queue()
        proc = mp.get_context("spawn").Process(target=_snapshot_child, daemon=True,
                                               args=(repo_id, revision, allow, str(s.hf_cache_dir), token, q))
        proc.start()
        last_t, last_b = time.time(), start_bytes
        paused = False
        try:
            while proc.is_alive():
                time.sleep(2)
                cur = _repo_bytes(s.hf_cache_dir, repo_id)
                now = time.time()
                speed = max(0.0, (cur - last_b) / (now - last_t)) if now > last_t else 0.0
                last_t, last_b = now, cur
                with SessionLocal() as db:
                    task = db.get(DownloadTask, task_id)
                    task.downloaded_bytes = min(cur, total) if total else cur
                    task.speed_bps = speed
                    want_pause = task.status == DownloadStatus.paused
                    db.commit()
                frac = (cur / total) if total else 0.0
                ctx.progress(min(frac, 0.99) * 0.9,
                             f"{cur / GB:.2f}/{total / GB:.2f} GB · {speed / 1e6:.1f} MB/s")
                if want_pause or ctx.cancelled():
                    paused = want_pause
                    proc.terminate()
                    proc.join(10)
                    raise Cancelled()
            proc.join()
            return q.get(timeout=10) if not q.empty() else {"ok": False, "error": f"exit code {proc.exitcode}"}
        except Cancelled:
            with SessionLocal() as db:
                task = db.get(DownloadTask, task_id)
                task.status = DownloadStatus.paused if paused else DownloadStatus.cancelled
                model = db.get(RegisteredModel, model_id)
                registry.mark(db, model, ModelStatus.discovered,
                              "Download paused (partial files kept, resumable)" if paused else "Download cancelled",
                              event="download.paused" if paused else "download.cancelled")
                db.commit()
            ctx.log("Download paused; partial files kept for resume" if paused else "Download cancelled")
            raise

    def _finish(self, ctx: JobContext, task_id, model_id, res: dict, files: list) -> None:
        s = get_settings()
        if not res.get("ok"):
            with SessionLocal() as db:
                task = db.get(DownloadTask, task_id)
                task.status = DownloadStatus.failed
                task.error = res.get("error")
                model = db.get(RegisteredModel, model_id)
                registry.mark(db, model, ModelStatus.failed, res.get("error"), event="download.failed")
                db.commit()
            raise RuntimeError(f"Download failed: {res.get('error')}")

        snap = Path(res["path"])
        # ---- verification
        ctx.progress(0.9, "Verifying integrity (sha256)", force=True)
        with SessionLocal() as db:
            task = db.get(DownloadTask, task_id)
            task.status = DownloadStatus.verifying
            db.commit()
        problems, checked = [], 0
        for i, f in enumerate(files):
            ctx.raise_if_cancelled()
            p = snap / f["path"]
            if not p.exists():
                problems.append({"path": f["path"], "error": "missing"})
                continue
            size = p.stat().st_size
            if f.get("size") and size != f["size"]:
                problems.append({"path": f["path"], "error": f"size {size} != {f['size']}"})
                continue
            if f.get("sha256"):
                if _sha256(p) != f["sha256"]:
                    problems.append({"path": f["path"], "error": "sha256 mismatch"})
                    continue
                checked += 1
            ctx.progress(0.9 + 0.08 * (i + 1) / max(len(files), 1), f"Verified {i + 1}/{len(files)} files")
        verification = {"ok": not problems, "sha256_checked": checked, "files": len(files), "problems": problems}
        rel = str(snap.relative_to(s.hf_cache_dir))
        with SessionLocal() as db:
            task = db.get(DownloadTask, task_id)
            task.verification = verification
            task.downloaded_bytes = task.total_bytes
            model = db.get(RegisteredModel, model_id)
            if problems:
                task.status = DownloadStatus.failed
                task.error = "Integrity verification failed"
                registry.mark(db, model, ModelStatus.failed, "Integrity verification failed", event="verify.failed",
                              details=verification)
                db.commit()
                raise RuntimeError(f"Integrity verification failed: {problems[:5]}")
            task.status = DownloadStatus.completed
            model.path = rel
            model.size_bytes = sum(f.get("size") or 0 for f in files)
            registry.mark(db, model, ModelStatus.downloaded, None, event="download.ok", details=verification)
            registry.ensure_version(db, model, files, verified=True)
            db.commit()
        ctx.log(f"Integrity verified: {checked} LFS files sha256-checked, {len(files)} files total")
        _install(ctx, model_id)


def _install(ctx: JobContext, model_id: uuid.UUID) -> None:
    """Configure the selected backend for a downloaded snapshot (does NOT load the model)."""
    s = get_settings()
    with SessionLocal() as db:
        model = db.get(RegisteredModel, model_id)
        registry.mark(db, model, ModelStatus.installing, None)
        db.commit()
        links = []
        if model.backend == "comfyui":
            kind = (model.compat or {}).get("comfyui_kind", "checkpoints")
            snap = s.hf_cache_dir / model.path
            for rel in (model.compat or {}).get("comfyui_files", []):
                src = (snap / rel).resolve()
                dst_dir = s.models_dir / "comfyui" / kind
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / Path(rel).name
                if not dst.exists():
                    os.link(src, dst)  # hard link: no duplicate disk usage, visible inside ComfyUI container
                links.append(f"{kind}/{dst.name}")
            compat = dict(model.compat or {})
            compat["comfyui_links"] = links
            model.compat = compat
        registry.mark(db, model, ModelStatus.installed, None, event="install.ok",
                      details={"backend": model.backend, "links": links})
        _notify_ready(db, model)
        db.commit()
        smoke = ctx.params.get("smoke_test", True) and model.backend != "files"  # plain files have nothing to run
        owner = model.owner_id
    ctx.progress(1.0, "Installed", force=True)
    ctx.set_result({"model_id": str(model_id), "installed": True})
    _maybe_make_main(ctx, model_id)
    if smoke:
        with SessionLocal() as db:
            from ...models import SchedClass, User

            user = db.get(User, owner) if owner else None
            if user:
                j = create_job(db, user, "smoke_test", {"model_id": str(model_id)}, title="Smoke test",
                               sched_class=SchedClass.batch)
                ctx.log(f"Smoke test queued as job {j.id}")


@register
class OllamaPull(Handler):
    type = "ollama_pull"

    def run(self, ctx: JobContext) -> None:
        model_id = uuid.UUID(ctx.params["model_id"])
        task_id = uuid.UUID(ctx.params["download_task_id"])
        engine = get_engine("ollama")
        with SessionLocal() as db:
            model = db.get(RegisteredModel, model_id)
            name = model.engine_ref
            registry.mark(db, model, ModelStatus.downloading, None, event="pull.start", details={"name": name})
            task = db.get(DownloadTask, task_id)
            task.status = DownloadStatus.running
            task.job_id = ctx.job_id
            db.commit()
        per_digest: dict[str, tuple[int, int]] = {}
        try:
            for ev in engine.pull(name):  # type: ignore[attr-defined]
                if ctx.cancelled():
                    raise Cancelled()
                if ev.get("digest") and ev.get("total"):
                    per_digest[ev["digest"]] = (ev.get("completed") or 0, ev["total"])
                done = sum(c for c, _ in per_digest.values())
                tot = sum(t for _, t in per_digest.values())
                ctx.progress((done / tot * 0.95) if tot else 0.0, f"{ev.get('status')} {done / GB:.2f}/{tot / GB:.2f} GB")
                with SessionLocal() as db:
                    task = db.get(DownloadTask, task_id)
                    task.downloaded_bytes, task.total_bytes = done, max(tot, task.total_bytes or 0)
                    db.commit()
        except Cancelled:
            with SessionLocal() as db:
                db.get(DownloadTask, task_id).status = DownloadStatus.cancelled
                registry.mark(db, db.get(RegisteredModel, model_id), ModelStatus.discovered, "Pull cancelled")
                db.commit()
            raise
        except Exception as e:
            with SessionLocal() as db:
                t = db.get(DownloadTask, task_id)
                t.status, t.error = DownloadStatus.failed, str(e)[:2000]
                registry.mark(db, db.get(RegisteredModel, model_id), ModelStatus.failed, str(e)[:2000])
                db.commit()
            raise
        ctx.log("Ollama verified layer digests (sha256) during pull")
        register_ollama_details(model_id)
        _maybe_make_main(ctx, model_id)
        with SessionLocal() as db:
            t = db.get(DownloadTask, task_id)
            t.status = DownloadStatus.completed
            t.verification = {"ok": True, "method": "ollama sha256 layer digests"}
            db.commit()
        ctx.progress(1.0, "Installed", force=True)
        ctx.set_result({"model_id": str(model_id), "installed": True})
        if ctx.params.get("smoke_test", True):
            with SessionLocal() as db:
                from ...models import SchedClass, User

                m = db.get(RegisteredModel, model_id)
                user = db.get(User, m.owner_id) if m.owner_id else None
                if user:
                    create_job(db, user, "smoke_test", {"model_id": str(model_id)}, title="Smoke test",
                               sched_class=SchedClass.batch)


def register_ollama_details(model_id: uuid.UUID) -> None:
    engine = get_engine("ollama")
    with SessionLocal() as db:
        model = db.get(RegisteredModel, model_id)
        info = engine.show(model.engine_ref)  # type: ignore[attr-defined]
        details = info.get("details", {})
        mi = info.get("model_info", {}) or {}
        caps = set(info.get("capabilities") or [])
        mapped = []
        if "completion" in caps:
            mapped += ["chat", "completion", "streaming"]
        if "tools" in caps:
            mapped.append("tools")
        if "vision" in caps:
            mapped.append("vision")
        if "embedding" in caps:
            mapped.append("embeddings")
        if "thinking" in caps:
            mapped.append("reasoning")
        ctx_len = next((v for k, v in mi.items() if k.endswith(".context_length")), None)
        size = next((m.get("size") for m in engine.list_local() if m.get("name") == model.engine_ref), None)  # type: ignore[attr-defined]
        model.capabilities = mapped
        model.modality = "embedding" if ("embeddings" in mapped and "chat" not in mapped) else "text"
        model.architecture = details.get("family") or mi.get("general.architecture")
        model.quantization = details.get("quantization_level")
        model.format = details.get("format") or "gguf"
        model.context_length = int(ctx_len) if ctx_len else None
        ps = details.get("parameter_size")
        try:
            model.parameters_b = float(str(ps).rstrip("BbMm")) / (1000 if str(ps).upper().endswith("M") else 1) if ps else None
        except ValueError:
            pass
        model.size_bytes = size
        if size:
            model.estimated_memory_gb = round(size / GB * 1.15 + 1.0, 2)
        registry.mark(db, model, ModelStatus.installed, None, event="install.ok",
                      details={"capabilities": mapped, "ollama_capabilities": sorted(caps)})
        _notify_ready(db, model)
        db.commit()


def _maybe_make_main(ctx: JobContext, model_id: uuid.UUID) -> None:
    """Complete a "download and make main" request (pending-main marker) once the install succeeded."""
    from ...services import main_model

    if main_model.on_installed(model_id):
        ctx.log("Installed model is now the main model; loading it in the background")


def _notify_ready(db, model) -> None:
    if model.owner_id and model.backend != "comfyui":
        from ...services import notify

        if model.backend == "files":
            notify.notify(db, model.owner_id, "model_ready", f"Archivos descargados: {model.alias}",
                          "Están verificados y guardados en el Spark; no se cargan en ningún motor.", link=f"/models/{model.id}")
            return
        notify.notify(db, model.owner_id, "model_ready", f"Modelo listo: {model.alias}",
                      "Ya puedes usarlo en el chat, en la API o compararlo.", link=f"/models/{model.id}")


def find_existing_download(db, repo_id: str, revision: str):
    return db.scalar(select(DownloadTask).where(DownloadTask.repo_id == repo_id, DownloadTask.revision == revision,
                                                DownloadTask.status.in_([DownloadStatus.running, DownloadStatus.pending])))


_ = JobStatus  # re-export for type checkers
