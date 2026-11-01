"""Client for the internal engine manager (the only holder of the Docker socket)."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings


class OrchestratorError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _client(timeout: float = 30) -> httpx.Client:
    s = get_settings()
    token = s.secret("orchestrator_token") or ""
    return httpx.Client(base_url=s.orchestrator_url, timeout=timeout,
                        headers={"Authorization": f"Bearer {token}"})


def _call(method: str, path: str, timeout: float = 30, **kw) -> Any:
    try:
        with _client(timeout) as c:
            r = c.request(method, path, **kw)
    except httpx.HTTPError as e:
        raise OrchestratorError(f"engine manager unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise OrchestratorError(str(detail), r.status_code)
    return r.json()


def containers() -> list[dict]:
    return _call("GET", "/containers")


def container_stats() -> dict:
    return _call("GET", "/containers/stats", timeout=20)


def engines() -> dict:
    return _call("GET", "/engines")


def start_vllm(params: dict) -> dict:
    return _call("POST", "/engines/vllm/start", json=params, timeout=120)


def start_engine(engine: str) -> dict:
    return _call("POST", f"/engines/{engine}/start", timeout=120)


def stop_engine(engine: str) -> dict:
    return _call("POST", f"/engines/{engine}/stop", timeout=90)


def stop_vllm(model: str | None = None) -> dict:
    return _call("POST", "/engines/vllm/stop", params={"model": model} if model else None, timeout=90)


def engine_logs(engine: str, tail: int = 200, model: str | None = None) -> str:
    params = {"tail": tail, **({"model": model} if model else {})}
    return _call("GET", f"/engines/{engine}/logs", params=params)["logs"]


def service_logs(service: str, tail: int = 200) -> str:
    return _call("GET", f"/services/{service}/logs", params={"tail": tail})["logs"]


def restart_service(service: str) -> dict:
    return _call("POST", f"/services/{service}/restart", timeout=90)


def versions() -> dict:
    return _call("GET", "/versions")
