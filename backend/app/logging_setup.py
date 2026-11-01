"""Structured JSON logging with request ids. Never log credentials or prompt contents."""
from __future__ import annotations

import contextvars
import logging
import re
import sys

import structlog

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_SECRET_PATTERNS = [
    re.compile(r"(sah-)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(hf_)[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(password[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"),
]


def redact(text: str) -> str:
    for p in _SECRET_PATTERNS:
        text = p.sub(r"\1***", text)
    return text


def _add_request_id(_, __, event_dict):
    rid = request_id_var.get()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def _redact_processor(_, __, event_dict):
    for k, v in list(event_dict.items()):
        if isinstance(v, str):
            event_dict[k] = redact(v)
    return event_dict


def setup_logging(level: str = "INFO", service: str = "backend") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)
    # Silence noisy libraries that may print URLs with tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


log = structlog.get_logger()
