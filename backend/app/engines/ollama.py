"""Ollama adapter (GGUF models, multi-model, always-on lightweight server)."""
from __future__ import annotations

import time
from collections.abc import Iterator

import httpx

from ..config import get_settings
from .base import EngineAdapter, EngineError, Health, LoadResult


class OllamaAdapter(EngineAdapter):
    name = "ollama"
    capabilities = {"chat", "completion", "embeddings", "streaming", "multi_model"}

    def __init__(self) -> None:
        self.base = get_settings().ollama_url

    def _c(self, timeout: float = 30) -> httpx.Client:
        return httpx.Client(base_url=self.base, timeout=timeout)

    def health_check(self) -> Health:
        try:
            with self._c(5) as c:
                v = c.get("/api/version").json().get("version")
            return Health(ok=True, status="ok", version=v)
        except httpx.HTTPError as e:
            return Health(ok=False, status="down", detail=str(e))

    def openai_base(self, model=None) -> str:
        return f"{self.base}/v1"

    def list_local(self) -> list[dict]:
        with self._c() as c:
            return c.get("/api/tags").json().get("models", [])

    def show(self, name: str) -> dict:
        with self._c() as c:
            r = c.post("/api/show", json={"model": name})
        if r.status_code != 200:
            raise EngineError(f"ollama show failed: {r.text[:200]}")
        return r.json()

    def loaded_models(self) -> list[dict]:
        with self._c(10) as c:
            return c.get("/api/ps").json().get("models", [])

    def pull(self, name: str) -> Iterator[dict]:
        with httpx.Client(base_url=self.base, timeout=httpx.Timeout(30, read=600)) as c:
            with c.stream("POST", "/api/pull", json={"model": name, "stream": True}) as r:
                if r.status_code != 200:
                    raise EngineError(f"ollama pull failed ({r.status_code}): {r.read()[:300].decode(errors='replace')}")
                import json

                for line in r.iter_lines():
                    if line.strip():
                        ev = json.loads(line)
                        if ev.get("error"):
                            raise EngineError(f"ollama pull error: {ev['error']}")
                        yield ev

    def delete(self, name: str) -> None:
        with self._c() as c:
            r = c.request("DELETE", "/api/delete", json={"model": name})
        if r.status_code not in (200, 404):
            raise EngineError(f"ollama delete failed: {r.text[:200]}")

    def load(self, model, params: dict) -> LoadResult:
        t0 = time.time()
        keep = params.get("keep_alive", "60m")
        body = {"model": model.engine_ref, "keep_alive": keep}
        caps = set(model.capabilities or [])
        endpoint = "/api/embed" if ("embeddings" in caps and "chat" not in caps) else "/api/generate"
        if endpoint == "/api/embed":
            body["input"] = "warmup"
        with httpx.Client(base_url=self.base, timeout=httpx.Timeout(30, read=1800)) as c:
            r = c.post(endpoint, json=body)
        if r.status_code != 200:
            raise EngineError(f"ollama load failed ({r.status_code}): {r.text[:300]}",
                              remediation="Check that the model is installed and fits in memory")
        mem = None
        for m in self.loaded_models():
            if m.get("name") == model.engine_ref or m.get("model") == model.engine_ref:
                mem = (m.get("size") or 0) / 1e9
        return LoadResult(ok=True, seconds=time.time() - t0, measured_memory_gb=mem,
                          detail="memory from ollama /api/ps 'size'")

    def unload(self, model) -> None:
        with self._c(60) as c:
            caps = set(model.capabilities or [])
            if "embeddings" in caps and "chat" not in caps:
                c.post("/api/embed", json={"model": model.engine_ref, "input": "", "keep_alive": 0})
            else:
                c.post("/api/generate", json={"model": model.engine_ref, "keep_alive": 0})
