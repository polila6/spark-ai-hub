# Changelog

## 0.5.0 — 2026-11-01
Code, canvas, deep research, voice, scheduled tasks, connectors, single sign-on and a usage panel.

* **Faster answers (MTP)**: vLLM speculative decoding with the model's own multi-token-prediction layers
  (`speculative_tokens`, switch on the model page; set automatically when installing Qwen3.5/Qwen3-Next). General model:
  50 → 63 tok/s for one user, 117 → 129 tok/s for four (2 draft tokens; 3 gave no further gain on GB10).
* **Code execution**: `run_code` tool (Python, bash, Node) in a fresh sandbox container per run — no network,
  read-only root, uid 1000, no capabilities, memory/CPU/pids/time limits (engine-manager `/sandbox/run`, image
  `engines/sandbox`: numpy/pandas/scipy/scikit-learn/matplotlib, python-docx/pptx/openpyxl/reportlab, Node docx and
  pptxgenjs, pandoc, LibreOffice). Files written to `/work` go to My files → "Resultados de código" and show in the chat
  (images inline). Skills can now run their scripts (`run_code(skills=[…])`). Admin switch and limits.
* **Canvas**: `canvas_write` / `canvas_edit` open a document or code file beside the chat; the user edits it, restores
  versions (last 30), asks for changes and exports to Word, PDF, Markdown, HTML or the source file (pandoc +
  LibreOffice in the sandbox), or saves a copy to My files.
* **Deep research**: "Deep research" switch (or `deep_research` tool): plan → 3–7 researcher agents in parallel (plus
  the user's documents on request) → long report with numbered sources in the canvas. Verified: 7 agents, 29 sources,
  ~6 min.
* **Voice**: dictation (🎤, local Whisper) and read-aloud ("Listen", "Read answers"; local MMS-TTS voices per language)
  through the ML worker; audio never leaves the Spark. Android app already grants the microphone.
* **Scheduled tasks**: Tasks page and `schedule_task` tool ("every Monday at 9 …"): hourly/daily (weekdays)/weekly/
  monthly in the user's time zone, web/deep-research options, own chat per task, bell notification and optional e-mail
  with the answer, "Run now". Runs from the worker (2 at a time); tokens counted as source `task`.
* **Knowledge connectors**: Nextcloud/ownCloud/WebDAV, GitHub (public or token), Zotero (user/group libraries) and SMB
  network shares, synced hourly/daily/manually into a collection (new, changed and deleted files). Remote addresses must
  be public unless an admin lists the private host/network (`connector_private_hosts`); loopback, link-local and the
  hub's containers are always refused; connections pinned to the checked IP; secrets encrypted.
* **Sign in with Google, Microsoft or any OpenID Connect provider**: PKCE, state bound to the browser, nonce,
  iss/aud/exp checks; accounts linked by e-mail only when the provider vouches for it; new accounts follow the sign-up
  mode (approval/open/closed) and allowed domains. Step-by-step setup with the redirect address in Settings → Access.
* **Usage panel**: tokens per day, per model, per source (chat/API/tasks) and per project, GPU/processing jobs, storage
  and monthly quota; managers see everyone (numbers only).
* Database migration 7982a336c53a (canvas_docs, scheduled_tasks, kb_connectors, connector_items, user_identities).
* New dependencies: smbprotocol 1.17.0, pyspnego 0.12.4 (backend); sandbox image built by `install.sh` /
  `scripts/sah.sh build`.

## 0.4.0 — 2026-11-01
Bigger context for coding agents, one-command install and first public release.

* Context window per model: new "Context window" card on a vLLM model's page with presets (8k … the model's trained
  maximum, read from `config.json`). Each preset shows how many conversations of that size fit at once, computed from
  the KV cache vLLM actually reserved (`/metrics` `kv_cache_size_tokens`). "Apply" stores `max_model_len` and reloads
  the model in the background. API: `GET/POST /api/v1/models/{id}/context`.
* The recommended general model (Qwen3.5-35B-A3B-FP8) now serves **256k tokens** with the same memory as before (hybrid
  attention: only 1 in 4 layers keeps a KV cache); verified with a 167k-token prompt. OpenCode, Cline and Claude Code
  configurations in "Connect tools" pick the new window up automatically; OpenCode steps explain `/compact`.
* Installing a model from Hugging Face sets vLLM tool-calling and reasoning parsers for known families (Qwen3.5 /
  Qwen3-Coder → `qwen3_coder`, Qwen3/Qwen2.5 → `hermes`, Llama 3 → `llama3_json`, Mistral → `mistral`, gpt-oss →
  `openai`), so tools work right after install.
* `install.sh`: requirement checks (architecture, Docker, NVIDIA toolkit, memory, disk), `.env` generation (address from
  Tailscale), host preparation, secrets, image build and start-up, printed next steps. Tailscale is optional.
* Instance-neutral code: the address comes from `.env` / the browser, the organisation name is a setting
  (`SAH_ORG_NAME`, used in the terms, e-mails and the assistant prompt), the Android app gets its default address at
  build time, no hard-coded hostnames, paths or e-mail servers.
* README with screenshots, Spanish README, MIT licence.

## 0.3.0 — 2026-10-30
Web search, skills, sub-agents and context management in the web chat.

* Web search: private SearXNG container (`searxng`, egress network only, no published port) with engines tested from
  a university network (Google CSE, Yahoo, Yandex, Wikipedia; arXiv/Scholar/Semantic Scholar/OpenAlex/Crossref/PubMed for
  science; GitHub/Stack Overflow/MDN for code). Chat tools `web_search` and `fetch_url` (HTML → readable text, PDFs via
  pypdfium2). Fetching is SSRF-safe: every hop is resolved and must be a global address (no private, tailnet, Docker,
  loopback or link-local), ports 80/443/8080/8443 only, connection pinned to the checked IP, 6 MB limit. Per-chat
  "Web" switch; admin switch.
* Skills (Agent Skills layout, SKILL.md + text files): catalog from GitHub sources (default `anthropics/skills`),
  install from owner/repo/path, GitHub URL or SKILL.md link, write your own, enable/disable, update, publish for everyone
  (admin/manager). The chat lists installed skills and loads them with `use_skill`/`read_skill_file`; it can find
  (`find_skills`) and install (`install_skill`) skills by itself when the admin allows it. Skills are text only: scripts
  are never executed.
* Sub-agents: `delegate_task` runs built-in (investigador, documentalista, analista, revisor, general) or custom agents
  with their own context and tool subset; several calls in one step run in parallel (admin limit, default 3). Live steps
  and the agent report in the chat; Agents page to create/edit custom agents.
* Context window: per-chat window size (4k … model maximum), live meter in the chat header, automatic compaction (the
  model summarises older messages when the history exceeds half of the free window; summary stored on the conversation,
  messages kept), "Compact now", view/forget summary. Requests are fitted to the window (old tool results trimmed,
  max_tokens clamped, chars/token calibrated from the engine's usage, one stricter retry on context-length errors).
* Tool loop: up to 10 steps; tool results of the current turn feed the window estimate.

## 0.2.1 — 2026-10-30
Memory policy and background chats.

* General model always loaded for everyone: `qwen3.5-35b-a3b-fp8` (vision, tools `qwen3_coder`, 64k context,
  8 concurrent sequences, ≈ 53 GB reserved; 50.8 tok/s single stream, 109 tok/s with 4 users). Pinned, preferred
  resident, default for new chats and for Claude Code / OpenCode; the worker reloads it automatically if it is missing.
* vLLM runs one container per model (`sah-vllm-<alias>`): the general model and extra vLLM models can coexist;
  start-ups are serialised. Extra models unload after 5 idle minutes (hybrid mode).
* Memory panel (dashboard, library, monitoring and a bar in the header): reservations by kind, loading progress with
  ETA from the last load, idle countdown to auto-unload, running image/video jobs, unload buttons. Loading a model shows
  a plan first (memory needed, idle models that would be unloaded, ETA) — in the library and when sending a chat message.
* KV-cache estimate counts only full-attention layers for hybrid models (Qwen3.5/Qwen3-Next).
* Chat answers run in the background: leaving the page no longer stops them. Events are kept in Redis; reopening the
  chat replays and follows them live; header indicator "N chats working", completion toast, browser notification when
  the tab is hidden and a bell notice for answers longer than 30 s. Stop really cancels; a second message while one is
  running is refused.

## 0.2.0 — 2026-10-30
Personal private cloud, harness access and Android app.

* Accounts: self sign-up with approval (any domain by default, optional allow-list), e-mail verification, password
  recovery, terms of use with versioning, **manager** role (users, sign-ups, model requests; no system settings),
  invitation e-mails with Tailscale steps (SMTP outbox with retries), optional Tailscale API
  device invites, in-app notifications (bell) with e-mail copies, data export (ZIP) and account deletion.
* Personal cloud: My files (typed uploads, magic-byte checks, text extraction for PDF/Word/PowerPoint/Excel/HTML/code),
  knowledge base (pgvector + full-text hybrid search, Qwen3-Embedding-0.6B), projects (instructions, documents, chats,
  read/write sharing), memory and personal instructions, full-text search over chats.
* Chat agent: attachments (images to vision models, documents inline or via read_document, scanned PDFs as images,
  data files), tools generate_image / edit_image (Qwen-Image + Qwen-Image-Edit-2509 with Lightning LoRAs),
  search_knowledge, describe_data / query_data (DuckDB/SQLite sandboxed subprocess), save_memory; image mode; thinking
  switch; streamed tool progress.
* Harness: Anthropic Messages API (/v1/messages, streaming, tools; Claude Code), MCP server (/mcp, streamable HTTP,
  12 tools), "Connect tools" page generating per-tool/per-OS setup with the user's key (OpenCode, Claude Code, Continue,
  Cline, Cursor, Claude Desktop, Aider, Python, LangChain, Open WebUI, curl).
* In-app guides on every page, help centre, onboarding; mobile layout (bottom tab bar by role, chat drawer).
* Android app (es.upv.idf.sparkhub 0.2.0): WebView shell with Tailscale detection/launch, camera and file pickers,
  share-to-chat, downloads; built on the Spark without Gradle (android/build.sh); served at /api/v1/app/android.
* PDF guides for users and administrators with real screenshots (docs/guides/).
* Database: postgres image → pgvector/pgvector:0.8.7-pg16-trixie (PostgreSQL 16.15); migration cda914892726.

### Coding agents (OpenCode) over the gateway

* Fix: Ollama truncated prompts longer than ~4k tokens (8192 context shared by the request) — now
  `OLLAMA_CONTEXT_LENGTH=32768` with flash attention and q8_0 KV cache (Qwen3 4B: 13.5 GB resident).
* Fix: `tools` on vLLM models is accepted only when the instance was started with a tool-call parser (was gated on
  the HF capability list, which never included `tools`).
* Fix: Ollama reservations follow the size reported by `/api/ps` (periodic `sync_ollama`), and a post-load
  measurement can raise a reservation as well as lower it.
* Models: Qwen3.5-35B-A3B (BF16, vLLM, 64k context, `qwen3_coder` tools, ≈90 GB, 30 tok/s); Qwen3-8B-FP8 now 32k context
  with `hermes` tools. Verified tool calls with a 12.5k-token prompt on all three chat models.
* Docs: OpenCode provider configuration (API.md).

## 0.1.0 — 2026-10-29
Initial deployment on a DGX Spark (DGX OS 7.5.0, driver 580.142, CUDA 13.0).

* Core: FastAPI backend (modular monolith), PostgreSQL 16, Redis 8, Next.js 15 frontend, Caddy reverse proxy,
  Tailscale Serve (tailnet-only HTTPS). Cookie sessions + CSRF, Argon2id, RBAC (admin/researcher/viewer), invitations.
* Engines: Ollama 0.34.4 (always on), vLLM (NGC 26.09, on demand), ComfyUI v0.42.5 and ML worker on NGC PyTorch 26.09
  (on demand), all managed by an allow-listed engine manager.
* Hugging Face explorer, deterministic compatibility/recommendation engine, download manager with sha256 verification,
  model registry and lifecycle, conservative unified-memory scheduler (hybrid/on-demand/persistent).
* OpenAI-compatible gateway (/v1 models, chat completions with streaming, embeddings), per-user API keys, quotas,
  rate limits, privacy-aware usage logging.
* Playground, model comparison, benchmarks (perf + deterministic quality suites), Image/Video/Audio studios,
  Scientific Lab (datasets, trusted adapters, experiments), jobs with recovery, monitoring + alerts, backups + restore test.
* Fixes found by acceptance testing: periodic job reconciliation after fast worker restarts, HF download retries with
  partial-snapshot detection, benchmark environment GPU identity, webp MIME type, grid overflow, a11y heading.
