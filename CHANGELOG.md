# Changelog

## 0.10.2 — 2026-11-03
Publishes the 0.10.1 changes below: the v0.10.1 tag was created by mistake on the 0.10.0 code. No other changes.

## 0.10.1 — 2026-11-03
Follows Tailscale changes by itself; OpenClaw and Hermes in Connect tools.

* **Automatic Tailscale sync** (`scripts/tailnet-sync.py`, a timer installed by `sah.sh tailnet-install`): when the
  account, tailnet name or IP changes, the hub's address (read live, no restart), `.env`, Tailscale Serve, Sunshine's
  `bind_address` and the Android app follow, and managers are notified. Custom addresses are left alone.
  `scripts/tailnet-root-setup.sh` (once, with sudo) keeps the permission Serve needs across account changes.
* **Hub address card** (Settings → Access and mail): address, account, IP, whether it is reachable, and step-by-step
  fixes for what needs a person (enable HTTPS on the tailnet, operator permission, disable key expiry).
* **Connect tools**: new **OpenClaw** and **Hermes Agent** guides; the API key is now written directly in everything you
  copy (Aider, Python and LangChain used environment variables).

## 0.10.0 — 2026-11-02
Tasks that run until they are done, a live activity panel, messages added to a running task, reliable web reading.

* **Runs until done**: the fixed step caps are gone (the Workspace stopped at 30 steps — all recent tasks with the new
  model ended there — and the chat at 10). The assistant plans with a new `update_plan` tool, keeps the plan updated
  and is nudged to continue while items are pending, files were written but not run, or it only announced work.
  Safety net: `agent_max_steps_*` / `agent_max_minutes_*` settings (Workspace 400 steps / 4 h, chat 40 / 30 min);
  at the limit it summarises what is done and what is left.
* **"Think" no longer leaves answers half-done**: reasoning and answer shared `max_tokens`, so a long reasoning ended
  with `finish=length` and no text, which was "continued" without its reasoning. Thinking steps now get ≥16k tokens;
  a reasoning-only cut retries with a bigger budget (then without thinking).
* **Automatic context compression inside a task**: when a long task approaches 70 % of the window, older steps are
  replaced by a model-written summary (files, results, open problems) and the plan; "context length exceeded"
  errors compress and retry instead of failing.
* **Add to a running task**: sending a message while the assistant works no longer cancels or is refused: it is
  queued and injected into the running task at the next step (`202 {queued:true}`); if the task ends first, a
  follow-up answer starts by itself.
* **Live activity panel** (chat and Workspace): plan checklist with progress, streaming reasoning, a timeline of every
  step (icon, arguments, live timer, ✓/✗, duration, terminal output, changed files, sub-agent reports, sources),
  context-usage bar and compression notices; collapses into a one-line summary and is rebuilt after a reload.
* **Web reading**: useful text from 54 typical research URLs went from 39 % to ~79 %: browser and honest-bot header
  profiles (Wikipedia blocks fake browsers), cookies, retries with backoff and `Retry-After`, anti-bot page detection,
  scholarly fallbacks (Europe PMC / OpenAlex / Crossref by DOI, PMID, PII) and the Internet Archive, with the source
  noted; fixed extraction of front pages with many `<article>` elements, header charsets and concurrent PDFs. SearXNG:
  news search works again (Bing News, DuckDuckGo News, Wikinews), Bing added to web, Europe PMC to science; its
  settings are regenerated from the template (the installed file had lost its `server` section).
* **MCP**: `GET /mcp` opens an authenticated SSE stream instead of answering 405 (OpenCode showed it as an error).
* **Skills and agents are shared**: new skills and custom agents are public by default and existing ones were made
  public; everyone can use them, only the owner or an admin/manager edits them ("Compartida por …").
* **Connect → OpenCode**: pick the model, `small_model` is always the same model (no second light model slowing the
  main one), and the API key is written into the file (create one in place); per-OS instructions.

## 0.9.1 — 2026-11-02
Sub-agents that work in the Workspace, and a catalogue of built-in agents and skills.

* **Multi-agent Workspace**: sub-agents can now list, read, write and run files of the workspace (`ws_*` tools).
  `delegate_task` takes `write_paths` (files/folders the agent may change): agents running in parallel must get
  non-overlapping paths (a second agent on the same area is refused), writes outside the area are rejected, and
  agents wait for a free run slot instead of failing. Coding agents get more steps (24) and longer outputs. The
  workspace assistant is told to plan, split separable work between parallel agents, then integrate and test; the
  files each agent changed appear as links and its report can be opened in the chat.
* **15 new built-in agents**: programador, tester, depurador, revisor-codigo, arquitecto, redactor-cientifico,
  revisor-bibliografia, revisor-pares, estadistico, visualizador, limpiador-datos, maquetador-latex, traductor,
  verificador and docente (several preload their skill).
* **17 built-in skills** shipped with the hub and installed at start-up (public, "Built-in" badge, can be disabled
  and restored): informe-latex, presentacion-beamer, citas-bibtex, excel-con-graficos, documento-word,
  presentacion-pptx, graficos-publicacion, analisis-estadistico, limpieza-datos-pandas, informe-desde-datos,
  articulo-cientifico-imrad, revision-por-pares, propuesta-investigacion, tfg-tfm, docencia-rubricas,
  traduccion-tecnica and python-tests. Every code and LaTeX template was run in the sandbox image.
* **Sandbox image**: adds `pytest`, the journal LaTeX classes (`texlive-publishers`: IEEEtran, elsarticle, llncs,
  acmart…) and the Carlito/Caladea fonts (metric-compatible with Calibri/Cambria).

## 0.9.0 — 2026-11-02
New main model, a simple way to choose it, message actions in the chat and a redesigned Workspace.

* **Qwen3.8-Flash-Next FP4 as the general model** (`nvidia/Qwen3.8-Flash-Next-FP4-Local`: 125B MoE, 6B active,
  vision and tools, NVIDIA's quantization-aware FP4 for local hardware). It needs a community DGX Spark vLLM build
  with the b12x kernels: the engine manager now has fixed **launch profiles** (image pinned by digest, environment and
  flags allow-listed; nothing comes from the caller). The 51B-parameter n-gram table is read from disk with io_uring,
  allowed by a seccomp profile that is Docker's default plus the three io_uring calls (not `seccomp=unconfined`).
  Measured on the Spark: 70.3 GiB resident, FP8 KV cache for 625k tokens, 256k context, MTP (4 draft tokens),
  ~45 tok/s. The first start autotunes the kernels (~45 min, cached afterwards in `cache/vllm/b12x`).
* **Longer windows with YaRN**: Qwen models can go beyond their native window (up to 4×, 1M at most); the context card
  marks those sizes "YaRN" and explains the trade-off (better for long texts, slightly worse on short chats).
* **Main model** card on the Models page: current model, state, memory and context; pick another one among the
  downloaded models, the recommended ones or any Hugging Face repository ("Download and make main" switches by itself
  when the download ends, with progress, speed and ETA). New API: `GET/POST /api/v1/models/main`,
  `GET /api/v1/models/main/plan`, `DELETE /api/v1/models/main/pending`.
* **Load / Unload buttons** on every model (library and detail page) with live state, ETA and memory, and the reason
  when an action is not possible.
* Model loads: a profile can allow a long first start (2 h); a load in progress survives a worker restart (the hub
  waits for the running container instead of restarting it).
* **Chat message actions**: copy any message, copy code blocks, edit a sent message and regenerate, regenerate the
  last answer, reply quoting a selected piece of an answer, and "resume from here" (deletes what follows). Same actions
  in the Workspace assistant. New API: `POST …/messages/{id}/regenerate`, `POST …/messages/{id}/truncate`; the
  context summary is invalidated when it covered removed messages. Your own messages are selectable again (the
  selection colour was the bubble colour).
* **Workspace redesigned**: workspace switcher (rename, sizes, last used), resizable and collapsible panels, file
  tree with icons, context menu, drag & drop to folders and move, editor tabs with unsaved marks and Ctrl+S, conflict
  warning when a file changes on the server, and a **multi-line terminal** (Enter runs, Shift+Enter new line, Tab
  indents, history) with run history, exit codes, duration and a **Stop** button for background runs. Mobile: panels
  become tabs.

## 0.8.1 — 2026-11-01
Connectors verified against real servers; voice fixes.

* **WebDAV connector** now reads every `propstat` block of a PROPFIND answer (servers such as WsgiDAV and Nextcloud
  put found and missing properties in separate blocks): sub-folders were not followed and some files had no ETag.
  Verified against a real WebDAV server: new files, sub-folders, changed files and deleted files.
* **SMB connector**: each connector uses its own connection cache (the shared pool reused another connector's session
  and credentials for the same server: "access denied"); guest shares work (NTLM without signing); clear messages for
  wrong passwords, missing shares and folders. Verified against Samba: guest, user/password and wrong password.
* The sync statistics count deletions correctly.
* **ML worker**: `transformers` is imported at start-up; two simultaneous requests (e.g. a smoke test and a
  read-aloud) could fail with "cannot import name AutoTokenizer".
* English read-aloud: install `facebook/mms-tts-eng` (the chat picks the voice matching the user's language).
* Admin PDF guide: background replies, workspace packages and LaTeX, backups of users' files.

## 0.8.0 — 2026-11-01
Deep review: fixes and hardening across the platform.

* **Backups were missing user files**: "include user data" archived datasets, outputs and experiments but not the
  users' drives (`users/`, since 0.2.0) nor workspaces. Both are included now (without `node_modules`/installed
  packages); symbolic links and special files are skipped. Verified with a real backup.
* **Answers cut by the length limit** (`finish_reason=length`) now continue automatically (twice at most) instead of
  ending mid-sentence; the chat's default output limit is 8192 tokens.
* **Small models** (< 14B, e.g. Qwen3 4B) get only the core tools and a shorter prompt: with 20+ tools they stopped
  calling the right one (memory regression found by the acceptance tests). «Recuerda que…» is always saved, even if
  the model forgets to call the tool.
* **Workspaces**: deleting a workspace stops its running reply and deletes its chat (a running reply could recreate a
  deleted workspace); workspace chats no longer clutter the Chat sidebar; uploads are streamed to disk instead of read
  into memory; change detection skips `node_modules`/`.local` (fast after `npm install`).
* **Fair sandbox use**: at most 2 programs per person at once (chat + workspaces, setting `code_exec_per_user`); the
  slot frees itself if a process dies. The engine manager removes sandbox containers left by a restart.
* Replies longer than 2 h keep their state alive through the heartbeat.
* **Version**: the app reports its real release (`/api/v1/system/health`, MCP, backups) instead of the Docker image tag.
* `scripts/sah.sh up|update` create the runtime folders before Compose mounts them and `update` also rebuilds the
  sandbox image.
* Tests: acceptance tests can run with an existing researcher account (`SAH_TEST_USER`, `SAH_TEST_USER_PASSWORD`),
  no admin password needed; 27 researcher-level acceptance tests and 36 unit tests pass.

## 0.7.0 — 2026-11-01
Reliable background work, LaTeX and package installs in workspaces, skills in workspaces.

* **No more "zombie" chats**: every running reply keeps a heartbeat in Redis while its thread lives; a reply whose
  process died (backend restart, crash) is closed as interrupted with a message, instead of showing "working" for two
  hours. Deleting a conversation stops its reply.
* **"N chats working" menu**: lists every running reply with its title, elapsed time and current step, opens it (in the
  Chat or in its Workspace) and has a **Stop** button for each.
* **LaTeX in workspaces**: the sandbox image now includes TeX Live (pdflatex, xelatex, lualatex, latexmk, biber, most
  LaTeX packages, Spanish and English).
* **Install missing packages**: in workspaces `pip install` / `npm install` work and persist in the workspace
  (`.local`, `node_modules`). The sandbox still has no network interface: a loopback forwarder talks to the new
  `pkgproxy` service through a Unix socket, and the proxy only allows PyPI and npm over HTTPS (admin switch).
* **Skills in workspaces**: loading a skill copies its files to `.skills/<name>/` so its scripts can run with `ws_run`.
* Workspace assistant: long files are written in parts (`ws_append`); a tool call cut by the output limit is reported
  to the model instead of running with empty arguments; images generated in a workspace chat are saved in `imagenes/`
  so documents can include them; up to 16k output tokens per step.
* The Workspace opens the right workspace from links (`/workspace?w=<id>`).

## 0.6.1 — 2026-11-01
Work keeps going — and stays visible — when you leave the page.

* Workspace assistant: coming back to the Workspace (another page, window or a reload) re-attaches to a reply in
  progress and replays its steps live, as the Chat already did.
* Workspace terminal: commands run in the background on the server (`POST /run` with `background: true`); the last 30
  runs per workspace are kept for 7 days (`GET/DELETE /runs`), so any window shows what is running and how it ended.
  Up to 3 commands at once per workspace; a button clears the terminal.
* Audio Studio lists recent transcription/speech jobs from the server instead of the page memory.

## 0.6.0 — 2026-11-01
Workspace: a remote harness per user.

* **Workspace** page: persistent folders per user (several per person, ZIP download, 10 GB each by default) with a
  file tree (upload, drag & drop, import from My files, new file/folder, rename, delete), an editor (Ctrl+S, ▶ to run
  `.py`/`.sh`/`.js`), previews (Excel/CSV as tables with sheets, PDF, images, HTML in a sandboxed frame, Markdown) and a
  terminal. Every command runs in the no-network sandbox with the workspace mounted as `/work` (engine-manager
  `workspace` mount; the program itself comes read-only from `/job`); up to 10 min per command by default.
* **Workspace assistant**: its own chat per workspace with `ws_list`, `ws_read`, `ws_write`, `ws_edit` and `ws_run`; it
  writes code, runs it, reads the errors and fixes them (up to 30 tool steps) and leaves Excel, PDF, charts or scripts in
  the workspace. Verified: CSV → Excel with detail and monthly sheets + chart + one-page PDF report.
* Untrusted files written by programs are handled safely: symbolic links are never followed from the web, only regular
  files are read (no FIFOs/devices), paths cannot leave the workspace.
* Settings: `workspace_timeout_s`, `workspace_max_gb`. Migration 38ed2f96b523.
* OpenCode configuration from "Connect tools" adds `headerTimeout` (30 min) so the first request to a model that is not
  in memory waits for it to load, labels each model with its context and marks the general one.
* Chat: the "Think" switch is remembered (per browser and per conversation).

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
