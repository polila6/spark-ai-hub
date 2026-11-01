# Architecture

## Services

| Service | Image | Network(s) | Role |
|---|---|---|---|
| `proxy` | caddy:2.11.4-alpine | frontnet; publishes **127.0.0.1:8080** only | Routes `/api/*`, `/v1/*` → backend, rest → frontend; security headers; SSE flush |
| `frontend` | spark-ai-hub/frontend (Node 22, Next.js 15 standalone) | frontnet | UI (client-rendered pages, SWR, i18n es/en, dark/light) |
| `backend` | spark-ai-hub/backend (Python 3.12, FastAPI) | frontnet, data, control, engines, egress | REST API `/api/v1`, OpenAI gateway `/v1`, auth/RBAC, registry, HF, scheduler decisions for interactive loads |
| `worker` | same image, `python -m app.worker.main` | data, control, engines, egress | Durable job execution (downloads, benchmarks, media, audio, science, backups), idle sweeper, reconciliation |
| `monitor` | same image, `python -m app.monitor` | data, control | Host telemetry (5 s), downsampled history (1 min/1 h), alerts |
| `postgres` | postgres:16.13 | data (internal) | All durable state (29 tables, Alembic migrations) |
| `redis` | redis:8.6.8-alpine | data (internal) | Wake-ups, pub/sub progress, rate limits, caches. Not a source of truth |
| `engine-manager` | spark-ai-hub/engine-manager | control (internal) | Only Docker-socket holder; typed allow-listed operations |
| `ollama` | ollama/ollama:0.34.4 | engines (internal), egress | GGUF LLMs, always on (idle = no GPU memory) |
| `sah-vllm` | nvcr.io/nvidia/vllm:26.09-py3 (vLLM 0.29.0) | engines (internal, **no egress**) | Created on demand per model by engine-manager |
| `sah-comfyui` | spark-ai-hub/comfyui:0.42.5 (NGC PyTorch 26.09) | engines | Image/video workflows, on demand |
| `sah-mlworker` | spark-ai-hub/mlworker (NGC PyTorch 26.09) | engines | ASR/TTS and scientific adapters, on demand, uid 1000 |

Networks `data`, `control` and `engines` are Docker `internal: true` networks (no route to the Internet or LAN).

## Architectural decisions (ADR)

**ADR-001 Modular monolith backend.** One FastAPI codebase with routers per domain and a separate worker and monitor
process from the same image. Lower operational cost than microservices for a handful of users; engines remain isolated.

**ADR-002 Built-in OpenAI gateway instead of LiteLLM Proxy.** The gateway must (a) authenticate with the same user/RBAC
database, (b) load models on demand through the unified-memory scheduler before routing, and (c) never expose a master key.
LiteLLM would require its own key store and could not trigger scheduler-aware loads. The built-in gateway implements
`/v1/models`, `/v1/chat/completions` (incl. streaming, usage), `/v1/embeddings`, per-key rate limits, monthly quotas and
attribution. Verified with the official OpenAI Python SDK (TEST_REPORT §9–12).

**ADR-003 Engine manager instead of compose profiles for GPU engines.** GPU engines are created by `engine-manager`
from fixed specs (image, mounts, network, GPU request, no-new-privileges). Researchers cannot specify images, mounts or
commands. vLLM parameters are typed and validated (paths must be HF snapshot paths under `/models/hf`).

**ADR-004 One vLLM model at a time.** vLLM pre-allocates `gpu_memory_utilization × total memory`. A single `sah-vllm`
container keeps the memory model predictable; multiple small models are served by Ollama concurrently.

**ADR-005 ComfyUI headless.** Users never reach the ComfyUI UI (it has no authentication). Only registered,
parameter-validated workflow templates (`backend/app/services/workflows.py`) are submitted through the API.

**ADR-006 Whisper/VITS through transformers on NGC PyTorch.** faster-whisper/CTranslate2 has no CUDA aarch64 wheels;
the transformers pipeline on the NGC image uses the GB10 GPU (verified). Speaker diarisation (pyannote, gated) is not installed.

**ADR-007 Postgres is the source of truth.** Jobs, events, reservations, registry, benchmarks and usage are in Postgres;
Redis only accelerates. Worker restarts are reconciled from heartbeats (TEST_REPORT recovery test).

## Unified-memory scheduler

`backend/app/services/scheduler.py`

* Budget = `MemTotal − system_reserve_gb` (default 14 GB). Every resident model / running heavy job holds a logical
  reservation row. Decisions are taken under a Postgres advisory lock (API and worker processes never race).
* A reservation is granted only if (1) Σ reservations + request ≤ budget, (2) live `MemAvailable` ≥ request + 3 GB
  (detects memory used outside the hub), (3) GPU-heavy slots (`max_concurrent_gpu_jobs`, default 1) are free.
* When room is needed, idle (no active requests), non-pinned instances are unloaded, least recently used first. Busy
  models are never evicted; unrelated processes are never touched.
* Modes: **hybrid** (default: preferred resident model pinned, others unloaded after `model_idle_timeout_min`),
  **on_demand** (short idle timeout), **persistent** (never auto-unload).
* Estimates: weights (file sizes) + KV cache (`2·layers·kv_heads·head_dim·2B·ctx·seqs`) + 2.5 GB + 8 % overhead; vLLM
  reservation = `gpu_memory_utilization × MemTotal + 1 GB`. After loading, the measured footprint (Ollama `/api/ps`
  or MemAvailable delta) replaces the estimate (+10 %) for the next load.
* Interactive requests load on demand (with waiting); jobs wait in the queue with an explicit reason
  ("Waiting for resources: …") rather than launching into an OOM.

## Jobs

States: queued → preparing → running → completed | failed | cancelled | interrupted. Per-user concurrency and queue
limits (resource policies), class priorities (interactive < maintenance < batch < scientific < image < video),
cooperative cancellation, heartbeats every 10 s; jobs without heartbeat for 45 s are re-queued if idempotent
(downloads, benchmarks, media, …) or marked interrupted otherwise. Event logs, actual resource measurements and
artifacts are stored per job.

## Model lifecycle

discovered → downloading → downloaded → installing → installed ⇄ loading → ready/busy → unloading → installed;
failed / disabled / removing. Installation never loads a model; the post-install smoke test loads it, runs one real
inference and unloads it again if it was not resident before. History is append-only (`model_installations`).

## Data flow of an install

1. `POST /huggingface/plan` — metadata (huggingface_hub), formats, compatibility rules, memory/disk estimate, warnings.
2. `POST /huggingface/install` — registers the model (revision pinned to the commit SHA), reserves disk, creates a
   download task; researchers' requests wait for admin approval.
3. Worker `hf_download` — `snapshot_download` in a child process (cancellable/pausable/resumable), retries,
   **sha256 verification of every LFS file**, hard links for ComfyUI files, backend configuration.
4. Worker `smoke_test` — real inference with the target engine; result stored and shown in the UI.
