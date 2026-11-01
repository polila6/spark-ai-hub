"""Adapters for on-demand GPU containers that are not LLM servers: ComfyUI and the ML worker."""
from __future__ import annotations

import time

import httpx

from ..config import get_settings
from ..services import orchestrator
from .base import EngineAdapter, EngineError, Health, LoadResult


class _ContainerEngine(EngineAdapter):
    on_demand_container = True
    health_path = "/health"
    base = ""

    def health_check(self) -> Health:
        try:
            info = orchestrator.engines().get(self.name, {})
        except orchestrator.OrchestratorError as e:
            return Health(ok=False, status="down", detail=str(e))
        if not info.get("image_present"):
            return Health(ok=False, status="not_installed", detail=f"image {info.get('image')} not built")
        c = info.get("container")
        if not c or c.get("state") != "running":
            return Health(ok=True, status="stopped", detail="started on demand", extra={"image": info.get("image")})
        try:
            with httpx.Client(base_url=self.base, timeout=5) as cl:
                r = cl.get(self.health_path)
            return Health(ok=r.status_code == 200, status="ok" if r.status_code == 200 else "degraded",
                          extra={"image": info.get("image"), **(self._extra(r) if r.status_code == 200 else {})})
        except httpx.HTTPError:
            return Health(ok=False, status="degraded", detail="container running, API not ready")

    def _extra(self, r: httpx.Response) -> dict:
        return {}

    def start(self, timeout_s: float = 600) -> LoadResult:
        t0 = time.time()
        try:
            orchestrator.start_engine(self.name)
        except orchestrator.OrchestratorError as e:
            raise EngineError(f"{self.name} start failed: {e}") from e
        while time.time() - t0 < timeout_s:
            try:
                with httpx.Client(base_url=self.base, timeout=5) as cl:
                    if cl.get(self.health_path).status_code == 200:
                        return LoadResult(ok=True, seconds=time.time() - t0)
            except httpx.HTTPError:
                pass
            info = orchestrator.engines().get(self.name, {}).get("container") or {}
            if info.get("state") in ("exited", "dead"):
                logs = orchestrator.engine_logs(self.name, 80)
                raise EngineError(f"{self.name} exited during start", remediation=logs[-3000:])
            time.sleep(3)
        raise EngineError(f"{self.name} did not become healthy in {timeout_s}s")

    def stop(self) -> None:
        orchestrator.stop_engine(self.name)

    def is_running(self) -> bool:
        try:
            with httpx.Client(base_url=self.base, timeout=3) as cl:
                return cl.get(self.health_path).status_code == 200
        except httpx.HTTPError:
            return False


class ComfyUIAdapter(_ContainerEngine):
    name = "comfyui"
    capabilities = {"image", "video"}
    health_path = "/system_stats"

    def __init__(self) -> None:
        self.base = get_settings().comfyui_url

    def _extra(self, r):
        d = r.json()
        return {"comfyui_version": d.get("system", {}).get("comfyui_version"),
                "pytorch_version": d.get("system", {}).get("pytorch_version")}

    def submit(self, workflow: dict, client_id: str) -> str:
        with httpx.Client(base_url=self.base, timeout=30) as cl:
            r = cl.post("/prompt", json={"prompt": workflow, "client_id": client_id})
        if r.status_code != 200:
            raise EngineError(f"ComfyUI rejected workflow: {r.text[:1500]}", status=422)
        return r.json()["prompt_id"]

    def history(self, prompt_id: str) -> dict | None:
        with httpx.Client(base_url=self.base, timeout=15) as cl:
            r = cl.get(f"/history/{prompt_id}")
        return r.json().get(prompt_id) if r.status_code == 200 else None

    def queue(self) -> dict:
        with httpx.Client(base_url=self.base, timeout=10) as cl:
            return cl.get("/queue").json()

    def cancel(self, ref: str) -> None:
        with httpx.Client(base_url=self.base, timeout=10) as cl:
            q = cl.get("/queue").json()
            running = [x[1] for x in q.get("queue_running", [])]
            if ref in running:
                cl.post("/interrupt")
            cl.post("/queue", json={"delete": [ref]})

    def free_memory(self) -> None:
        try:
            with httpx.Client(base_url=self.base, timeout=30) as cl:
                cl.post("/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError:
            pass

    def object_info(self, node: str) -> dict:
        with httpx.Client(base_url=self.base, timeout=15) as cl:
            return cl.get(f"/object_info/{node}").json()


class MLWorkerAdapter(_ContainerEngine):
    name = "mlworker"
    capabilities = {"asr", "tts", "scientific"}

    def __init__(self) -> None:
        self.base = get_settings().mlworker_url

    def _extra(self, r):
        return r.json()

    def call(self, path: str, payload: dict, timeout: float = 3600) -> dict:
        with httpx.Client(base_url=self.base, timeout=httpx.Timeout(30, read=timeout)) as cl:
            r = cl.post(path, json=payload)
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:1000]
            raise EngineError(f"ML worker error: {detail}", status=r.status_code)
        return r.json()

    def cancel(self, ref: str) -> None:
        try:
            with httpx.Client(base_url=self.base, timeout=10) as cl:
                cl.post(f"/cancel/{ref}")
        except httpx.HTTPError:
            pass
