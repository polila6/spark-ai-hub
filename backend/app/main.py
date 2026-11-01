"""Spark AI Hub backend (FastAPI modular monolith)."""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .logging_setup import log, request_id_var, setup_logging
from .models import Role, User
from .routers import (
    account,
    admin,
    assistant,
    anthropic_gateway,
    api_keys,
    auth,
    evaluation,
    files,
    huggingface,
    jobs,
    mcp_server,
    media,
    models,
    oidc,
    openai_gateway,
    playground,
    projects,
    science,
    system,
    users,
    workspaces,
)
from .security import hash_password
from .services.storage import ensure_layout

settings = get_settings()
setup_logging(settings.log_level, "backend")

app = FastAPI(
    title="Spark AI Hub API",
    version=settings.version,
    description="Private AI platform for NVIDIA DGX Spark. Session-cookie auth for /api/v1 (CSRF header required "
                "on unsafe methods); Bearer API keys for /v1 (OpenAI-compatible) and job endpoints.",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    token = request_id_var.set(rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    path = request.url.path
    if not path.endswith(("/stream", "/health", "/stream/updates")):
        # Only method, path, status, latency — never bodies or headers.
        log.info("http", method=request.method, path=path, status=response.status_code,
                 ms=round((time.perf_counter() - t0) * 1000, 1), request_id=rid)
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail, "request_id": getattr(request.state, "request_id", None)},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errs = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()][:20]
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Invalid request",
                                                            "fields": errs}})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", None)
    log.error("unhandled", path=request.url.path, error=f"{type(exc).__name__}: {exc}", request_id=rid)
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error",
                                                            "message": "Internal error; see logs with this request id"},
                                                  "request_id": rid})


for r in (auth, users, system, models, huggingface, jobs, evaluation, playground, media, science, api_keys, admin,
          account, files, projects, assistant, oidc, workspaces):
    app.include_router(r.router)
app.include_router(openai_gateway.router)
app.include_router(anthropic_gateway.router)
app.include_router(mcp_server.router)


@app.on_event("startup")
def bootstrap() -> None:
    ensure_layout()
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            pw = settings.secret("bootstrap_admin_password")
            if not pw:
                log.warning("bootstrap.no_admin_password")
                return
            db.add(User(username=settings.bootstrap_admin_username, display_name="Administrador", role=Role.admin,
                        password_hash=hash_password(pw), is_active=True, preferences={"language": "es"}))
            db.commit()
            log.info("bootstrap.admin_created", username=settings.bootstrap_admin_username)
