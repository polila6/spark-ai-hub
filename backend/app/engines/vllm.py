"""vLLM adapter: one model per vLLM container, started on demand by the engine manager."""
from __future__ import annotations

import re
import time

import httpx

from ..services import compat, orchestrator, registry
from .base import EngineAdapter, EngineError, Health, LoadResult


def container_host(alias: str) -> str:
    """Same naming as the engine manager: one container per served model on the engines network."""
    slug = re.sub(r"[^a-z0-9]+", "-", alias.lower()).strip("-")[:48]
    return "sah-vllm-" + (slug or "model")


class VllmAdapter(EngineAdapter):
    """Several vLLM models can be resident at once, each in its own container (sah-vllm-<alias>)."""

    name = "vllm"
    capabilities = {"chat", "completion", "embeddings", "streaming", "tools"}
    on_demand_container = True

    def base_for(self, alias: str) -> str:
        return f"http://{container_host(alias)}:8000"

    def openai_base(self, model=None) -> str:
        if model is None:
            raise EngineError("vLLM needs the model to know which container serves it")
        return f"{self.base_for(model.alias)}/v1"

    def instances(self) -> list[dict]:
        try:
            return orchestrator.engines().get("vllm", {}).get("instances") or []
        except orchestrator.OrchestratorError:
            return []

    def health_check(self) -> Health:
        try:
            info = orchestrator.engines().get("vllm", {})
        except orchestrator.OrchestratorError as e:
            return Health(ok=False, status="down", detail=str(e))
        if not info.get("image_present"):
            return Health(ok=False, status="not_installed", detail=f"image {info.get('image')} not present")
        running = [i for i in info.get("instances") or [] if i.get("state") == "running"]
        if not running:
            return Health(ok=True, status="stopped", detail="no model loaded", extra={"image": info.get("image")})
        served, ver = [], None
        for i in running:
            try:
                with httpx.Client(base_url=self.base_for(i["model"]), timeout=5) as cl:
                    if cl.get("/health").status_code == 200:
                        served += [m["id"] for m in cl.get("/v1/models").json().get("data", [])]
                        ver = ver or cl.get("/version").json().get("version")
            except httpx.HTTPError:
                pass
        return Health(ok=True, status="ok" if served else "degraded", version=ver,
                      detail=None if served else "container running, API not ready (loading?)",
                      extra={"served": served, "containers": len(running), "image": info.get("image")})

    def loaded_models(self) -> list[dict]:
        out: list[dict] = []
        for i in self.instances():
            if i.get("state") != "running" or not i.get("model"):
                continue
            try:
                with httpx.Client(base_url=self.base_for(i["model"]), timeout=3) as cl:
                    out += cl.get("/v1/models").json().get("data", [])
            except httpx.HTTPError:
                pass
        return out

    def load(self, model, params: dict) -> LoadResult:
        t0 = time.time()
        body = {
            "model_path": f"/models/hf/{model.path}",
            "served_model_name": model.alias,
            "max_model_len": int(params.get("max_model_len") or 8192),
            "gpu_memory_utilization": float(params.get("gpu_memory_utilization") or 0.3),
            "max_num_seqs": int(params.get("max_num_seqs") or 16),
            "dtype": params.get("dtype", "auto"),
            "kv_cache_dtype": params.get("kv_cache_dtype", "auto"),
            "trust_remote_code": bool(model.trust_remote_code_approved and params.get("trust_remote_code")),
            "enable_tool_calling": bool(params.get("enable_tool_calling")),
            "tool_call_parser": params.get("tool_call_parser"),
            "reasoning_parser": params.get("reasoning_parser"),
            "enforce_eager": bool(params.get("enforce_eager")),
            "speculative_tokens": int(params.get("speculative_tokens") or 0),
            "task": "embed" if model.modality == "embedding" else "generate",
        }
        if params.get("quantization"):
            body["quantization"] = params["quantization"]
        if params.get("profile"):
            body["profile"] = params["profile"]
        rope = compat.yarn_scaling(registry.hf_config(model), body["max_model_len"])
        if rope:  # window beyond the native one → static YaRN
            body["rope_scaling"] = rope
        base = self.base_for(model.alias)
        # A container still starting for this model (e.g. the worker restarted mid-load) is waited for, not restarted:
        # a first start can spend a long time autotuning kernels. Unloads remove the container, so it is current.
        running = any(i.get("model") == model.alias and i.get("state") == "running" for i in self.instances())
        if not running:
            try:
                orchestrator.start_vllm(body)
            except orchestrator.OrchestratorError as e:
                raise EngineError(f"vLLM start failed: {e}", remediation="Check the vLLM image is pulled") from e
        deadline = time.time() + float(params.get("load_timeout_s") or 1800)
        while time.time() < deadline:
            time.sleep(5)
            info = next((i for i in self.instances() if i.get("model") == model.alias), {})
            if info.get("state") in ("exited", "dead"):
                logs = orchestrator.engine_logs("vllm", 60, model=model.alias)
                raise EngineError(f"vLLM exited during load (code {info.get('exit_code')}, oom={info.get('oom_killed')})",
                                  remediation=_remediation(logs) + "\n--- last log lines ---\n" + logs[-3000:])
            try:
                with httpx.Client(base_url=base, timeout=5) as cl:
                    if cl.get("/health").status_code == 200:
                        return LoadResult(ok=True, seconds=time.time() - t0,
                                          detail="vLLM reserves gpu_memory_utilization × total memory")
            except httpx.HTTPError:
                pass
        logs = orchestrator.engine_logs("vllm", 60, model=model.alias)
        orchestrator.stop_vllm(model.alias)
        raise EngineError("vLLM did not become healthy before timeout", remediation=logs[-3000:])

    def unload(self, model) -> None:
        orchestrator.stop_vllm(model.alias)


def _remediation(logs: str) -> str:
    low = logs.lower()
    if "out of memory" in low or "cuda error: out of memory" in low:
        return "Out of memory: lower max_model_len / max_num_seqs or gpu_memory_utilization, or unload other models."
    if "free memory on device" in low or "gpu_memory_utilization" in low:
        return ("Not enough free memory for the requested gpu_memory_utilization on unified memory. Unload other "
                "models or reduce gpu_memory_utilization.")
    if "trust_remote_code" in low:
        return "Model requires trust_remote_code: an administrator must approve custom code for this model."
    if "not supported" in low and "architecture" in low:
        return "Architecture not supported by the pinned vLLM image."
    return "See engine logs."
