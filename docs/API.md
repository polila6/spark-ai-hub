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

OpenCode talks to the gateway as an OpenAI-compatible provider. The computer must be on the tailnet. The easiest way
is the **Connect tools** page: choose OpenCode, answer *"Which model do you want to use in OpenCode?"* (each chat
model shows its real context and whether it is the main model — always loaded — or loads on demand), press
**Create key for OpenCode** (an `inference` key named `OpenCode · <date>`) or paste a key of yours, and copy the
generated file. Step by step:

1. Open the config file: Windows `%USERPROFILE%\.config\opencode\opencode.json`, macOS/Linux
   `~/.config/opencode/opencode.json` (or `opencode.json` in a project folder, for that project only).
2. Replace its content with the generated JSON and save. **The key is written literally in the file** (`apiKey` and the
   MCP `Authorization` header), so no environment variable is needed — and the file is now a secret: do not share it or
   commit it to git.
3. Restart OpenCode (it reads the config only at start-up) and check `/models`: the chosen model is the default.
4. To change model later, pick another one on the page, copy the whole file again and restart OpenCode.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "spark": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Spark AI Hub",
      "options": {
        "baseURL": "https://your-spark.your-tailnet.ts.net/v1",
        "apiKey": "sah-...",
        "headerTimeout": 1800000
      },
      "models": {
        "qwen3.5-35b-a3b": { "name": "qwen3.5-35b-a3b (Spark · 64k · main)", "limit": { "context": 65536, "output": 8192 },
                             "reasoning": true, "tool_call": true }
      }
    }
  },
  "model": "spark/qwen3.5-35b-a3b",
  "small_model": "spark/qwen3.5-35b-a3b",
  "mcp": {
    "spark-cloud": { "type": "remote", "url": "https://your-spark.your-tailnet.ts.net/mcp",
                     "headers": { "Authorization": "Bearer sah-..." } }
  }
}
```

Notes:

* `small_model` is **the same model** on purpose. OpenCode uses it for titles and summaries; pointing it at a second
  "light" model makes the hub load that model too, which slows the main one down a lot (or evicts it).
* Only the chosen model is listed by default. The page has an optional checkbox to add the other chat models as
  alternatives in `/models`; models other than the main one load on demand (the first answer takes minutes and may
  unload other models).
* Limits come from the model: `context` is the window actually served (vLLM `max_model_len`, Ollama context) and
  `output` is `min(32768, context / 8)`; `reasoning: true` is set for reasoning models and `tool_call: true` when tool
  calling is enabled. `headerTimeout` (30 min) lets OpenCode wait while a model loads.

Requirements on the hub side, already applied to the installed models:

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
`/comparisons` · `/playground` (conversations, messages SSE — body fields `context_limit`, `auto_compact`, `web`, `research`; `conversations/{id}/context`, `/compact`, `DELETE /summary`; prompts, export) · `/skills` (list, catalog, install from GitHub/URL, create, patch, update, delete) · `/agents` (built-in + custom sub-agents CRUD) · `/canvas` (list by conversation, get, create, PUT with
`base_version`, `restore/{version}`, `export` md/docx/pdf/html/odt/code, delete) · `/workspaces` (list/create/rename/delete, `tree`, `file` GET/PUT, `raw`, `table`, `upload`, `import`, `mkdir`,
`move`, DELETE `file`, `run`, `zip`, `export`, `conversation`; chat messages accept `workspace_id`) · `/tasks` (scheduled tasks CRUD,
`run`) · `/connectors` (WebDAV/GitHub/Zotero/SMB CRUD, `test`, `sync`) · `/voice` (status, `transcribe` multipart,
`speak` → audio/wav) · `/usage` (`days`, `scope=me|all`) · `/auth/oidc` (`providers`, `{provider}/start`,
`{provider}/callback`) · `/models/{id}/context` (GET window info and KV capacity; POST `max_model_len`,
`speculative_tokens`, reloads in the background) · `/models/main` (see below) · `/media` (workflows, install, uploads,
generate, artifacts, audio/transcribe, audio/tts) · `/datasets` · `/science` (adapters, models, models/from-hf) ·
`/experiments` (CRUD, run) · `/api-keys` (CRUD, usage) · `/storage` (overview, hf-cache, gc) · `/admin` (settings,
hf-token status, audit, backups, restore-test, diagnostics, bundle).

### Main (general) model

The main model is the chat LLM kept resident for everyone (`pinned` + settings `preferred_resident_model` /
`default_model`; `chat_default_model` and `harness_default_model` follow it when they pointed at the previous main).

| Endpoint | Role | Description |
|---|---|---|
| `GET /api/v1/models/main` | any | `{alias, model, switch, pending, mode, idle_timeout_min, can_change}` — `model` is the registry entry plus `state` (`loaded`/`loading`/`unloading`/`stopped`), `reserved_gb`, `measured_gb`, `started_at`, `expected_load_seconds`; `switch` is the last switch (`state`: `loading`/`waiting`/`ready`/`failed`, `error`); `pending` is a model being downloaded to become main (with `download` progress and `error` if the install failed) |
| `GET /api/v1/models/main/plan?model_id=` | any | load-plan for making that model main: like `/{id}/load-plan` but the current main counts as freeable (`unload_previous`, `previous`, `previous_alias`) |
| `POST /api/v1/models/main` | admin/manager | body `{model_id}` or `{install: <POST /huggingface/install body>}`, optional `force`. Installed model → pins it, unpins the previous main, updates the default settings, makes it public and loads it in the background (the previous main is unloaded only if the room is needed). Not downloaded yet (or `install`) → starts/approves the install and records a pending marker (`pending: true`); the switch happens automatically when the install job succeeds. Errors: `400 not_llm`, `409 does_not_fit` (with `plan`; retry with `force: true`), `409 not_downloaded`, `409 install_failed`, `409 not_installed`, install errors (`blocked`, `license_ack_required`) |
| `DELETE /api/v1/models/main/pending` | admin/manager | forget the pending marker (the download continues) |

### Chat message actions

| Endpoint | Description |
|---|---|
| `POST /api/v1/playground/conversations/{cid}/messages/{mid}/regenerate` | `mid` must be a user message. Same body as `/messages` with optional `content` (`null` = answer again, text = edit the message); attachments are kept. Deletes every later message and streams the new answer (SSE, background turn like `/messages`). `409 turn_running` while a reply is running |
| `POST /api/v1/playground/conversations/{cid}/messages/{mid}/truncate` | "Resume from here": keeps `mid`, deletes what follows → `{ok, deleted, summary_cleared}` |

`/messages` streams a `{"type": "user_message", "message_id": N}` event when the turn starts. The context summary is
cleared when it covered an edited or deleted message.

**Messages while a turn runs.** `POST /messages` on a chat that is still working is not refused: the message is stored
and handed to the running task → `202 {"queued": true, "message_id": N}` (no SSE; keep following `/stream`). The agent
reads it before its next model step and emits `{"type": "user_injected", "message_id", "content"}`; the final answer is
saved after the injected messages (one assistant message covers them all; its `parts` contain
`{"type": "user_injected", "message_id"}` where each one was absorbed). Messages that were never injected (the turn hit
its safety limit, failed or was in image mode) are answered by an automatic follow-up turn with the same settings:
the `done`/`error` event carries `"followup": true` and, ~1.5 s later, the follow-up turn starts (re-attach to
`/stream`; it begins with `user_message {message_id, followup: true, message_ids}`). Stopping the turn does not start a
follow-up. `regenerate`, `truncate` and `compact` still answer `409 turn_running`.

**Turn events** (SSE `/messages`, `/stream`): `status`, `step {n}`, `delta {content}`, `reasoning {content}`,
`plan {items: [{content, status: pending|in_progress|completed}]}` (latest plan, also stored as a
`{"type": "plan", "items"}` part), `tool_start {id, name, label, args, started}` (epoch s), `tool_progress {id, message}`,
`tool_result {id, name, ok, summary, seconds, …}`, `context {used, limit}`, `compacted` (history summarised before the
turn), `context_compacted {before, after}` (the turn's own older steps summarised mid-turn, estimated tokens),
`user_injected`, `warning {message}`, `done {message_id, metrics, followup}`, `error {message, followup}`. The loop runs
until the plan is completed (admin settings `agent_max_steps_chat|workspace`, `agent_max_minutes_chat|workspace` are
only a safety net).

### Workspace terminal

| Endpoint | Description |
|---|---|
| `POST /api/v1/workspaces/{wid}/run` | body `{command, timeout_s?, background?}`; `command` may span several lines (run by bash as a script; CRLF normalised) |
| `GET /api/v1/workspaces/{wid}/runs` | background runs (survive leaving the page) |
| `POST /api/v1/workspaces/{wid}/runs/{rid}/stop` | stops a background run (its container exits with code 130) |
| `DELETE /api/v1/workspaces/{wid}/runs` | clears the finished runs (running ones are kept) |

Audited as `models.main`, `models.main_pending`, `models.main_pending_cancel`.

Pagination: `limit`/`offset` on large lists. Errors: `{"error": {"code", "message", ...}, "request_id"}`; every response
carries `X-Request-ID` for log correlation.
