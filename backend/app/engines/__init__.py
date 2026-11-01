"""Engine adapter registry. New adapters register here without changing callers."""
from __future__ import annotations

from functools import lru_cache

from .base import EngineAdapter, EngineError, NotSupported  # noqa: F401
from .containers import ComfyUIAdapter, MLWorkerAdapter
from .ollama import OllamaAdapter
from .vllm import VllmAdapter

_REGISTRY: dict[str, type[EngineAdapter]] = {
    "ollama": OllamaAdapter,
    "vllm": VllmAdapter,
    "comfyui": ComfyUIAdapter,
    "mlworker": MLWorkerAdapter,
}


@lru_cache
def get_engine(name: str) -> EngineAdapter:
    try:
        return _REGISTRY[name]()
    except KeyError as e:
        raise NotSupported(f"unknown engine {name}") from e


def all_engines() -> dict[str, EngineAdapter]:
    return {n: get_engine(n) for n in _REGISTRY}


LLM_ENGINES = {"ollama", "vllm"}
