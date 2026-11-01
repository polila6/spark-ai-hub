"""Common engine adapter interface.

Adapters implement what is meaningful for their engine and declare it in `capabilities`.
Methods that an engine cannot support raise `NotSupported` — callers must check
`capabilities` instead of assuming an LLM-shaped API for every modality.
See docs/MODEL_ADAPTERS.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class NotSupported(Exception):
    pass


class EngineError(Exception):
    def __init__(self, message: str, *, remediation: str | None = None, status: int = 502):
        super().__init__(message)
        self.message, self.remediation, self.status = message, remediation, status


@dataclass
class Health:
    ok: bool
    status: str  # ok | degraded | down | stopped | not_installed
    detail: str = ""
    version: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class LoadResult:
    ok: bool
    seconds: float
    measured_memory_gb: float | None = None
    detail: str = ""


class EngineAdapter(ABC):
    name: str = "base"
    #: subset of: chat, completion, embeddings, tools, streaming, image, video, asr, tts, scientific, multi_model
    capabilities: set[str] = set()
    #: True when the engine is a container started/stopped on demand by the engine manager
    on_demand_container: bool = False

    @abstractmethod
    def health_check(self) -> Health: ...

    def inspect(self, model) -> dict[str, Any]:
        raise NotSupported

    def check_compatibility(self, model) -> dict[str, Any]:
        raise NotSupported

    def estimate_resources(self, model) -> dict[str, Any]:
        raise NotSupported

    def install(self, model, **kw) -> None:
        raise NotSupported

    def load(self, model, params: dict) -> LoadResult:
        raise NotSupported

    def unload(self, model) -> None:
        raise NotSupported

    def loaded_models(self) -> list[dict]:
        return []

    def infer(self, model, payload: dict) -> dict:
        raise NotSupported

    def benchmark(self, model, config: dict) -> dict:
        raise NotSupported

    def cancel(self, ref: str) -> None:
        raise NotSupported

    # OpenAI-compatible base URL (LLM engines only)
    def openai_base(self, model=None) -> str:
        raise NotSupported
