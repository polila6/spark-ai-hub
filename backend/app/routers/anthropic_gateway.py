"""Anthropic Messages API compatibility (/v1/messages) so Claude Code and other Anthropic SDK clients can use the
local models. Requests are translated to the OpenAI chat format of the engines and responses (including streaming,
tool use and thinking) back to Anthropic events. Same API keys, quotas and rate limits as /v1/chat/completions.

Claude Code: ANTHROPIC_BASE_URL=https://<hub>  ANTHROPIC_AUTH_TOKEN=sah-...  ANTHROPIC_MODEL=<alias>.
Model names starting with "claude" are mapped to the administrator's harness defaults.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import orjson
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..engines import EngineError
from ..services import llm, scheduler
from ..settings_store import get_setting
from .openai_gateway import CLAUDE_PREFIX, GatewayError, _auth, _record, _resolve

router = APIRouter(prefix="/v1", tags=["anthropic-compatible"])

STOP_MAP = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens", "function_call": "tool_use"}


def a_error(status: int, message: str, type_: str = "invalid_request_error", headers=None):
    return JSONResponse(status_code=status, content={"type": "error", "error": {"type": type_, "message": message}},
                        headers=headers)


def _map_model(db: Session, name: str) -> str:
    if name.startswith(CLAUDE_PREFIX):  # a Spark model picked in Claude Code's /model
        return name[len(CLAUDE_PREFIX):]
    if not name.lower().startswith("claude"):
        return name
    small = any(x in name.lower() for x in ("haiku",))
    target = get_setting(db, "harness_small_model" if small else "harness_default_model") or \
        get_setting(db, "harness_default_model") or get_setting(db, "chat_default_model")
    if not target:
        raise GatewayError(404, f"Model '{name}' is not available here. Set ANTHROPIC_MODEL to a hub model alias "
                                "(GET /v1/models) or ask the administrator to configure the harness default model.",
                           code="model_not_found")
    return target


def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if b.get("type") == "text")


def _image_part(src: dict) -> dict | None:
    if src.get("type") == "base64":
        return {"type": "image_url", "image_url": {"url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"}}
    if src.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": src.get("url")}}
    return None


def to_openai(body: dict) -> dict:
    """Translate an Anthropic Messages request body into an OpenAI chat.completions body."""
    msgs: list[dict] = []
    system = body.get("system")
    if system:
        msgs.append({"role": "system", "content": _blocks_text(system)})
    for m in body.get("messages") or []:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in content if b.get("type") == "tool_use"]
            out = {"role": "assistant", "content": text or None}
            if calls:
                out["tool_calls"] = calls
            msgs.append(out)
            continue
        parts: list[dict] = []
        for b in content:
            t = b.get("type")
            if t == "tool_result":
                c = b.get("content")
                txt = c if isinstance(c, str) else "\n".join(
                    x.get("text", "") if x.get("type") == "text" else "[image]" for x in (c or []))
                if b.get("is_error"):
                    txt = f"[tool error] {txt}"
                msgs.append({"role": "tool", "tool_call_id": b.get("tool_use_id"), "content": txt or ""})
            elif t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image":
                ip = _image_part(b.get("source") or {})
                if ip:
                    parts.append(ip)
            elif t == "document":
                src = b.get("source") or {}
                if src.get("type") == "text":
                    parts.append({"type": "text", "text": src.get("data", "")})
                else:
                    parts.append({"type": "text", "text": "[document omitted: only text documents are supported]"})
        if parts:
            if all(p["type"] == "text" for p in parts):
                msgs.append({"role": "user", "content": "\n".join(p["text"] for p in parts)})
            else:
                msgs.append({"role": "user", "content": parts})
    out: dict = {"messages": msgs, "max_tokens": int(body.get("max_tokens") or 4096)}
    for k in ("temperature", "top_p"):
        if body.get(k) is not None:
            out[k] = body[k]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"][:4]
    tools = body.get("tools") or []
    fn_tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("input_schema") or {"type": "object"}}}
                for t in tools if t.get("name") and (t.get("input_schema") is not None or not t.get("type"))]
    if fn_tools:
        out["tools"] = fn_tools
        tc = body.get("tool_choice") or {}
        if tc.get("type") == "any":
            out["tool_choice"] = "required"
        elif tc.get("type") == "tool" and tc.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif tc.get("type") == "none":
            out["tool_choice"] = "none"
    return out


def from_openai(data: dict, model_name: str) -> dict:
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content: list[dict] = []
    if msg.get("reasoning_content") or msg.get("reasoning"):
        content.append({"type": "thinking", "thinking": msg.get("reasoning_content") or msg.get("reasoning"),
                        "signature": ""})
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except ValueError:
            args = {"_raw": tc["function"].get("arguments")}
        content.append({"type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
                        "name": tc["function"]["name"], "input": args})
    u = data.get("usage") or {}
    return {"id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "model": model_name,
            "content": content, "stop_reason": "tool_use" if msg.get("tool_calls") else STOP_MAP.get(ch.get("finish_reason"), "end_turn"),
            "stop_sequence": None,
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}}


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: ".encode() + orjson.dumps(data) + b"\n\n"


class StreamTranslator:
    """OpenAI chat.completion.chunk stream → Anthropic message stream events."""

    def __init__(self, model_name: str, thinking: bool):
        self.model = model_name
        self.thinking = thinking
        self.index = -1
        self.open: str | None = None  # "text" | "thinking" | "tool:<n>"
        self.tool_blocks: dict[int, int] = {}
        self.finish = None
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.saw_tool = False

    def start(self) -> bytes:
        return _sse("message_start", {"type": "message_start", "message": {
            "id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant", "model": self.model,
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})

    def _close(self) -> bytes:
        if self.open is None:
            return b""
        out = b""
        if self.open == "thinking":
            out += _sse("content_block_delta", {"type": "content_block_delta", "index": self.index,
                                                "delta": {"type": "signature_delta", "signature": ""}})
        out += _sse("content_block_stop", {"type": "content_block_stop", "index": self.index})
        self.open = None
        return out

    def _open(self, kind: str, block: dict) -> bytes:
        out = self._close()
        self.index += 1
        self.open = kind
        return out + _sse("content_block_start", {"type": "content_block_start", "index": self.index, "content_block": block})

    def feed(self, chunk: dict) -> bytes:
        out = b""
        if chunk.get("usage"):
            self.usage = {"input_tokens": chunk["usage"].get("prompt_tokens", 0),
                          "output_tokens": chunk["usage"].get("completion_tokens", 0)}
        for ch in chunk.get("choices") or []:
            d = ch.get("delta") or {}
            r = d.get("reasoning_content") or d.get("reasoning")
            if r and self.thinking:
                if self.open != "thinking":
                    out += self._open("thinking", {"type": "thinking", "thinking": ""})
                out += _sse("content_block_delta", {"type": "content_block_delta", "index": self.index,
                                                    "delta": {"type": "thinking_delta", "thinking": r}})
            if d.get("content"):
                if self.open != "text":
                    out += self._open("text", {"type": "text", "text": ""})
                out += _sse("content_block_delta", {"type": "content_block_delta", "index": self.index,
                                                    "delta": {"type": "text_delta", "text": d["content"]}})
            for tc in d.get("tool_calls") or []:
                n = tc.get("index", 0)
                fn = tc.get("function") or {}
                if self.open != f"tool:{n}":
                    self.saw_tool = True
                    out += self._open(f"tool:{n}", {"type": "tool_use", "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
                                                     "name": fn.get("name") or "", "input": {}})
                if fn.get("arguments"):
                    out += _sse("content_block_delta", {"type": "content_block_delta", "index": self.index,
                                                        "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}})
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
        return out

    def end(self) -> bytes:
        stop = "tool_use" if self.saw_tool and self.finish in (None, "stop", "tool_calls") else STOP_MAP.get(self.finish, "end_turn")
        return self._close() + _sse("message_delta", {"type": "message_delta",
                                                      "delta": {"stop_reason": stop, "stop_sequence": None},
                                                      "usage": self.usage}) + _sse("message_stop", {"type": "message_stop"})


@router.post("/messages")
async def messages(request: Request, db: Session = Depends(get_db)):
    t0 = time.perf_counter()
    try:
        p = _auth(request, db)
        try:
            body = await request.json()
        except ValueError:
            return a_error(400, "Request body must be JSON")
        if not isinstance(body, dict) or not body.get("model") or not isinstance(body.get("messages"), list):
            return a_error(400, "'model', 'messages' and 'max_tokens' are required")
        model = _resolve(db, p, _map_model(db, str(body["model"])))
    except GatewayError as e:
        st, msg, type_, _code, headers = e.args_
        return a_error(st, msg, {"authentication_error": "authentication_error", "rate_limit_error": "rate_limit_error",
                                 "permission_error": "permission_error"}.get(type_, "invalid_request_error")
                       if st != 404 else "not_found_error", headers)
    if "chat" not in (model.capabilities or []):
        return a_error(400, f"Model '{model.alias}' does not support chat")
    oa = to_openai(body)
    if oa.get("tools") and not llm.supports_tools(model):
        return a_error(400, f"Model '{model.alias}' does not support tool use on its backend; choose a model with tools")
    thinking = (body.get("thinking") or {}).get("type") in ("enabled", "adaptive")
    try:
        inst = await asyncio.to_thread(scheduler.ensure_loaded, model.id, wait_s=1800)
    except scheduler.SchedulingError as e:
        return a_error(529, e.message, "overloaded_error", {"Retry-After": str(e.retry_after or 60)})
    except EngineError as e:
        return a_error(500, f"Engine failed to load model: {e.message}", "api_error")
    if model.backend == "vllm" and ((model.runtime_config.params if model.runtime_config else {}) or {}).get("reasoning_parser"):
        oa["chat_template_kwargs"] = {"enable_thinking": thinking}
    payload = llm.sanitize_chat_payload(model, oa)
    alias = model.alias
    scheduler.begin_request(inst.id)
    if body.get("stream"):
        tr = StreamTranslator(alias, thinking)

        def gen():
            status = 200
            try:
                yield tr.start()
                for chunk in llm.stream_json(model, payload):
                    data = tr.feed(chunk)
                    if data:
                        yield data
                yield tr.end()
            except EngineError as e:
                status = 500
                yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": e.message}})
            finally:
                scheduler.end_request(inst.id)
                _record(p, "anthropic.messages", alias, status,
                        {"prompt_tokens": tr.usage["input_tokens"], "completion_tokens": tr.usage["output_tokens"]}, t0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        data = await asyncio.to_thread(llm.chat_once, model, oa)
    except EngineError as e:
        _record(p, "anthropic.messages", alias, 500, None, t0)
        return a_error(500, e.message, "api_error")
    finally:
        scheduler.end_request(inst.id)
    out = from_openai(data, alias)
    _record(p, "anthropic.messages", alias, 200, data.get("usage"), t0)
    return out


@router.post("/messages/count_tokens")
async def count_tokens(request: Request, db: Session = Depends(get_db)):
    """Approximate token count (≈ characters / 3.5); the engines do not expose a tokenizer endpoint."""
    try:
        _auth(request, db)
    except GatewayError as e:
        st, msg, *_ = e.args_
        return a_error(st, msg, "authentication_error" if st == 401 else "invalid_request_error")
    body = await request.json()
    oa = to_openai(body)
    chars = sum(len(m["content"]) if isinstance(m.get("content"), str) else
                sum(len(p.get("text", "")) + (1500 if p.get("type") == "image_url" else 0) for p in (m.get("content") or []))
                for m in oa["messages"]) + len(json.dumps(oa.get("tools") or []))
    return {"input_tokens": int(chars / 3.5) + 1}
