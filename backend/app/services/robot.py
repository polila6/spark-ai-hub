"""Robot suites: the motion server's address, its models and the library of models it can deploy.

The server itself (engines/robot) runs Kimodo and MuJoCo. The hub only starts it, reports on it and tells it what to load."""
from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from .. import config
from ..config import get_settings
from . import hf, orchestrator

PORT = 5000
BASE = f"http://sah-robot:{PORT}"
LLAMA = "meta-llama/Meta-Llama-3-8B-Instruct"
ENCODER_REPOS = (LLAMA, "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp", "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised")
PROVISIONAL = "RedHatAI/Llama-3.1-8B-Instruct"  # stands in for Llama 3 while Meta has not approved the account
INSTALLABLE_EXTRA = {*ENCODER_REPOS, PROVISIONAL}

# What the deployed Kimodo release can load. "compatible" means the output is a G1 motion in MuJoCo order (root, quaternion,
# 29 joints), the only thing a G1 suite can play; the human skeletons are listed so nobody has to wonder why they are absent.
CATALOG: list[dict[str, Any]] = [
    {"repo": "nvidia/Kimodo-G1-RP-v1", "skeleton": "Unitree G1", "data": "Bones Rigplay 1 (~560 h)", "licence": "NVIDIA Open Model", "compatible": True, "recommended": True},
    {"repo": "nvidia/Kimodo-G1-SEED-v1", "skeleton": "Unitree G1", "data": "BONES-SEED", "licence": "NVIDIA Open Model", "compatible": True},
    {"repo": "nvidia/Kimodo-SOMA-RP-v1.2", "skeleton": "SOMA", "data": "Bones Rigplay 1", "licence": "NVIDIA Open Model", "compatible": False},
    {"repo": "nvidia/Kimodo-SOMA-RP-ArdyRep-v1.2", "skeleton": "SOMA", "data": "Bones Rigplay 1", "licence": "NVIDIA Open Model", "compatible": False},
    {"repo": "nvidia/Kimodo-SOMA-RP-v1.1", "skeleton": "SOMA", "data": "Bones Rigplay 1", "licence": "NVIDIA Open Model", "compatible": False},
    {"repo": "nvidia/Kimodo-SOMA-SEED-v1.1", "skeleton": "SOMA", "data": "BONES-SEED", "licence": "NVIDIA Open Model", "compatible": False},
    {"repo": "nvidia/Kimodo-SOMA-RP-v1", "skeleton": "SOMA", "data": "Bones Rigplay 1", "licence": "NVIDIA Open Model", "compatible": False},
    {"repo": "nvidia/Kimodo-SOMA-SEED-v1", "skeleton": "SOMA", "data": "BONES-SEED", "licence": "NVIDIA Open Model", "compatible": False},
    {"repo": "nvidia/Kimodo-SMPLX-RP-v1", "skeleton": "SMPL-X", "data": "Bones Rigplay 1", "licence": "NVIDIA R&D Model", "compatible": False},
]
KNOWN = {m["repo"] for m in CATALOG}


class RobotError(Exception):
    def __init__(self, message: str, status: int = 503, code: str = "robot_unavailable"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def admin_token() -> str:
    return hashlib.sha256(f"robot-admin:{get_settings().secret('orchestrator_token') or ''}".encode()).hexdigest()


def urls(tailnet: dict | None = None) -> dict:
    """Where a suite connects. The IP is the one Tailscale gives this machine today, read live: it follows any change."""
    t = config.tailnet_state() if tailnet is None else tailnet
    ip, dns = t.get("ipv4"), t.get("dns")
    return {"ip": ip, "dns": dns, "port": PORT,
            "base": f"http://{ip}:{PORT}" if ip else None,
            "generate": f"http://{ip}:{PORT}/generate" if ip else None,
            "generate_dns": f"http://{dns}:{PORT}/generate" if dns else None}


def call(method: str, path: str, *, timeout: float = 15, **kw) -> Any:
    try:
        with httpx.Client(base_url=BASE, timeout=timeout, headers={"X-Admin-Token": admin_token()}) as c:
            r = c.request(method, path, **kw)
    except httpx.HTTPError as e:
        raise RobotError("The motion server is not running") from e
    try:
        data = r.json()
    except ValueError:
        raise RobotError(f"Unexpected answer from the motion server ({r.status_code})") from None
    if r.status_code >= 400:
        raise RobotError(data.get("error") or data.get("detail") or "Motion server error", r.status_code, data.get("code") or "robot_error")
    return data


def container() -> dict:
    try:
        info = orchestrator.engines().get("robot") or {}
    except orchestrator.OrchestratorError as e:
        return {"image_present": None, "state": "unknown", "error": str(e)}
    c = info.get("container") or {}
    return {"image_present": info.get("image_present"), "state": c.get("state") or "absent"}


_access: dict[str, tuple[float, dict]] = {}
ACCESS_TTL_S = 300  # the page polls: asking Hugging Face once every few minutes is enough


def llama_access(token: str | None) -> dict:
    """Whether the hub's Hugging Face token can download Llama 3 (Meta approves each account)."""
    if not token:
        return {"state": "no_token"}
    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _access.get(key)
    if cached and time.time() - cached[0] < ACCESS_TTL_S:
        return cached[1]
    try:
        hf.api(token).auth_check(LLAMA)
        result = {"state": "granted"}
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        denied = status in (401, 403) or type(e).__name__ in ("GatedRepoError", "RepositoryNotFoundError")
        result = {"state": "denied" if denied else "unknown"}
    _access[key] = (time.time(), result)
    return result


def library(server: dict | None) -> list[dict]:
    installed = set((server or {}).get("installed") or [])
    loaded = {m["repo"] for m in (server or {}).get("loaded") or []}
    downloads = (server or {}).get("downloads") or {}
    out = []
    for m in CATALOG:
        job = downloads.get(m["repo"]) or {}
        out.append({**m, "name": m["repo"].split("/")[-1], "installed": m["repo"] in installed, "loaded": m["repo"] in loaded,
                    "download": {k: job.get(k) for k in ("state", "bytes", "error")} if job else None, "url": f"https://huggingface.co/{m['repo']}"})
    return out


def newer_on_hub(token: str | None) -> list[dict]:
    """Kimodo models published since this release: shown as news, because the deployed code cannot load them until updated."""
    try:
        found = hf.search(token, query="Kimodo", task=None, library=None, author="nvidia", tags=[], sort="lastModified", limit=30)
    except Exception:
        return []
    out = []
    for m in found:
        repo = m.get("id") or m.get("repo_id") or ""
        if repo.startswith("nvidia/Kimodo-") and repo not in KNOWN:
            out.append({"repo": repo, "name": repo.split("/")[-1], "compatible": "-G1-" in repo, "url": f"https://huggingface.co/{repo}"})
    return out


def summarize_test(result: dict) -> dict:
    keep = ("status", "model", "fps", "elapsed_s", "num_frames", "duration", "dof", "seed", "csv", "text_encoder")
    return {k: result.get(k) for k in keep}
