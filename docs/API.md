# API

* Interactive OpenAPI docs: `https://your-spark.your-tailnet.ts.net/api/docs` (schema `/api/openapi.json`).
* Only reachable inside the tailnet.

## Authentication

| Surface | Auth | Notes |
|---|---|---|
| `/v1/*` (OpenAI-compatible) | `Authorization: Bearer sah-…` key with scope **inference** | per-key rpm + monthly token quota |
| `/api/v1/*` from the browser | session cookie + `X-CSRF-Token` header on POST/PUT/PATCH/DELETE | |
| `/api/v1/jobs`, benchmarks, media, science (programmatic) | Bearer key with scope **jobs** | never accepted on admin/settings/users/api-keys endpoints |

Create keys in **Centro API → Claves API**. The full key is shown once.

## OpenAI-compatible endpoints

| Endpoint | Supported | Notes |
|---|---|---|
| `GET /v1/models` | yes | models visible to the key owner, installed/ready, LLM engines only; extra `sah` field with backend/capabilities |
| `POST /v1/chat/completions` | yes | `stream`, `temperature`, `top_p`, `max_tokens`, `stop`, `seed`, `n`, `response_format`, `logprobs`; `tools` for Ollama models whose capabilities include `tools`, and for vLLM models whose runtime config sets `enable_tool_calling` + `tool_call_parser` (else HTTP 400) |
| `POST /v1/embeddings` | yes, for models with `embeddings` capability | |
| others (`/v1/completions`, images, audio) | not exposed | use the hub job API |

Models are loaded on demand: the first request may take seconds (Ollama) to ~2 minutes (vLLM). If memory cannot be
reserved the API answers **503** with `Retry-After`. Errors use the OpenAI error envelope.

### Python (OpenAI SDK)

```python
from openai import OpenAI
client = OpenAI(base_url="https://your-spark.your-tailnet.ts.net/v1", api_key="sah-...")
print([m.id for m in client.models.list().data])
r = client.chat.completions.create(model="qwen3-4b-instruct",
        messages=[{"role": "user", "content": "Hola"}], max_tokens=64)
print(r.choices[0].message.content, r.usage)
for chunk in client.chat.completions.create(model="qwen3-8b-fp8", stream=True,
        messages=[{"role": "user", "content": "Explica la memoria unificada"}]):
    print(chunk.choices[0].delta.content or "", end="")
```

### cURL

```bash
curl https://your-spark.your-tailnet.ts.net/v1/chat/completions \
  -H "Authorization: Bearer $SAH_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"qwen3-4b-instruct","messages":[{"role":"user","content":"Hola"}],"max_tokens":64}'
```

### OpenCode (coding agent)

OpenCode talks to the gateway as an OpenAI-compatible provider. The computer must be on the tailnet. Create an
API key with scope `inference` (**API keys** page), then put this in `~/.config/opencode/opencode.json`
(Windows: `%USERPROFILE%\.config\opencode\opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "spark": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Spark AI Hub",
      "options": {
        "baseURL": "https://your-spark.your-tailnet.ts.net/v1",
        "apiKey": "{env:SPARK_API_KEY}"
      },
      "models": {
        "qwen3.5-35b-a3b":   { "name": "Qwen3.5 35B-A3B (vLLM)", "limit": { "context": 65536, "output": 16384 } },
        "qwen3-8b-fp8":      { "name": "Qwen3 8B FP8 (vLLM)",    "limit": { "context": 32768, "output": 8192 } },
        "qwen3-4b-instruct": { "name": "Qwen3 4B (Ollama)",      "limit": { "context": 32768, "output": 8192 } }
      }
    }
  },
  "model": "spark/qwen3.5-35b-a3b",
  "small_model": "spark/qwen3-4b-instruct"
}
```

and set `SPARK_API_KEY` in the environment (or run `opencode auth login`, choose *Other*, provider id `spark`, and
remove the `apiKey` line). Requirements on the hub side, already applied to the installed models:

* tool calling — vLLM models need `enable_tool_calling: true` and a parser (`hermes` for Qwen3, `qwen3_coder` for
  Qwen3.5); `reasoning_parser: qwen3` keeps the thinking out of the answer text;
* context — OpenCode's system prompt and tool definitions are ~10k tokens. Ollama runs with
  `OLLAMA_CONTEXT_LENGTH=32768` (8192 silently truncated the start of the prompt); vLLM models use
  `max_model_len` 32768 (Qwen3 8B) and 65536 (Qwen3.5 35B-A3B).

The first request after a model is unloaded waits for it to load (≈2 min for Qwen3 8B, ≈6 min for Qwen3.5 35B-A3B).
Loading the 35B model (≈90 GB) can evict other idle models.

## Async jobs (key with scope `jobs`)

```python
import time, httpx
H = {"Authorization": "Bearer sah-..."}
API = "https://your-spark.your-tailnet.ts.net/api/v1"
models = httpx.get(f"{API}/models", headers=H).json()
run = httpx.post(f"{API}/benchmarks", headers=H, json={"model_id": models[0]["id"], "suite": "perf-short-v1"}).json()
while (j := httpx.get(f"{API}/jobs/{run['job_id']}", headers=H).json())["status"] not in ("completed", "failed", "cancelled"):
    time.sleep(5)
print(j["result"])
img = httpx.post(f"{API}/media/generate", headers=H, json={"workflow_id": "sd15-txt2img",
                 "inputs": {"prompt": "a lighthouse at dusk", "seed": 1}}).json()
# poll /jobs/{job_id}; download artifacts: GET {artifact.url} with the same header
```

Note: unsafe methods with a session cookie need CSRF; with a Bearer key no CSRF is needed.

## Main REST groups

`/api/v1/auth` (login, logout, me, preferences, password, sessions, invitations) · `/users` (admin; policies) ·
`/system` (health, status, containers) · `/metrics` (current, recent, history, stream SSE, alerts) · `/engines` ·
`/models` (list, detail, patch, load, unload, check, smoke-test, shares, delete, import) · `/huggingface` (search, models,
card, refs, plan, install) · `/downloads` (list, approve, pause, resume, cancel) · `/recommendations` · `/jobs` (list,
detail, events, cancel, retry, test, stream/updates SSE) · `/benchmarks` (suites, run, list, detail, export) ·
`/comparisons` · `/playground` (conversations, messages SSE — body fields `context_limit`, `auto_compact`, `web`; `conversations/{id}/context`, `/compact`, `DELETE /summary`; prompts, export) · `/skills` (list, catalog, install from GitHub/URL, create, patch, update, delete) · `/agents` (built-in + custom sub-agents CRUD) · `/media` (workflows, install, uploads,
generate, artifacts, audio/transcribe, audio/tts) · `/datasets` · `/science` (adapters, models, models/from-hf) ·
`/experiments` (CRUD, run) · `/api-keys` (CRUD, usage) · `/storage` (overview, hf-cache, gc) · `/admin` (settings,
hf-token status, audit, backups, restore-test, diagnostics, bundle).

Pagination: `limit`/`offset` on large lists. Errors: `{"error": {"code", "message", ...}, "request_id"}`; every response
carries `X-Request-ID` for log correlation.
