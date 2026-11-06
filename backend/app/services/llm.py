"""OpenAI-compatible calls to LLM engines with real timing measurements."""
from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx

from ..engines import EngineError, get_engine
from ..models import RegisteredModel


def engine_model_name(model: RegisteredModel) -> str:
    return model.engine_ref if model.backend == "ollama" else model.alias


def _client(model: RegisteredModel, read_timeout: float = 600) -> httpx.Client:
    return httpx.Client(base_url=get_engine(model.backend).openai_base(model),
                        timeout=httpx.Timeout(30, read=read_timeout))


ALLOWED_CHAT_PARAMS = {"messages", "temperature", "top_p", "max_tokens", "max_completion_tokens", "stop", "seed",
                       "presence_penalty", "frequency_penalty", "n", "response_format", "tools", "tool_choice",
                       "logprobs", "top_logprobs", "user", "stream", "stream_options"}


def supports_tools(model: RegisteredModel) -> bool:
    """vLLM only parses tool calls when started with a tool-call parser; Ollama reports it per model."""
    if model.backend == "vllm":
        params = (model.runtime_config.params if model.runtime_config else {}) or {}
        return bool(params.get("enable_tool_calling") and params.get("tool_call_parser"))
    return "tools" in set(model.capabilities or [])


OFF_EFFORT = {"none", "minimal", "off", "disabled", "false"}
ON_EFFORT = {"low", "medium", "high", "xhigh", "max", "on", "enabled", "true"}


def apply_reasoning_defaults(model: RegisteredModel, body: dict, default: str = "auto") -> dict:
    """OpenAI-compatible clients (Hermes, OpenCode…) cannot send a vLLM `enable_thinking`, but many send `reasoning_effort`
    (or `reasoning: {effort}`): «none»/«minimal» turn the model's thinking off, anything else on. When the client says nothing,
    the hub's `api_default_thinking` setting (auto = the model's own default, off, on) decides. An explicit
    chat_template_kwargs.enable_thinking from the client always wins. Only for vLLM models served with a reasoning parser."""
    if model.backend != "vllm":
        return body
    params = (model.runtime_config.params if model.runtime_config else {}) or {}
    if not params.get("reasoning_parser"):
        return body
    ck = body.get("chat_template_kwargs")
    if isinstance(ck, dict) and isinstance(ck.get("enable_thinking"), bool):
        return body
    effort = body.get("reasoning_effort")
    if effort is None and isinstance(body.get("reasoning"), dict):
        effort = body["reasoning"].get("effort")
    think = None
    if isinstance(effort, str):
        e = effort.strip().lower()
        think = False if e in OFF_EFFORT else True if e in ON_EFFORT else None
    if think is None and default in ("off", "on"):
        think = default == "on"
    if think is None:
        return body
    return {**body, "chat_template_kwargs": {**(ck if isinstance(ck, dict) else {}), "enable_thinking": think}}


def system_first(messages: list) -> list:
    """Many chat templates reject a system message anywhere but first (Claude Code and others send them mid-conversation):
    gather them all at the start, in order, without losing any."""
    if not any(isinstance(m, dict) and m.get("role") in ("system", "developer") for m in messages[1:]):
        return messages
    def text(c):
        return c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
    is_sys = lambda m: isinstance(m, dict) and m.get("role") in ("system", "developer")  # noqa: E731
    joined = "\n\n".join(t for t in (text(m.get("content")) for m in messages if is_sys(m)) if t)
    return [{"role": "system", "content": joined}, *(m for m in messages if not is_sys(m))]


def sanitize_chat_payload(model: RegisteredModel, body: dict) -> dict:
    payload = {k: v for k, v in body.items() if k in ALLOWED_CHAT_PARAMS}
    if isinstance(payload.get("messages"), list):
        payload["messages"] = system_first(payload["messages"])
    if model.backend == "vllm" and isinstance(body.get("chat_template_kwargs"), dict):
        # e.g. {"enable_thinking": false} for Qwen3/Qwen3.5 served with a reasoning parser
        payload["chat_template_kwargs"] = {k: v for k, v in body["chat_template_kwargs"].items()
                                           if k in ("enable_thinking",) and isinstance(v, bool)}
    if ("tools" in payload or "tool_choice" in payload) and not supports_tools(model):
        raise EngineError(f"Model '{model.alias}' does not support tool calling on its backend", status=400)
    payload["model"] = engine_model_name(model)
    return payload


def chat_once(model: RegisteredModel, body: dict) -> dict:
    payload = sanitize_chat_payload(model, body)
    payload["stream"] = False
    payload.pop("stream_options", None)
    with _client(model) as c:
        r = c.post("/chat/completions", json=payload)
    if r.status_code != 200:
        raise EngineError(f"Engine returned {r.status_code}: {r.text[:500]}", status=r.status_code)
    data = r.json()
    data["model"] = model.alias
    return data


def responses_payload(model: RegisteredModel, body: dict) -> dict:
    """OpenAI Responses request for the engine: the engine's model name; hosted tools it cannot run are dropped."""
    out = {**body, "model": engine_model_name(model)}
    if isinstance(out.get("tools"), list):
        out["tools"] = [t for t in out["tools"] if isinstance(t, dict) and t.get("type") == "function"]
        if not out["tools"]:
            out.pop("tools")
            out.pop("tool_choice", None)
    return out


def responses_once(model: RegisteredModel, body: dict) -> dict:
    with _client(model) as c:
        r = c.post("/responses", json=responses_payload(model, body))
    if r.status_code != 200:
        raise EngineError(f"Engine returned {r.status_code}: {r.text[:500]}", status=r.status_code)
    data = r.json()
    data["model"] = model.alias
    return data


def responses_stream_raw(model: RegisteredModel, body: dict) -> Iterator[bytes]:
    """Pass-through SSE stream of the Responses API (Codex), line by line."""
    with _client(model, read_timeout=1800) as c:
        with c.stream("POST", "/responses", json={**responses_payload(model, body), "stream": True}) as r:
            if r.status_code != 200:
                txt = r.read()[:500].decode(errors="replace")
                raise EngineError(f"Engine returned {r.status_code}: {txt}", status=r.status_code)
            for line in r.iter_lines():
                yield (line + "\n").encode()


def chat_stream_raw(model: RegisteredModel, body: dict) -> Iterator[bytes]:
    """Pass-through SSE stream (rewrites model name to the public alias)."""
    payload = sanitize_chat_payload(model, body)
    payload["stream"] = True
    payload.setdefault("stream_options", {"include_usage": True})
    with _client(model) as c:
        with c.stream("POST", "/chat/completions", json=payload) as r:
            if r.status_code != 200:
                txt = r.read()[:500].decode(errors="replace")
                raise EngineError(f"Engine returned {r.status_code}: {txt}", status=r.status_code)
            for line in r.iter_lines():
                if not line:
                    continue
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        obj = json.loads(line[6:])
                        obj["model"] = model.alias
                        line = "data: " + json.dumps(obj, ensure_ascii=False)
                    except ValueError:
                        pass
                yield (line + "\n\n").encode()


def chat_measured(model: RegisteredModel, messages: list[dict], params: dict | None = None,
                  on_delta=None, cancel_check=None) -> dict:
    """Streamed chat with client-side timing. TTFT = first content token; decode tps excludes TTFT."""
    params = dict(params or {})
    body = {"messages": messages, **{k: v for k, v in params.items() if k in ALLOWED_CHAT_PARAMS}}
    payload = sanitize_chat_payload(model, body)
    payload["stream"] = True
    payload["stream_options"] = {"include_usage": True}
    t0 = time.perf_counter()
    ttft = None
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage = None
    chunks = 0
    finish = None
    with _client(model) as c:
        with c.stream("POST", "/chat/completions", json=payload) as r:
            if r.status_code != 200:
                raise EngineError(f"Engine returned {r.status_code}: {r.read()[:500].decode(errors='replace')}",
                                  status=r.status_code)
            for line in r.iter_lines():
                if cancel_check and cancel_check():
                    break
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                obj = json.loads(line[6:])
                if obj.get("usage"):
                    usage = obj["usage"]
                for ch in obj.get("choices") or []:
                    d = ch.get("delta") or {}
                    piece = d.get("content") or ""
                    rpiece = d.get("reasoning_content") or d.get("reasoning") or ""
                    if (piece or rpiece) and ttft is None:
                        ttft = time.perf_counter() - t0
                    if piece:
                        text_parts.append(piece)
                        chunks += 1
                        if on_delta:
                            on_delta(piece)
                    if rpiece:
                        reasoning_parts.append(rpiece)
                        chunks += 1
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
    total = time.perf_counter() - t0
    completion_tokens = (usage or {}).get("completion_tokens")
    token_source = "engine_usage"
    if completion_tokens is None:
        completion_tokens = chunks
        token_source = "stream_chunks (approximation)"
    decode_time = total - (ttft or 0)
    tps = (completion_tokens - 1) / decode_time if completion_tokens and completion_tokens > 1 and decode_time > 0 else None
    return {
        "text": "".join(text_parts),
        "reasoning": "".join(reasoning_parts) or None,
        "finish_reason": finish,
        "ttft_ms": round(ttft * 1000, 1) if ttft is not None else None,
        "latency_ms": round(total * 1000, 1),
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": completion_tokens,
        "tokens_per_s": round(tps, 2) if tps else None,
        "token_count_source": token_source,
    }


def stream_json(model: RegisteredModel, payload: dict, cancel_check=None) -> Iterator[dict]:
    """Yield the parsed SSE chunks of an OpenAI-compatible streaming chat completion (payload already sanitised)."""
    payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
    with _client(model) as c:
        with c.stream("POST", "/chat/completions", json=payload) as r:
            if r.status_code != 200:
                raise EngineError(f"Engine returned {r.status_code}: {r.read()[:800].decode(errors='replace')}",
                                  status=r.status_code)
            for line in r.iter_lines():
                if cancel_check and cancel_check():
                    break
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    yield json.loads(line[6:])
                except ValueError:
                    continue


def supports_vision(model: RegisteredModel) -> bool:
    return "vision" in set(model.capabilities or [])


def context_tokens(model: RegisteredModel) -> int:
    """Usable context window: the vLLM max_model_len actually served, or the Ollama server-wide context."""
    if model.backend == "vllm":
        params = (model.runtime_config.params if model.runtime_config else {}) or {}
        return int(params.get("max_model_len") or 8192)
    import os

    return min(int(model.context_length or 32768), int(os.environ.get("SAH_OLLAMA_CONTEXT", "32768")))


def embeddings(model: RegisteredModel, body: dict) -> dict:
    payload = {k: v for k, v in body.items() if k in ("input", "encoding_format", "dimensions", "user")}
    payload["model"] = engine_model_name(model)
    with _client(model, 300) as c:
        r = c.post("/embeddings", json=payload)
    if r.status_code != 200:
        raise EngineError(f"Engine returned {r.status_code}: {r.text[:500]}", status=r.status_code)
    data = r.json()
    data["model"] = model.alias
    return data
