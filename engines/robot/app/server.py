"""HTTP server that a robot suite connects to: text to G1 motion (Kimodo) and physics checks (MuJoCo).

Public, on the Tailscale address: /generate, /physics, /models, /health.
Hub only, with the admin token: /_admin/*.

Models stay in memory while they fit. The text encoder (about 17 GB) loads only if the memory is free, is the first to leave
when the machine runs short, and embeddings are cached on disk so a repeated prompt does not need it at all.

The encoder Kimodo was trained on is Llama 3 8B, which needs access approved by Meta. Until it is installed Llama 3.1 8B stands
in for it, which is close but not the same: answers say so, its vectors are cached apart so none survive the change, and the
server moves to Llama 3 by itself once the download is complete."""
from __future__ import annotations

import collections
import hmac
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Request as HttpRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from . import normalize as n

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
log = logging.getLogger("robot")

ADMIN_TOKEN = os.environ.get("SAH_ROBOT_ADMIN_TOKEN", "")
MODELS_DIR = Path(os.environ.get("HF_HUB_CACHE", "/models/robot"))
CACHE_DIR = Path(os.environ.get("SAH_ROBOT_CACHE", "/cache/embeddings"))
AUTOLOAD = [m for m in os.environ.get("SAH_ROBOT_AUTOLOAD", n.DEFAULT_MODEL).split(",") if m]
MARGIN_GB = float(os.environ.get("SAH_ROBOT_MARGIN_GB", "2"))
ENCODER_GB = 17.0  # Llama 3 8B in bf16 with its adapters (Kimodo's documented ~17 GB)
MOTION_MODEL_GB = 2.0
LLAMA = "meta-llama/Meta-Llama-3-8B-Instruct"
PROVISIONAL = "RedHatAI/Llama-3.1-8B-Instruct"
ENCODER_REPOS = ("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp", "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised")
PROVISIONAL_NOTE = "Text encoded with Llama 3.1 while Llama 3 is not installed: motions may differ from the official encoder's."

app = FastAPI(title="Spark AI Hub robot server", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_private_network=True)

gpu = threading.RLock()  # one generation at a time
state_lock = threading.Lock()
models: dict[str, dict] = {}
encoder: Any = None
encoder_loaded_mode: str | None = None
busy: dict | None = None
history: collections.deque = collections.deque(maxlen=60)
downloads: dict[str, dict] = {}
loading: dict[str, str] = {}
last_error: str | None = None
generated = 0


def available_gb() -> float:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    return 0.0


def repo_dir(repo_id: str) -> Path:
    return MODELS_DIR / ("models--" + repo_id.replace("/", "--"))


def is_downloaded(repo_id: str) -> bool:
    return any(repo_dir(repo_id).glob("snapshots/*/*"))


def _weights_complete(repo_id: str) -> bool:
    for snapshot in repo_dir(repo_id).glob("snapshots/*"):
        try:
            index = json.loads((snapshot / "model.safetensors.index.json").read_text())
        except (OSError, ValueError):
            continue
        if n.weights_complete(index, {f.name for f in snapshot.iterdir() if f.exists()}):
            return True
    return False


def llama_ready() -> bool:
    return _weights_complete(LLAMA) and all(is_downloaded(r) for r in ENCODER_REPOS)


def provisional_ready() -> bool:
    return _weights_complete(PROVISIONAL) and all(is_downloaded(r) for r in ENCODER_REPOS)


def encoder_mode() -> str | None:
    return n.encoder_mode(llama_ready(), provisional_ready())


class Unavailable(Exception):
    def __init__(self, code: str, message: str, status: int = 503):
        self.code, self.message, self.status = code, message, status


def provisional_base() -> str:
    """LLM2Vec reads the base model's name from the adapter, so Llama 3.1 is named in a private copy of the adapter's folder."""
    source = next(repo_dir(ENCODER_REPOS[0]).glob("snapshots/*"))
    target = next(repo_dir(PROVISIONAL).glob("snapshots/*"))
    folder = Path("/tmp/provisional-base")
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for f in source.iterdir():
        if f.name != "adapter_config.json" and f.suffix != ".py":
            (folder / f.name).symlink_to(f.resolve())
    conf = json.loads((source / "adapter_config.json").read_text())
    conf["base_model_name_or_path"] = str(target)
    (folder / "adapter_config.json").write_text(json.dumps(conf))
    return str(folder)


def get_encoder():
    global encoder, encoder_loaded_mode
    mode = encoder_mode()
    with state_lock:
        if encoder is not None and encoder_loaded_mode == mode:
            return encoder
    if mode is None:
        raise Unavailable("text_encoder_missing", "No text encoder is installed on this Spark: open the Robot page of the hub to install it.")
    if encoder is not None:  # Llama 3 has just been installed: Llama 3.1 gives up its place first
        log.info(json.dumps({"event": "text_encoder.switching", "to": mode}))
        unload("text_encoder")
    free = available_gb()
    if not n.fits(free, ENCODER_GB, MARGIN_GB):
        raise Unavailable("not_enough_memory", f"The text encoder needs about {ENCODER_GB:.0f} GB and only {free:.1f} GB are free. "
                          "Free memory by unloading a model in the hub, or use a prompt generated before (those are cached).")
    from kimodo.model import LLM2VecEncoder
    loading["text_encoder"] = "loading"
    try:
        t0 = time.time()
        enc = LLM2VecEncoder(base_model_name_or_path=ENCODER_REPOS[0] if mode == "official" else provisional_base(),
                             peft_model_name_or_path=ENCODER_REPOS[1], dtype="bfloat16", llm_dim=4096, device="auto")
        log.info(json.dumps({"event": "text_encoder.loaded", "mode": mode, "s": round(time.time() - t0, 1)}))
        with state_lock:
            encoder, encoder_loaded_mode = enc, mode
        return enc
    finally:
        loading.pop("text_encoder", None)


class CachedEncoder:
    """Same interface as Kimodo's encoder. Embeddings live on disk, so Llama 3 is only needed for prompts never seen."""

    def __init__(self):
        self.device = "cuda"

    def __call__(self, texts):
        import torch
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        mode = encoder_mode()
        if mode is None:
            raise Unavailable("text_encoder_missing", "No text encoder is installed on this Spark: open the Robot page of the hub to install it.")
        folder = CACHE_DIR / mode  # vectors of different encoders must never mix
        out = []
        for text in items:
            path = folder / f"{n.cache_key(text)}.npy"
            if path.exists():
                out.append(np.load(path))
                continue
            log.info(json.dumps({"event": "text_encoder.needed", "chars": len(text), "text": text[:80]}))
            value, _ = get_encoder()(text)
            array = value.detach().float().cpu().numpy()
            folder.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npy")
            np.save(tmp, array)
            tmp.replace(path)
            out.append(array)
        lengths = [a.shape[0] for a in out]
        padded = np.zeros((len(out), max(lengths), out[0].shape[-1]), dtype=np.float32)
        for i, a in enumerate(out):
            padded[i, : a.shape[0]] = a
        return torch.from_numpy(padded).to(self.device), lengths

    def to(self, device):
        self.device = str(device)
        return self

    def eval(self):
        return self

    def get_device(self):
        return self.device


def get_model(name: str) -> dict:
    from kimodo import load_model
    from kimodo.model.registry import resolve_model_name
    from kimodo.model.loading import AVAILABLE_MODELS, MODEL_NAMES
    with gpu:
        for key, m in models.items():
            if name in (key, m["display"], m["repo"]):
                m["used"] = time.time()
                return m
        try:
            key = name if name in AVAILABLE_MODELS else resolve_model_name(name, "Kimodo")
        except ValueError as e:
            raise Unavailable("unknown_model", str(e), 404) from None
        repo = MODEL_NAMES[key]
        if not is_downloaded(repo):
            raise Unavailable("model_not_installed", f"{repo.split('/')[-1]} is not installed: install it from the Robot page of the hub.", 404)
        free = available_gb()
        if not n.fits(free, MOTION_MODEL_GB, MARGIN_GB):
            raise Unavailable("not_enough_memory", f"Loading {name} needs about {MOTION_MODEL_GB:.0f} GB and only {free:.1f} GB are free.")
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        loading[key] = "loading"
        try:
            t0 = time.time()
            model = load_model(key, device=device, default_family="Kimodo", text_encoder=CachedEncoder().to(device))
        finally:
            loading.pop(key, None)
        entry = {"key": key, "display": repo.split("/")[-1], "repo": repo, "model": model, "device": device, "used": time.time(),
                 "skeleton": model.skeleton.name, "fps": float(model.fps)}
        models[key] = entry
        log.info(json.dumps({"event": "model.loaded", "model": entry["display"], "s": round(time.time() - t0, 1)}))
        return entry


def unload(target: str) -> bool:
    global encoder, encoder_loaded_mode
    import gc
    import torch
    with gpu:
        hit = False
        if target in ("text_encoder", "all") and encoder is not None:
            encoder, encoder_loaded_mode, hit = None, None, True
        for key in list(models):
            if target in ("all", key, models[key]["display"]):
                del models[key]
                hit = True
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return hit


def memory_guard() -> None:
    """The hub's main model must not pay for this: under the margin the text encoder leaves first (prompts already seen keep working)."""
    while True:
        time.sleep(20)
        try:
            if encoder is not None and available_gb() < MARGIN_GB and gpu.acquire(blocking=False):
                try:
                    log.info(json.dumps({"event": "text_encoder.released_low_memory", "available_gb": round(available_gb(), 1)}))
                    unload("text_encoder")
                finally:
                    gpu.release()
        except Exception as e:
            log.info(json.dumps({"event": "memory_guard.error", "error": str(e)}))


def autoload() -> None:
    """What fits stays ready: the first prompt of the day should not wait a minute and a half for the encoder."""
    for name in AUTOLOAD:
        try:
            get_model(name)
        except Exception as e:
            log.info(json.dumps({"event": "autoload.failed", "model": name, "error": str(getattr(e, "message", e))}))
    try:
        if encoder_mode():
            with gpu:
                get_encoder()
    except Exception as e:
        log.info(json.dumps({"event": "autoload.failed", "model": "text_encoder", "error": str(getattr(e, "message", e))}))


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=memory_guard, daemon=True).start()
    threading.Thread(target=autoload, daemon=True).start()


# ------------------------------------------------------------------ what the suite sees


@app.middleware("http")
async def record(request: HttpRequest, call_next):
    t0 = time.time()
    body: Any = None
    if request.method in ("POST", "PUT", "PATCH") and not request.url.path.startswith("/_admin"):
        raw = await request.body()
        try:
            body = n.summarize(json.loads(raw)) if raw else None
        except ValueError:
            body = f"[{len(raw)} bytes, {request.headers.get('content-type', '?')}]"
    response = await call_next(request)
    if not request.url.path.startswith("/_admin") and request.method != "OPTIONS":
        history.appendleft({"at": t0, "method": request.method, "path": request.url.path, "status": response.status_code,
                            "ms": round((time.time() - t0) * 1000), "client": request.client.host if request.client else None,
                            "origin": request.headers.get("origin"), "body": body})
    return response


def error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"status": "error", "ok": False, "success": False, "code": code, "error": message, "detail": message}, status_code=status)


def _pose_constraints(req: n.Request, entry: dict) -> list:
    """Kimodo constraints from pose targets: each one is {time or time_s or frame, qpos} with qpos = root xyz, wxyz, 29 angles."""
    from kimodo.exports.mujoco import MujocoQposConverter
    from kimodo.constraints import load_constraints_lst
    constraints = list(req.constraints)
    if req.poses:
        if "g1" not in entry["key"]:
            raise n.BadRequest("pose targets are only supported on G1 models")
        converter = MujocoQposConverter(entry["model"].skeleton)
        total = sum(req.frames)
        indices, rows = [], []
        for pose in req.poses:
            row = pose.get("qpos") or pose.get("pose") or pose.get("values")
            if not isinstance(row, list) or len(row) not in (30, 36):
                raise n.BadRequest("each pose target needs qpos: root xyz, quaternion wxyz and 29 joint angles (or 23)")
            when = pose.get("frame")
            when = when if when is not None else round(float(pose.get("time", pose.get("time_s", 0))) * n.FPS)
            indices.append(min(total - 1, max(0, int(when))))  # quantised to the nearest frame
            rows.append(row)
        import torch
        full = n.expand_23_to_29(np.asarray(rows, dtype=np.float32))
        rots, roots = [], []
        for row in full:  # the conversion smooths over time, so a single pose goes in as a short still clip
            still = torch.as_tensor(np.repeat(row[None], 16, axis=0), device=entry["device"])
            motion = converter.qpos_to_motion_dict(still, n.FPS)
            rots.append(motion["local_rot_mats"][0])
            roots.append(motion["root_positions"][0])
        constraints.append(_fullbody_from_matrices({"local_rot_mats": torch.stack(rots), "root_positions": torch.stack(roots)}, indices))
    if not constraints:
        return []
    return load_constraints_lst(constraints, entry["model"].skeleton, device=entry["device"])


def _fullbody_from_matrices(motion: dict, indices: list[int]) -> dict:
    from scipy.spatial.transform import Rotation
    plain = lambda v: v.detach().cpu().numpy() if hasattr(v, "detach") else np.asarray(v)
    rots = plain(motion["local_rot_mats"]).reshape(len(indices), -1, 3, 3)
    axis = Rotation.from_matrix(rots.reshape(-1, 3, 3)).as_rotvec().reshape(len(indices), -1, 3)
    roots = plain(motion["root_positions"]).reshape(len(indices), 3)
    return {"type": "fullbody", "frame_indices": indices, "local_joints_rot": axis.tolist(), "root_positions": roots.tolist()}


def generate(req: n.Request) -> dict:
    global busy, generated, last_error
    from kimodo.exports.mujoco import MujocoQposConverter
    from kimodo.tools import seed_everything
    with gpu:
        entry = get_model(req.model)
        busy = {"prompt": req.prompts[0][:120], "since": time.time(), "model": entry["display"]}
        try:
            if req.seed is not None:
                seed_everything(req.seed)
            kwargs: dict[str, Any] = {}
            if req.cfg_weight is not None:
                kwargs["cfg_weight"] = req.cfg_weight if isinstance(req.cfg_weight, (list, tuple)) else [req.cfg_weight, req.cfg_weight]
            if req.cfg_type:
                kwargs["cfg_type"] = req.cfg_type
            t0 = time.time()
            multi = len(req.prompts) > 1
            output = entry["model"](req.prompts, req.frames,
                                    num_denoising_steps=req.steps, num_samples=req.samples, multi_prompt=True,
                                    num_transition_frames=req.transitions, constraint_lst=_pose_constraints(req, entry),
                                    post_processing=False, return_numpy=True, **kwargs)
            elapsed = time.time() - t0
            result: dict[str, Any] = {"status": "ok", "ok": True, "success": True, "model": entry["display"], "fps": entry["fps"],
                                      "elapsed_s": round(elapsed, 2), "prompt": req.prompts if multi else req.prompts[0],
                                      "seed": req.seed, "skeleton": entry["skeleton"], "num_frames": sum(req.frames),
                                      "duration": sum(req.frames) / entry["fps"], "text_encoder": encoder_mode()}
            if encoder_mode() == "provisional":
                result["warnings"] = [PROVISIONAL_NOTE]
            if "g1" in entry["key"]:
                qpos = np.asarray(MujocoQposConverter(entry["model"].skeleton).dict_to_qpos(output, entry["device"]))
                if qpos.ndim == 2:
                    qpos = qpos[None]
                if not req.root_motion:
                    qpos = n.freeze_root(qpos)
                qpos = n.select_dof(qpos, req.dof)
                first = qpos[0]
                result.update(dof=req.dof, joint_names=n.joint_names(req.dof), columns=["x", "y", "z", "qw", "qx", "qy", "qz", *n.joint_names(req.dof)],
                              qpos=first.round(6).tolist(), csv=n.to_csv(first), root_positions=first[:, :3].round(6).tolist(),
                              root_quaternions=first[:, 3:7].round(6).tolist(), joint_angles=first[:, 7:].round(6).tolist())
                if req.samples > 1:
                    result["samples"] = [q.round(6).tolist() for q in qpos]
            else:  # human skeletons: no robot CSV, only what the model outputs
                result.update(local_rot_mats=np.asarray(output["local_rot_mats"]).round(5).tolist(), root_positions=np.asarray(output["root_positions"]).round(5).tolist())
            generated += 1
            last_error = None
            return result
        finally:
            busy = None


@app.post("/generate")
async def generate_route(request: HttpRequest):
    global last_error
    try:
        body = await request.json()
    except ValueError:
        return error("bad_request", "the body must be JSON", 400)
    if not isinstance(body, dict):
        return error("bad_request", "the body must be a JSON object", 400)
    try:
        req = n.parse_generate(body)
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(generate, req)
    except n.BadRequest as e:
        return error("bad_request", str(e), 400)
    except Unavailable as e:
        last_error = None if e.code == "text_encoder_missing" else e.message  # the hub already explains that one
        return error(e.code, e.message, e.status)
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        log.exception("generate failed")
        return error("generation_failed", last_error, 500)
    if str(body.get("format", "")).lower() == "csv" or "text/csv" in request.headers.get("accept", ""):
        return PlainTextResponse(result.get("csv", ""), media_type="text/csv")
    return result


async def physics_route(request: HttpRequest):
    try:
        body = await request.json()
        rows, fps = n.parse_qpos(body)
        qpos = n.expand_23_to_29(np.asarray(rows, dtype=np.float64))
        from . import physics
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(physics.check, qpos, fps, bool(body.get("rollout", False)))
        return {"status": "ok", "success": True, **result}
    except n.BadRequest as e:
        return error("bad_request", str(e), 400)
    except ValueError:
        return error("bad_request", "the body must be JSON", 400)
    except Exception as e:
        log.exception("physics failed")
        return error("physics_failed", f"{type(e).__name__}: {e}", 500)


for _path in ("/physics", "/physics/check", "/check_physics", "/simulate", "/mujoco/check", "/mujoco/simulate", "/validate"):
    app.add_api_route(_path, physics_route, methods=["POST"])


def public_status() -> dict:
    return {"status": "ok", "ok": True, "service": "spark-robot", "models": [m["display"] for m in models.values()], "default_model": n.DEFAULT_MODEL,
            "text_encoder": encoder_mode() or "missing", "text_encoder_loaded": encoder is not None, "busy": busy is not None,
            "physics": True, "mujoco": True, "dof": [23, 29], "fps": n.FPS, "poses": True}


@app.get("/")
@app.get("/health")
@app.get("/healthz")
@app.get("/status")
@app.get("/capabilities")
def health():
    return public_status()


@app.get("/models")
def models_route():
    from kimodo.model.loading import MODEL_NAMES
    return {"models": [{"name": r.split("/")[-1], "id": r.split("/")[-1], "loaded": any(m["repo"] == r for m in models.values())}
                       for r in sorted(set(MODEL_NAMES.values())) if is_downloaded(r) and "TMR" not in r]}


# ------------------------------------------------------------------ hub only


def admin_ok(request: HttpRequest) -> bool:
    return bool(ADMIN_TOKEN) and hmac.compare_digest(request.headers.get("x-admin-token", ""), ADMIN_TOKEN)


@app.middleware("http")
async def guard_admin(request: HttpRequest, call_next):
    if request.url.path.startswith("/_admin") and not admin_ok(request):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/_admin/status")
def admin_status():
    from kimodo.model.loading import MODEL_NAMES
    installed = [r for r in sorted(set(MODEL_NAMES.values())) if is_downloaded(r)]
    for repo, job in list(downloads.items()):
        if job.get("proc") is not None and job["proc"].poll() is not None:
            job.update(state="done" if job["proc"].returncode == 0 else "failed", finished=time.time(), proc=None)
            job["error"] = None if job["state"] == "done" else _tail(job)
    return {**public_status(), "available_gb": round(available_gb(), 1), "margin_gb": MARGIN_GB, "encoder_gb": ENCODER_GB,
            "encoder_loaded": encoder is not None, "llama_installed": llama_ready(), "provisional_installed": provisional_ready(), "encoder_mode": encoder_mode(), "encoder_loaded_mode": encoder_loaded_mode, "loaded": [{k: m[k] for k in ("key", "display", "repo", "device", "skeleton", "fps", "used")} for m in models.values()],
            "loading": list(loading), "installed": installed, "busy_info": busy, "last_error": last_error, "generated": generated,
            "cached_prompts": len(list((CACHE_DIR / (encoder_mode() or "none")).glob("*.npy"))),
            "downloads": {r: {k: v for k, v in j.items() if k != "proc"} | {"bytes": _size(r)} for r, j in downloads.items()}}


def _size(repo: str) -> int:
    root = repo_dir(repo)
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0


def _tail(job: dict) -> str | None:
    try:
        return Path(job["log"]).read_text()[-400:]
    except OSError:
        return None


@app.get("/_admin/requests")
def admin_requests():
    return {"requests": list(history)}


@app.post("/_admin/load")
async def admin_load(request: HttpRequest):
    body = await request.json()
    from starlette.concurrency import run_in_threadpool
    target = str(body.get("model") or n.DEFAULT_MODEL)
    try:
        if target == "text_encoder":
            await run_in_threadpool(get_encoder)
        else:
            await run_in_threadpool(get_model, target)
    except Unavailable as e:
        return error(e.code, e.message, e.status)
    return {"ok": True}


@app.post("/_admin/unload")
async def admin_unload(request: HttpRequest):
    body = await request.json()
    from starlette.concurrency import run_in_threadpool
    return {"ok": await run_in_threadpool(unload, str(body.get("model") or "all"))}


DOWNLOAD_SCRIPT = """
import sys
from huggingface_hub import snapshot_download
print(snapshot_download(sys.argv[1], cache_dir=sys.argv[2], ignore_patterns=["original/*", "*.pth"]))
"""


@app.post("/_admin/download")
async def admin_download(request: HttpRequest):
    body = await request.json()
    repo = str(body.get("repo_id") or "")
    current = downloads.get(repo)
    if current and current.get("state") == "running":
        return {"ok": True, "state": "running"}
    env = {**os.environ, "HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}
    env.pop("HF_TOKEN", None)
    if body.get("token"):
        env["HF_TOKEN"] = str(body["token"])
    log_path = f"/tmp/download-{repo.replace('/', '_')}.log"
    with open(log_path, "wb") as out:
        proc = subprocess.Popen([sys.executable, "-c", DOWNLOAD_SCRIPT, repo, str(MODELS_DIR)], env=env, stdout=out, stderr=subprocess.STDOUT)
    downloads[repo] = {"state": "running", "started": time.time(), "proc": proc, "log": log_path}
    return {"ok": True, "state": "running"}


@app.get("/{path:path}", include_in_schema=False)
@app.post("/{path:path}", include_in_schema=False)
def unknown(path: str):
    return error("not_found", f"this server has no /{path}", 404)
