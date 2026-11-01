"""Spark AI Hub engine manager.

The ONLY component with access to the Docker socket. It exposes a small, typed,
allow-listed API on the internal control network:

* start/stop/restart of known engines (vllm, comfyui, mlworker) with fixed images,
  fixed mounts and validated parameters — never arbitrary images, mounts or commands;
* restart of allow-listed compose services (ollama);
* read-only container status, stats and logs for hub containers.

Every mutating call requires the shared bearer token and is logged.
"""
from __future__ import annotations

import concurrent.futures
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Literal

import docker
from docker.errors import APIError, NotFound
from docker.types import DeviceRequest
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
logger = logging.getLogger("engine-manager")


def jlog(**kw):
    kw.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    kw.setdefault("service", "engine-manager")
    logger.info(json.dumps(kw, default=str))


TOKEN = Path(os.environ.get("SAH_ORCHESTRATOR_TOKEN_FILE", "/run/secrets/orchestrator_token")).read_text().strip()
HOST_DATA = os.environ.get("SAH_HOST_DATA_ROOT", "/srv/spark-ai-hub")
PROJECT = os.environ.get("SAH_COMPOSE_PROJECT", "spark-ai-hub")
ENGINE_NETWORK = os.environ.get("SAH_ENGINE_NETWORK", f"{PROJECT}_engines")
IMAGES = {
    "vllm": os.environ.get("SAH_VLLM_IMAGE", "nvcr.io/nvidia/vllm:26.09-py3"),
    "comfyui": os.environ.get("SAH_COMFYUI_IMAGE", "spark-ai-hub/comfyui:0.42.5"),
    "mlworker": os.environ.get("SAH_MLWORKER_IMAGE", "spark-ai-hub/mlworker:0.1.0"),
    "sandbox": os.environ.get("SAH_SANDBOX_IMAGE", "spark-ai-hub/sandbox:0.1.0"),
}
CONTAINER_NAMES = {"vllm": "sah-vllm", "comfyui": "sah-comfyui", "mlworker": "sah-mlworker"}
VLLM_PREFIX = "sah-vllm-"  # one container per served model: sah-vllm-<slug(alias)>, reachable by name on the engines network


def vllm_container_name(served: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", served.lower()).strip("-")[:48]
    return VLLM_PREFIX + (slug or "model")


def _vllm_containers() -> list:
    return client.containers.list(all=True, filters={"label": ["sah.managed=true", "sah.engine=vllm"]})
RESTARTABLE_SERVICES = {"ollama": "sah-ollama"}
SECRET_RE = re.compile(r"(hf_[A-Za-z0-9]{6})[A-Za-z0-9]+|(sah-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+")

client = docker.from_env()
app = FastAPI(title="Spark AI Hub engine manager", docs_url=None, redoc_url=None)


def auth(request: Request) -> None:
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer ") or not hmac.compare_digest(h[7:].encode(), TOKEN.encode()):
        raise HTTPException(401, "unauthorized")


# ------------------------------------------------------------------ parameter models

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
MODEL_PATH = re.compile(r"^/models/hf/models--[A-Za-z0-9._-]+--[A-Za-z0-9._-]+/snapshots/[0-9a-f]{40}$")


class VllmStart(BaseModel):
    model_path: str
    served_model_name: str
    max_model_len: int = Field(ge=256, le=1_048_576)
    gpu_memory_utilization: float = Field(ge=0.05, le=0.9)
    max_num_seqs: int = Field(default=16, ge=1, le=512)
    dtype: Literal["auto", "bfloat16", "float16"] = "auto"
    quantization: Literal["fp8", "awq", "gptq", "modelopt", "modelopt_fp4", "mxfp4", "compressed-tensors", "bitsandbytes"] | None = None
    kv_cache_dtype: Literal["auto", "fp8"] = "auto"
    trust_remote_code: bool = False
    enable_tool_calling: bool = False
    tool_call_parser: Literal["hermes", "llama3_json", "mistral", "openai", "qwen3_coder", "pythonic"] | None = None
    reasoning_parser: Literal["qwen3", "deepseek_r1", "openai_gptoss"] | None = None
    enforce_eager: bool = False
    speculative_tokens: int = Field(default=0, ge=0, le=4)  # MTP speculative decoding (models with MTP heads)
    task: Literal["generate", "embed"] = "generate"

    @field_validator("model_path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not MODEL_PATH.match(v) or ".." in v:
            raise ValueError("model_path must be an HF snapshot path under /models/hf")
        return v

    @field_validator("served_model_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not SAFE_NAME.match(v):
            raise ValueError("invalid served_model_name")
        return v


# ------------------------------------------------------------------ container specs


def _common(engine: str, name: str | None = None) -> dict:
    return dict(
        name=name or CONTAINER_NAMES[engine],
        detach=True,
        network=ENGINE_NETWORK,
        labels={"sah.managed": "true", "sah.engine": engine, "com.docker.compose.project": PROJECT},
        security_opt=["no-new-privileges:true"],
        restart_policy={"Name": "no"},
        device_requests=[DeviceRequest(count=-1, capabilities=[["gpu"]])],
        log_config={"type": "json-file", "config": {"max-size": "20m", "max-file": "3"}},
    )


def vllm_spec(p: VllmStart) -> dict:
    cmd = [
        "vllm", "serve", p.model_path,
        "--host", "0.0.0.0", "--port", "8000",
        "--served-model-name", p.served_model_name,
        "--max-model-len", str(p.max_model_len),
        "--gpu-memory-utilization", f"{p.gpu_memory_utilization:.3f}",
        "--max-num-seqs", str(p.max_num_seqs),
        "--dtype", p.dtype,
        "--kv-cache-dtype", p.kv_cache_dtype,
    ]
    if p.quantization:
        cmd += ["--quantization", p.quantization]
    if p.trust_remote_code:
        cmd += ["--trust-remote-code"]
    if p.enable_tool_calling and p.tool_call_parser:
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", p.tool_call_parser]
    if p.reasoning_parser:
        cmd += ["--reasoning-parser", p.reasoning_parser]
    if p.enforce_eager:
        cmd += ["--enforce-eager"]
    if p.speculative_tokens and p.task == "generate":
        cmd += ["--speculative-config", json.dumps({"method": "mtp", "num_speculative_tokens": p.speculative_tokens})]
    if p.task == "embed":
        cmd += ["--runner", "pooling"]
    spec = _common("vllm", vllm_container_name(p.served_model_name))
    spec["labels"]["sah.vllm.model"] = p.served_model_name
    spec.update(
        image=IMAGES["vllm"],
        command=cmd,
        environment={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1",
                     "DO_NOT_TRACK": "1"},
        volumes={
            f"{HOST_DATA}/models/hf": {"bind": "/models/hf", "mode": "ro"},
            f"{HOST_DATA}/cache/vllm": {"bind": "/root/.cache", "mode": "rw"},
        },
        shm_size="16g",
        ulimits=[docker.types.Ulimit(name="memlock", soft=-1, hard=-1)],
    )
    return spec


def comfyui_spec() -> dict:
    spec = _common("comfyui")
    spec.update(
        image=IMAGES["comfyui"],
        user="1000:1000",
        cap_drop=["ALL"],
        environment={"HF_HUB_OFFLINE": "1"},
        volumes={
            f"{HOST_DATA}/models/comfyui": {"bind": "/opt/ComfyUI/models", "mode": "ro"},
            f"{HOST_DATA}/outputs/comfyui": {"bind": "/opt/ComfyUI/output", "mode": "rw"},
            f"{HOST_DATA}/cache/comfyui/input": {"bind": "/opt/ComfyUI/input", "mode": "rw"},
            f"{HOST_DATA}/cache/comfyui/user": {"bind": "/opt/ComfyUI/user", "mode": "rw"},
            f"{HOST_DATA}/cache/comfyui/temp": {"bind": "/opt/ComfyUI/temp", "mode": "rw"},
        },
        shm_size="8g",
    )
    return spec


def mlworker_spec() -> dict:
    spec = _common("mlworker")
    spec.update(
        image=IMAGES["mlworker"],
        user="1000:1000",
        cap_drop=["ALL"],
        environment={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": "/models/hf-home",
                     "HF_HUB_CACHE": "/models/hf"},
        volumes={
            f"{HOST_DATA}/models/hf": {"bind": "/models/hf", "mode": "ro"},
            f"{HOST_DATA}/models/custom": {"bind": "/models/custom", "mode": "ro"},
            f"{HOST_DATA}/datasets": {"bind": "/data/datasets", "mode": "ro"},
            f"{HOST_DATA}/outputs/mlworker": {"bind": "/data/outputs", "mode": "rw"},
            f"{HOST_DATA}/cache/mlworker": {"bind": "/cache", "mode": "rw"},
        },
        shm_size="4g",
    )
    return spec


# ------------------------------------------------------------------ helpers


def _get(name: str):
    try:
        return client.containers.get(name)
    except NotFound:
        return None


def _describe(c) -> dict:
    attrs = c.attrs
    state = attrs.get("State", {})
    health = state.get("Health", {}).get("Status") if state.get("Health") else None
    return {
        "name": c.name,
        "image": attrs.get("Config", {}).get("Image"),
        "state": state.get("Status"),
        "health": health,
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled"),
        "restart_count": attrs.get("RestartCount"),
        "engine": c.labels.get("sah.engine"),
        "service": c.labels.get("com.docker.compose.service"),
        "cmd": attrs.get("Config", {}).get("Cmd") if c.labels.get("sah.engine") else None,
    }


def _remove_if_exists(name: str, timeout: int = 30) -> None:
    c = _get(name)
    if c is None:
        return
    if c.labels.get("sah.managed") != "true":
        raise HTTPException(409, f"container {name} exists but is not managed by Spark AI Hub")
    try:
        if c.status == "running":
            c.stop(timeout=timeout)
        c.remove(force=True)
    except NotFound:
        pass


def _start(engine: str, spec: dict) -> dict:
    image = spec["image"]
    try:
        client.images.get(image)
    except NotFound:
        raise HTTPException(424, f"image {image} not present; build/pull it first")
    _remove_if_exists(spec["name"])
    try:
        c = client.containers.run(**spec)
    except APIError as e:
        jlog(event="engine.start_failed", engine=engine, error=str(e))
        raise HTTPException(500, f"docker error: {e.explanation}")
    jlog(event="engine.start", engine=engine, image=image, cmd=spec.get("command"))
    return _describe(c)


# ------------------------------------------------------------------ code sandbox

RUN_ID = re.compile(r"^[0-9a-f]{32}$")
WORKSPACE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SANDBOX_SLOTS = threading.BoundedSemaphore(int(os.environ.get("SAH_SANDBOX_CONCURRENCY", "4")))


class SandboxRun(BaseModel):
    run_id: str
    language: Literal["python", "bash", "node"] = "python"
    timeout_s: int = Field(default=120, ge=5, le=900)
    memory_mb: int = Field(default=2048, ge=256, le=16384)
    cpus: float = Field(default=4, ge=0.5, le=16)
    workspace: str | None = None  # "<user uuid>/<workspace uuid>": persistent folder mounted as /work
    packages: bool = False  # pip/npm through the package proxy socket (still no network interface)

    @field_validator("workspace")
    @classmethod
    def _ws(cls, v: str | None) -> str | None:
        if v is not None and not WORKSPACE_ID.match(v):
            raise ValueError("workspace must be <uuid>/<uuid>")
        return v

    @field_validator("run_id")
    @classmethod
    def _rid(cls, v: str) -> str:
        if not RUN_ID.match(v):
            raise ValueError("run_id must be 32 hex characters")
        return v


ENTRY = {"python": ["python", "/work/.main.py"], "bash": ["bash", "/work/.main.sh"], "node": ["node", "/work/.main.js"]}


def sandbox_spec(p: SandboxRun) -> dict:
    """No network, read-only root, non-root, no capabilities, CPU/memory/pids limits; only /work is writable.
    With a workspace, /work is the user's persistent workspace and the program comes read-only from /job."""
    run_dir = f"{HOST_DATA}/sandbox/{p.run_id}"
    env = {"HOME": "/tmp", "MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1", "OPENBLAS_NUM_THREADS": "4"}
    if p.workspace:
        volumes = {f"{HOST_DATA}/workspaces/{p.workspace}": {"bind": "/work", "mode": "rw"},
                   run_dir: {"bind": "/job", "mode": "ro"}}
        command = [ENTRY[p.language][0], ENTRY[p.language][1].replace("/work/", "/job/")]
        # packages installed by the user persist in the workspace: pip --user → /work/.local, npm → /work/node_modules
        env.update(PYTHONUSERBASE="/work/.local", PIP_DISABLE_PIP_VERSION_CHECK="1",
                   PATH="/work/.local/bin:/work/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
                   NPM_CONFIG_CACHE="/tmp/.npm", NPM_CONFIG_UPDATE_NOTIFIER="false")
        if p.packages:
            volumes[f"{HOST_DATA}/run/pkgproxy"] = {"bind": "/proxy", "mode": "rw"}
            proxy = "http://127.0.0.1:8888"
            env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, http_proxy=proxy, https_proxy=proxy,
                       NPM_CONFIG_PROXY=proxy, NPM_CONFIG_HTTPS_PROXY=proxy)
            # a loopback-only forwarder inside the container: TCP 127.0.0.1:8888 → the proxy's Unix socket
            command = ["bash", "-c", "socat TCP-LISTEN:8888,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/proxy/proxy.sock "
                       "2>/dev/null & sleep 0.2; exec \"$@\"", "run"] + command
    else:
        volumes = {run_dir: {"bind": "/work", "mode": "rw"}}
        command = ENTRY[p.language]
    return dict(
        image=IMAGES["sandbox"], command=command, name=f"sah-sandbox-{p.run_id[:12]}", detach=True,
        network_mode="none", user="1000:1000", working_dir="/work", read_only=True,
        tmpfs={"/tmp": "size=512m,mode=1777"},
        volumes=volumes,
        environment=env,
        mem_limit=f"{p.memory_mb}m", memswap_limit=f"{p.memory_mb}m", pids_limit=512,
        nano_cpus=int(p.cpus * 1e9), cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
        labels={"sah.managed": "true", "sah.engine": "sandbox", "com.docker.compose.project": PROJECT},
        log_config={"type": "json-file", "config": {"max-size": "5m", "max-file": "1"}},
    )


@app.post("/sandbox/run", dependencies=[Depends(auth)])
def sandbox_run(p: SandboxRun):
    """Run one program to completion (or until the time limit) and return its exit code and output."""
    if not SANDBOX_SLOTS.acquire(timeout=60):
        raise HTTPException(429, "all sandbox slots are busy")
    t0 = time.time()
    c = None
    try:
        try:
            client.images.get(IMAGES["sandbox"])
        except NotFound:
            raise HTTPException(424, f"image {IMAGES['sandbox']} not present; build it first")
        c = client.containers.run(**sandbox_spec(p))
        timed_out = False
        try:
            res = c.wait(timeout=p.timeout_s)
            code = res.get("StatusCode", -1)
        except Exception:  # requests ReadTimeout: time limit reached
            timed_out, code = True, -1
            c.kill()
        c.reload()
        oom = bool(c.attrs.get("State", {}).get("OOMKilled"))
        out = c.logs(stdout=True, stderr=False).decode(errors="replace")
        err = c.logs(stdout=False, stderr=True).decode(errors="replace")
        jlog(event="sandbox.run", run=p.run_id, language=p.language, code=code, seconds=round(time.time() - t0, 2),
             timed_out=timed_out, oom=oom)
        return {"exit_code": code, "timed_out": timed_out, "oom_killed": oom, "seconds": round(time.time() - t0, 2),
                "stdout": out[-20000:], "stderr": err[-20000:]}
    finally:
        if c is not None:
            try:
                c.remove(force=True)
            except APIError:
                pass
        SANDBOX_SLOTS.release()


# ------------------------------------------------------------------ routes


@app.get("/health")
def health():
    client.ping()
    return {"ok": True}


@app.get("/containers", dependencies=[Depends(auth)])
def containers():
    cs = client.containers.list(all=True, filters={"label": f"com.docker.compose.project={PROJECT}"})
    return [_describe(c) for c in cs]


def _mem_stats(c) -> tuple[str, dict]:
    try:
        s = c.stats(stream=False)
        ms = s.get("memory_stats", {})
        usage = ms.get("usage", 0) - ms.get("stats", {}).get("inactive_file", 0)
        cpu = s.get("cpu_stats", {})
        pre = s.get("precpu_stats", {})
        cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or 1
        cpu_pct = (cpu_delta / sys_delta) * ncpu * 100 if sys_delta > 0 else None
        return c.name, {"memory_bytes": max(usage, 0), "memory_limit": ms.get("limit"), "cpu_percent": cpu_pct}
    except Exception as e:  # noqa: BLE001
        return c.name, {"error": str(e)}


@app.get("/containers/stats", dependencies=[Depends(auth)])
def container_stats():
    cs = client.containers.list(filters={"label": f"com.docker.compose.project={PROJECT}"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        return dict(ex.map(_mem_stats, cs))


@app.get("/engines", dependencies=[Depends(auth)])
def engines():
    out = {}
    for engine, name in CONTAINER_NAMES.items():
        img_present = True
        try:
            client.images.get(IMAGES[engine])
        except NotFound:
            img_present = False
        if engine == "vllm":
            inst = [{**_describe(c), "model": c.labels.get("sah.vllm.model")} for c in _vllm_containers()]
            running = [i for i in inst if i["state"] == "running"]
            out[engine] = {"image": IMAGES[engine], "image_present": img_present, "instances": inst,
                           "container": (running or inst or [None])[0]}
            continue
        c = _get(name)
        out[engine] = {"image": IMAGES[engine], "image_present": img_present,
                       "container": _describe(c) if c else None}
    return out


@app.post("/engines/vllm/start", dependencies=[Depends(auth)])
def start_vllm(p: VllmStart):
    return _start("vllm", vllm_spec(p))


@app.post("/engines/vllm/stop", dependencies=[Depends(auth)])
def stop_vllm(model: str | None = None):
    """Stop one model's vLLM container (or every vLLM container when no model is given)."""
    if model is not None and not SAFE_NAME.match(model):
        raise HTTPException(422, "invalid model name")
    for c in _vllm_containers():
        if model is None or c.labels.get("sah.vllm.model") == model:
            _remove_if_exists(c.name)
    jlog(event="engine.stop", engine="vllm", model=model)
    return {"ok": True}


@app.post("/engines/{engine}/start", dependencies=[Depends(auth)])
def start_engine(engine: Literal["comfyui", "mlworker"]):
    c = _get(CONTAINER_NAMES[engine])
    if c is not None and c.status == "running":
        return _describe(c)
    spec = comfyui_spec() if engine == "comfyui" else mlworker_spec()
    return _start(engine, spec)


@app.post("/engines/{engine}/stop", dependencies=[Depends(auth)])
def stop_engine(engine: Literal["vllm", "comfyui", "mlworker"]):
    if engine == "vllm":
        return stop_vllm(None)
    _remove_if_exists(CONTAINER_NAMES[engine])
    jlog(event="engine.stop", engine=engine)
    return {"ok": True}


@app.get("/engines/{engine}/logs", dependencies=[Depends(auth)])
def engine_logs(engine: Literal["vllm", "comfyui", "mlworker"], tail: int = 200, model: str | None = None):
    if engine == "vllm":
        cs = [c for c in _vllm_containers() if model is None or c.labels.get("sah.vllm.model") == model]
        c = cs[0] if cs else None
    else:
        c = _get(CONTAINER_NAMES[engine])
    if c is None:
        return {"logs": ""}
    text = c.logs(tail=max(1, min(tail, 2000))).decode(errors="replace")
    return {"logs": SECRET_RE.sub(lambda m: (m.group(1) or m.group(2)) + "***", text)}


@app.get("/services/{service}/logs", dependencies=[Depends(auth)])
def service_logs(service: str, tail: int = 200):
    cs = client.containers.list(all=True, filters={"label": [f"com.docker.compose.project={PROJECT}",
                                                            f"com.docker.compose.service={service}"]})
    if not cs:
        raise HTTPException(404, "unknown service")
    text = cs[0].logs(tail=max(1, min(tail, 2000))).decode(errors="replace")
    return {"logs": SECRET_RE.sub(lambda m: (m.group(1) or m.group(2)) + "***", text)}


@app.post("/services/{service}/restart", dependencies=[Depends(auth)])
def restart_service(service: str):
    name = RESTARTABLE_SERVICES.get(service)
    if not name:
        raise HTTPException(403, "service not in restart allow-list")
    c = _get(name)
    if c is None:
        raise HTTPException(404, "container not found")
    c.restart(timeout=30)
    jlog(event="service.restart", service=service)
    return _describe(c)


@app.get("/versions", dependencies=[Depends(auth)])
def versions():
    v = client.version()
    return {"docker": v.get("Version"), "api": v.get("ApiVersion"), "images": IMAGES}
