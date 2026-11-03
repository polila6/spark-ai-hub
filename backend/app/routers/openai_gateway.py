"""OpenAI-compatible gateway (/v1). Authenticated by per-user API keys with the 'inference' scope.

Routes to the engine that serves each registered model and loads it on demand through the
scheduler. Prompts/responses are not stored unless the administrator enables content logging.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import orjson
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import Principal, api_key_principal, visible_filter
from ..engines import LLM_ENGINES, EngineError
from ..logging_setup import request_id_var
from ..models import ApiUsage, ModelStatus, RegisteredModel, Role
from ..redis_client import rate_limit
from ..services import llm, scheduler
from ..services.storage import effective_policy
from ..settings_store import get_setting

router = APIRouter(prefix="/v1", tags=["openai-compatible"])


def oai_error(status: int, message: str, type_: str = "invalid_request_error", code: str | None = None, headers=None):
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": type_, "code": code}},
                        headers=headers)


class GatewayError(Exception):
    def __init__(self, status, message, type_="invalid_request_error", code=None, headers=None):
        self.args_ = (status, message, type_, code, headers)


def _auth(request: Request, db: Session) -> Principal:
    from fastapi import HTTPException

    try:
        p = api_key_principal(request, db, scope="inference")
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        raise GatewayError(e.status_code, d.get("message", "unauthorized"), "authentication_error", d.get("code"))
    if p is None:
        raise GatewayError(401, "Missing API key. Use 'Authorization: Bearer sah-...'", "authentication_error",
                           "missing_api_key")
    if p.user.role == Role.viewer and not get_setting(db, "features").get("viewer_inference", True):
        raise GatewayError(403, "Viewer accounts cannot use inference", "permission_error")
    key = p.api_key
    pol = effective_policy(db, p.user)
    rpm = key.rpm_limit or pol.api_rpm
    ok, retry = rate_limit(f"api:{key.id}", rpm, 60)
    if not ok:
        raise GatewayError(429, f"Rate limit exceeded ({rpm} requests/min)", "rate_limit_error", "rate_limited",
                           {"Retry-After": str(retry)})
    quota = key.monthly_token_quota or pol.monthly_token_quota
    if quota:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = db.scalar(select(func.coalesce(func.sum(ApiUsage.prompt_tokens + ApiUsage.completion_tokens), 0))
                         .where(ApiUsage.api_key_id == key.id, ApiUsage.ts >= start)) or 0
        if used >= quota:
            raise GatewayError(429, f"Monthly token quota exhausted ({used}/{quota})", "insufficient_quota", "quota")
    return p


CLAUDE_PREFIX = "claude-spark-"


def _resolve(db: Session, p: Principal, name: str) -> RegisteredModel:
    q = select(RegisteredModel).where(RegisteredModel.alias == name)
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    m = db.scalar(q)
    if not m:
        raise GatewayError(404, f"The model '{name}' does not exist or you do not have access to it",
                           code="model_not_found")
    if m.backend not in LLM_ENGINES:
        raise GatewayError(400, f"Model '{name}' is not served through the OpenAI-compatible API", code="unsupported_model")
    if m.status in (ModelStatus.disabled, ModelStatus.failed, ModelStatus.discovered, ModelStatus.downloading,
                    ModelStatus.removing):
        raise GatewayError(409, f"Model '{name}' is {m.status.value}", code="model_unavailable")
    return m


def _record(p: Principal | None, endpoint: str, model: str | None, status: int, usage: dict | None, t0: float,
            content: dict | None = None, source: str = "api") -> None:
    with SessionLocal() as db:
        log_content = bool(get_setting(db, "log_request_content"))
        db.add(ApiUsage(user_id=p.user.id if p else None, api_key_id=p.api_key.id if p and p.api_key else None,
                        source=source, endpoint=endpoint, model=model, status_code=status,
                        prompt_tokens=(usage or {}).get("prompt_tokens") or 0,
                        completion_tokens=(usage or {}).get("completion_tokens") or 0,
                        latency_ms=round((time.perf_counter() - t0) * 1000, 1), request_id=request_id_var.get(),
                        content=content if log_content else None))
        db.commit()


def _handle(fn):
    import functools

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except GatewayError as e:
            return oai_error(*e.args_)
    return wrapper


@router.get("/models")
@_handle
async def list_models(request: Request, db: Session = Depends(get_db)):
    p = _auth(request, db)
    q = select(RegisteredModel).where(RegisteredModel.backend.in_(list(LLM_ENGINES)),
                                      RegisteredModel.status.in_([ModelStatus.installed, ModelStatus.ready,
                                                                  ModelStatus.busy, ModelStatus.loading]))
    if not p.is_admin:
        q = q.where(visible_filter(RegisteredModel, p, "model"))
    main = get_setting(db, "preferred_resident_model")

    def about(m: RegisteredModel) -> str:  # shown by clients with a model picker (Claude Code's /model)
        ctx = llm.context_tokens(m)
        state = "principal, siempre cargado" if m.alias == main else "en memoria" if m.status in (ModelStatus.ready, ModelStatus.busy) else \
            "se carga bajo demanda (la primera respuesta tarda)"
        return f"Spark · {round(ctx / 1024)}k de contexto · {state}"

    data = [{"id": m.alias, "object": "model", "type": "model", "display_name": m.alias, "description": about(m),
             "created": int(m.created_at.timestamp()), "owned_by": "spark-ai-hub",
             "sah": {"backend": m.backend, "status": m.status.value, "capabilities": m.capabilities or [],
                     "context_length": m.context_length, "modality": m.modality}} for m in db.scalars(q)]
    # Claude Code's /model picker only lists ids containing "claude" or "anthropic": give it an extra spelling of each
    # chat model (the Anthropic gateway resolves the prefix). Other clients keep the plain aliases.
    if "claude" in request.headers.get("user-agent", "").lower() or request.headers.get("anthropic-version"):
        data = [{**d, "id": f"{CLAUDE_PREFIX}{d['id']}"} for d in data if "chat" in (d["sah"]["capabilities"] or [])]
    return {"object": "list", "data": data}


@router.post("/chat/completions")
@_handle
async def chat_completions(request: Request, db: Session = Depends(get_db)):
    import asyncio

    t0 = time.perf_counter()
    p = _auth(request, db)
    try:
        body = await request.json()
    except ValueError:
        raise GatewayError(400, "Request body must be JSON")
    if not isinstance(body, dict) or not body.get("model") or not isinstance(body.get("messages"), list):
        raise GatewayError(400, "'model' and 'messages' are required")
    model = _resolve(db, p, str(body["model"]))
    if "chat" not in (model.capabilities or []):
        raise GatewayError(400, f"Model '{model.alias}' does not support chat completions", code="unsupported_capability")
    try:
        inst = await asyncio.to_thread(scheduler.ensure_loaded, model.id, wait_s=1800)
    except scheduler.SchedulingError as e:
        _record(p, "chat.completions", model.alias, 503, None, t0)
        raise GatewayError(503, e.message, "server_error", e.code, {"Retry-After": str(e.retry_after or 60)})
    except EngineError as e:
        _record(p, "chat.completions", model.alias, 502, None, t0)
        raise GatewayError(502, f"Engine failed to load model: {e.message}", "server_error", "engine_error")
    scheduler.begin_request(inst.id)
    if body.get("stream"):
        def gen():
            usage = None
            status = 200
            try:
                for chunk in llm.chat_stream_raw(model, body):
                    if b'"usage"' in chunk:
                        try:
                            u = orjson.loads(chunk[6:].strip()).get("usage")
                            usage = u or usage
                        except ValueError:
                            pass
                    yield chunk
                yield b"data: [DONE]\n\n"
            except EngineError as e:
                status = 502
                err = {"error": {"message": e.message, "type": "server_error"}}
                yield b"data: " + orjson.dumps(err) + b"\n\n"
            finally:
                scheduler.end_request(inst.id)
                _record(p, "chat.completions", model.alias, status, usage, t0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        data = await asyncio.to_thread(llm.chat_once, model, body)
    except EngineError as e:
        _record(p, "chat.completions", model.alias, e.status, None, t0)
        raise GatewayError(e.status if 400 <= e.status < 600 else 502, e.message, "server_error")
    finally:
        scheduler.end_request(inst.id)
    _record(p, "chat.completions", model.alias, 200, data.get("usage"), t0,
            {"messages": body.get("messages"), "response": data.get("choices")})
    return data


@router.post("/responses")
@_handle
async def responses(request: Request, db: Session = Depends(get_db)):
    """OpenAI Responses API (Codex needs it): served by the engine itself, with the hub's keys, quotas and usage."""
    import asyncio

    t0 = time.perf_counter()
    p = _auth(request, db)
    try:
        body = await request.json()
    except ValueError:
        raise GatewayError(400, "Request body must be JSON")
    if not isinstance(body, dict) or not body.get("model"):
        raise GatewayError(400, "'model' is required")
    model = _resolve(db, p, str(body["model"]))
    if "chat" not in (model.capabilities or []):
        raise GatewayError(400, f"Model '{model.alias}' does not support chat", code="unsupported_capability")
    try:
        inst = await asyncio.to_thread(scheduler.ensure_loaded, model.id, wait_s=1800)
    except scheduler.SchedulingError as e:
        _record(p, "responses", model.alias, 503, None, t0)
        raise GatewayError(503, e.message, "server_error", e.code, {"Retry-After": str(e.retry_after or 60)})
    except EngineError as e:
        _record(p, "responses", model.alias, 502, None, t0)
        raise GatewayError(502, f"Engine failed to load model: {e.message}", "server_error", "engine_error")
    scheduler.begin_request(inst.id)
    if body.get("stream"):
        def gen():
            usage = None
            status = 200
            try:
                for line in llm.responses_stream_raw(model, body):
                    if line.startswith(b"data: ") and b'"response.completed"' in line:
                        try:
                            u = (orjson.loads(line[6:]).get("response") or {}).get("usage") or {}
                            usage = {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens")}
                        except ValueError:
                            pass
                    yield line
            except EngineError as e:
                status = 502
                yield b"event: error\ndata: " + orjson.dumps({"type": "error", "message": e.message}) + b"\n\n"
            finally:
                scheduler.end_request(inst.id)
                _record(p, "responses", model.alias, status, usage, t0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        data = await asyncio.to_thread(llm.responses_once, model, body)
    except EngineError as e:
        _record(p, "responses", model.alias, e.status, None, t0)
        raise GatewayError(e.status if 400 <= e.status < 600 else 502, e.message, "server_error")
    finally:
        scheduler.end_request(inst.id)
    u = data.get("usage") or {}
    _record(p, "responses", model.alias, 200, {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens")}, t0)
    return data


@router.post("/embeddings")
@_handle
async def create_embeddings(request: Request, db: Session = Depends(get_db)):
    import asyncio

    t0 = time.perf_counter()
    p = _auth(request, db)
    body = await request.json()
    if not body.get("model") or body.get("input") is None:
        raise GatewayError(400, "'model' and 'input' are required")
    model = _resolve(db, p, str(body["model"]))
    if "embeddings" not in (model.capabilities or []):
        raise GatewayError(400, f"Model '{model.alias}' does not provide embeddings", code="unsupported_capability")
    try:
        inst = await asyncio.to_thread(scheduler.ensure_loaded, model.id, wait_s=900)
    except scheduler.SchedulingError as e:
        raise GatewayError(503, e.message, "server_error", e.code, {"Retry-After": str(e.retry_after or 60)})
    scheduler.begin_request(inst.id)
    try:
        data = await asyncio.to_thread(llm.embeddings, model, body)
    except EngineError as e:
        _record(p, "embeddings", model.alias, 502, None, t0)
        raise GatewayError(502, e.message, "server_error")
    finally:
        scheduler.end_request(inst.id)
    _record(p, "embeddings", model.alias, 200, data.get("usage"), t0)
    return data


_ = uuid
